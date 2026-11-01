"""Committed manual sources survive new native PostgreSQL connections; no AI."""
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, AGENT, PROFILE
import tenant_ai_agents_store as agents
from tenant_linear_policy import TenantLinearError


@pytest.mark.parametrize('expert',[False,True])
def test_manual_source_commit_readback_and_reload_preserve_audience(db,monkeypatch,expert):
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,role) VALUES(%s::uuid,%s::uuid,'Expert',11,'expert')",(A,AGENT));db.commit()
    data={'title':'Synthetic manual source','kind':'text','content':'Synthetic verified instruction','active':True}
    if expert:data['agent_id']=AGENT
    result=agents.save_knowledge(PROFILE,data)
    assert result['id'] and result['agent_id']==(AGENT if expert else None)
    # Every read opens a new psycopg connection to this disposable test database.
    monkeypatch.setattr(agents,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    listed=agents.list_knowledge(PROFILE)
    assert len(listed)==1 and listed[0]['id']==result['id'] and listed[0]['ai_available'] is True
    loaded=agents.list_knowledge(PROFILE,result['id'])
    assert loaded['content']==data['content'] and loaded['agent_id']==(AGENT if expert else None)
    assert agents.list_knowledge({'tenant_id':B,'telegram_id':22})==[]
    with pytest.raises(TenantLinearError):agents.list_knowledge({'tenant_id':B,'telegram_id':22},result['id'])
    with pytest.raises(TenantLinearError):agents.save_knowledge(PROFILE,{**data,'content':''})
    assert len(agents.list_knowledge(PROFILE))==1


@pytest.mark.parametrize('expert',[False,True])
def test_manual_request_survives_lost_response_and_process_connection_restart(db,monkeypatch,expert):
    import uuid
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,role) VALUES(%s::uuid,%s::uuid,'Expert',11,'expert')",(A,AGENT));db.commit()
    monkeypatch.setattr(agents,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    request=str(uuid.uuid4())
    data={'create_request_id':request,'title':'Same visible title','kind':'text','content':'Exact committed operation','active':True}
    if expert:data['agent_id']=AGENT
    first=agents.save_knowledge(PROFILE,data) # Simulate discarding this HTTP response.
    assert first['id']==first['create_request_id']==request
    loaded=agents.list_knowledge(PROFILE,request,create_request_id=request)
    assert loaded['create_request_id']==request and str(loaded['created_by'])=='11'
    assert loaded['agent_id']==(AGENT if expert else None)
    assert 'manual_create_request' not in loaded['provenance']
    retry=agents.save_knowledge(PROFILE,dict(reversed(list(data.items()))))
    assert retry['id']==request
    assert len(agents.list_knowledge(PROFILE))==1
    for change in ({'content':'Different operation'},{'title':'Different title'},{'active':False},
                   {'agent_id':None if expert else AGENT}):
        with pytest.raises(TenantLinearError) as error:agents.save_knowledge(PROFILE,{**data,**change})
        assert error.value.status==409
    # Identical titles are not recovery identities; a deliberately new request is distinct.
    another=agents.save_knowledge(PROFILE,{**data,'create_request_id':str(uuid.uuid4()),'content':'A new edited operation'})
    assert another['id']!=request and len(agents.list_knowledge(PROFILE))==2
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM saas_ai_knowledge_chunks WHERE source_id=%s::uuid",(request,)).fetchone()['n']==1


def test_manual_request_fences_tenant_actor_and_legacy_sources(db,monkeypatch):
    import uuid
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,33,'owner','Fixture')",(A,));db.commit()
    monkeypatch.setattr(agents,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    data={'create_request_id':str(uuid.uuid4()),'title':'Fixture','kind':'text','content':'Exact operation','active':True}
    source=agents.save_knowledge(PROFILE,data)
    for profile in ({'tenant_id':B,'telegram_id':22},{'tenant_id':A,'telegram_id':33}):
        with pytest.raises(TenantLinearError) as error:agents.save_knowledge(profile,data)
        assert error.value.status==409
        with pytest.raises(TenantLinearError) as error:agents.list_knowledge(profile,source['id'],create_request_id=source['id'])
        assert error.value.status==404
    legacy=agents.save_knowledge(PROFILE,{k:v for k,v in data.items() if k!='create_request_id'})
    with pytest.raises(TenantLinearError) as error:agents.save_knowledge(PROFILE,{**data,'create_request_id':legacy['id']})
    assert error.value.status==409
    with pytest.raises(TenantLinearError) as error:agents.list_knowledge(PROFILE,legacy['id'],create_request_id=legacy['id'])
    assert error.value.status==404
    with pytest.raises(TenantLinearError):agents.list_knowledge(PROFILE,None,create_request_id=source['id'])
    with pytest.raises(TenantLinearError):agents.list_knowledge(PROFILE,legacy['id'],create_request_id=source['id'])
    assert agents.list_knowledge({'tenant_id':A,'telegram_id':33},source['id'])['id']==source['id'],'ordinary authorized company read is unchanged'
    with pytest.raises(TenantLinearError):agents.save_knowledge(PROFILE,{'create_request_id':source['id'],'content':'Edit'},source_id=source['id'])
    with pytest.raises(TenantLinearError):agents.save_knowledge(PROFILE,{**data,'setup_key':'manual_fixture_key'})
    assert len(agents.list_knowledge(PROFILE))==2 and agents.list_knowledge({'tenant_id':B,'telegram_id':22})==[]


@pytest.mark.parametrize('different_payload',[False,True])
def test_concurrent_manual_request_has_one_source_and_one_chunk_set(db,monkeypatch,different_payload):
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    monkeypatch.setattr(agents,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    data={'create_request_id':str(uuid.uuid4()),'title':'Concurrent','kind':'text','content':'One atomic creation','active':True}
    barrier=Barrier(2)
    def create(index):
        barrier.wait(timeout=10)
        try:return agents.save_knowledge(PROFILE,{**data,**({'content':'Conflicting concurrent payload'} if different_payload and index else {})})
        except TenantLinearError as error:return {'status':error.status}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,range(2)))
    assert {row['id'] for row in results if row.get('id')}=={data['create_request_id']}
    assert [row['status'] for row in results if 'status' in row]==([409] if different_payload else [])
    assert len(agents.list_knowledge(PROFILE))==1
    with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
        assert conn.execute('SELECT count(*) AS n FROM saas_ai_knowledge_chunks').fetchone()['n']==1
