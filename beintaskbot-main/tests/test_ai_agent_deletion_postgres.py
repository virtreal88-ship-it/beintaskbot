"""Real PostgreSQL checks for exact agent deletion, preserved history and assignment races."""
import concurrent.futures
import threading
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
import tenant_ai_agents_store as agents
import tenant_operator_assignment as assignment
from tenant_linear_policy import TenantLinearError


def _seed(db):
    tenant, channel, agent = (str(uuid.uuid4()) for _ in range(3))
    owner, lead = 990000001, int(uuid.uuid4().int % 2_000_000_000)
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Delete race','active',%s)",
               (tenant,'delete-'+tenant[:8],owner))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,%s,'owner','Owner')",(tenant,owner))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected')",(tenant,))
    db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled) VALUES(%s::uuid,TRUE)",(tenant,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,active) VALUES(%s::uuid,%s::uuid,'Delete test',TRUE)",(tenant,channel))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'customer',%s,'Customer',now())",(tenant,channel,lead))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,%s,'delete-source','connect:'||%s,'incoming','message')",(str(uuid.uuid4()),tenant,lead,channel))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,mode,scopes) VALUES(%s::uuid,%s::uuid,'Delete target',%s,TRUE,'manual',%s::jsonb)",
               (tenant,agent,owner,'[{"provider":"connect","channel_id":"'+channel+'","allow_groups":false}]'))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason) VALUES(%s::uuid,'connect',%s,'customer','paused','owner_review')",(tenant,channel))
    db.commit()
    return {'tenant_id':tenant,'telegram_id':owner},tenant,channel,agent,lead


def _connect_factory(dsn):
    return lambda: psycopg.connect(dsn,row_factory=dict_row)


def test_real_postgres_delete_preserves_runs_and_shared_knowledge(database,monkeypatch):
    db=database
    profile,tenant,_channel,agent,_lead=_seed(db)
    dsn=db.dsn
    monkeypatch.setattr(agents,'_connect',_connect_factory(dsn))
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=%s::uuid WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id='customer'",(agent,tenant))
    db.commit()
    run_id=str(uuid.uuid4());source_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s::uuid,'history-event','connect')",(tenant,))
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,result) VALUES(%s::uuid,%s::uuid,%s::uuid,'history-event',1,'fixture','completed','{\"kept\":true}'::jsonb)",(tenant,run_id,agent))
    db.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,active,content_hash,created_by,agent_id) VALUES(%s::uuid,%s::uuid,'Shared','text','preserve me',TRUE,'digest',%s,%s::uuid)",(source_id,tenant,profile['telegram_id'],agent))
    db.commit()
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        version=conn.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,agent)).fetchone()['version']
    preview=agents.preview_agent_deletion(profile,agent)
    assert preview['impact']['runs']==1 and preview['impact']['knowledge_sources']==1
    result=agents.permanently_delete_agent(profile,agent,expected_version=version,confirm_agent_id=agent)
    assert result['deleted'] and result['paused_dialogs']==1
    assert db.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,agent))==[]
    assert db.execute('SELECT result FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,run_id))[0]['result']=={'kept':True}
    assert db.execute('SELECT content FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,source_id))[0]['content']=='preserve me'
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id='customer'",(tenant,))[0]
    assert state['agent_id'] is None and state['state']=='paused' and state['reason']=='agent_permanently_deleted'
    db.commit()


def test_real_postgres_assignment_race_cannot_leave_deleted_agent_assigned(database,monkeypatch):
    db=database
    profile,tenant,channel,agent,lead=_seed(db)
    dsn=db.dsn;factory=_connect_factory(dsn)
    monkeypatch.setattr(agents,'_connect',factory)
    monkeypatch.setattr(assignment,'_connect',factory)
    monkeypatch.setattr(agents,'runtime_flag',lambda name: name=='AI_AGENTS_ENABLED')
    data={'provider':'connect','channel_id':channel,'conversation_id':'customer','lead_id':lead}
    options=assignment.options(profile,{**data,'manage':'1'})
    assert options.get('allowed') is True,options
    assert any(row['id']==agent for row in options.get('agents',[])),options
    assert db.execute("SELECT agent_id FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='customer'",(tenant,channel))[0]['agent_id'] is None
    db.commit()
    version=agents.preview_agent_deletion(profile,agent)['version']
    barrier=threading.Barrier(2)
    def delete():
        barrier.wait(10)
        try:return agents.permanently_delete_agent(profile,agent,expected_version=version,confirm_agent_id=agent)
        except Exception as error:return error
    def assign():
        barrier.wait(10)
        try:return assignment.assign(profile,{**data,'agent_id':agent,'request_id':str(uuid.uuid4()),'expected_revision':options['revision']})
        except Exception as error:return error
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        deleted=pool.submit(delete);assigned=pool.submit(assign)
        delete_result,assign_result=deleted.result(timeout=30),assigned.result(timeout=30)
    assert isinstance(delete_result,dict) and delete_result['deleted'],delete_result
    assert isinstance(assign_result,dict) or isinstance(assign_result,TenantLinearError) or isinstance(assign_result,psycopg.Error),assign_result
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        assert conn.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,agent)).fetchone() is None
        state=conn.execute("SELECT agent_id,state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id='customer'",(tenant,)).fetchone()
        assert state['agent_id'] is None
        assert state['state'] in {'paused','human'}
