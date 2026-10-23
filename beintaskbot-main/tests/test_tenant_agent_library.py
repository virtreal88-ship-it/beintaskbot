"""Creation library reuses the existing API, quotas, ownership and idempotency."""
import json
import uuid
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_ai_agents_store as agents
import tenant_ai_agents_schema as agent_schema
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def creation(db,monkeypatch):
    seed_job(db)
    monkeypatch.setattr(agents,'platform_service_ready',lambda:True)
    monkeypatch.setattr(agents,'platform_model_allowed',lambda model:model in ('','fixture-model'))
    return db


def draft(**changes):
    return {'name':'Оператор технической поддержки','instructions':'Use only verified knowledge; clarify issues; hand off.',
      'enabled':False,'mode':'manual','model':'fixture-model','language':'auto','style':'balanced','delay_seconds':40,
      'scopes':[{'provider':'connect','channel_id':CA,'pipeline_id':'','stage_id':'','instructions':'','allow_group_messages':False}],
      'schedule':{'timezone':'Asia/Baku','days':[0,1,2,3,4],'start':'19:00','end':'09:00'},
      'setup_key':'agent-library-'+str(uuid.uuid4()),**changes}


def test_template_and_blank_creation_use_safe_existing_api_defaults(creation):
    before=creation.execute('SELECT * FROM saas_ai_agent_settings')[0];creation.commit()
    template=agents.save_agent(PROFILE,draft())
    blank=agents.save_agent(PROFILE,draft(name='My agent',instructions='My policy',schedule={}))
    for agent in (template,blank):
        assert agent['enabled'] is False and agent['mode']=='manual'
        assert agent['scopes'][0]['channel_id']==CA
        assert not agent['scopes'][0]['allow_group_messages']
    assert template['schedule']['start']=='19:00' and template['schedule']['end']=='09:00'
    assert blank['schedule']=={}
    assert creation.execute('SELECT * FROM saas_ai_agent_settings')[0]==before


def test_company_active_mode_does_not_enable_or_change_agents(creation):
    before=creation.execute("SELECT enabled,mode FROM saas_ai_agents WHERE tenant_id=%s::uuid",(A,))[0]
    company_before=creation.execute("SELECT enabled,auto_send_enabled,emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]
    saved=agents.save_settings(PROFILE,{'operating_mode':'active'})
    assert saved['operating_mode']=='active'
    assert creation.execute("SELECT enabled,mode FROM saas_ai_agents WHERE tenant_id=%s::uuid",(A,))[0]==before
    after=creation.execute("SELECT enabled,auto_send_enabled,emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]
    assert after==company_before


def test_global_simulation_and_case_extraction_gates_are_not_bypassable(creation,monkeypatch):
    monkeypatch.setattr(agents,'runtime_flag',lambda name:name!='AI_SIMULATION_ENABLED')
    with pytest.raises(TenantLinearError,match='Simulation'):
        agents.save_settings(PROFILE,{'operating_mode':'simulation'})
    monkeypatch.setattr(agents,'_support_case_extraction_runtime_enabled',lambda tenant:False)
    with pytest.raises(TenantLinearError,match='Dəstək case'):
        agents.save_settings(PROFILE,{'support_case_extraction_enabled':True})
    assert creation.execute("SELECT support_case_extraction_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]['support_case_extraction_enabled'] is False


def test_legacy_test_mode_is_quarantined_without_deleting_history(creation):
    creation.execute("UPDATE saas_ai_agent_settings SET operating_mode=NULL,auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    creation.execute("UPDATE saas_tenant_webhook_events SET state='queued' WHERE tenant_id=%s::uuid AND fingerprint='evt'",(A,))
    creation.execute("UPDATE saas_ai_agent_jobs SET state='queued' WHERE tenant_id=%s::uuid AND event_fingerprint='evt'",(A,))
    creation.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_operating_mode_check')
    creation.execute("UPDATE saas_ai_agent_settings SET operating_mode='test' WHERE tenant_id=%s::uuid",(A,))
    creation.commit()
    agent_schema._ready=False
    agent_schema.ensure_schema(creation)
    setting=creation.execute("SELECT operating_mode,auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid",(A,))[0]
    job=creation.execute("SELECT state,error_code FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint='evt'",(A,))[0]
    event=creation.execute("SELECT state,error_code FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND fingerprint='evt'",(A,))[0]
    assert setting=={'operating_mode':'manual','auto_send_enabled':False}
    assert job=={'state':'skipped','error_code':'legacy_test_mode_retired'}
    assert event=={'state':'skipped','error_code':'legacy_test_mode_retired'}
    assert creation.execute("SELECT count(*) AS n FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint='evt'",(A,))[0]['n']==1


def test_retry_keys_keep_agent_trigger_and_knowledge_agent_bound(creation):
    payload=draft();first=agents.save_agent(PROFILE,payload)
    assert agents.save_agent(PROFILE,payload)['id']==first['id']
    assert agents.save_agent(PROFILE,{**payload,'instructions':'must not overwrite'})['instructions']==first['instructions']
    trigger={'name':'Support handoff','trigger_type':'phrase','condition':{'phrase':'manager|incident'},'actions':['handoff'],'enabled':True,'setup_key':payload['setup_key']+'-handoff'}
    assert agents.save_trigger(PROFILE,first['id'],trigger)['id']==agents.save_trigger(PROFILE,first['id'],trigger)['id']
    source={'title':'Manual','kind':'txt','content':'Router diagnostics','active':True,'agent_id':first['id'],'setup_key':payload['setup_key']+'-kb-file'}
    saved=agents.save_knowledge(PROFILE,source)
    assert agents.save_knowledge(PROFILE,source)['id']==saved['id']
    assert str(saved['agent_id'])==first['id']
    rows=creation.execute('SELECT agent_id FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid',(A,))
    assert len(rows)==1 and str(rows[0]['agent_id'])==first['id']
    assert creation.execute('SELECT count(*) AS n FROM saas_ai_agent_triggers')[0]['n']==1


def test_knowledge_center_metadata_is_tenant_scoped_and_human_readable(creation):
    agent=agents.save_agent(PROFILE,draft())
    source=agents.save_knowledge(PROFILE,{'title':'Published instruction','kind':'text','content':'verified','active':True,'agent_id':agent['id'],'setup_key':'center-instruction'})
    creation.execute("UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE id=%s::uuid",(json.dumps({'instruction_id':str(uuid.uuid4())}),source['id']))
    creation.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,active,content_hash,created_by,provenance) VALUES(%s::uuid,%s::uuid,'General','text','shared',true,'hash',11,'{}'::jsonb)",(str(uuid.uuid4()),A));creation.commit()
    rows=agents.list_knowledge(PROFILE)
    bound=next(row for row in rows if row['title']=='Published instruction')
    general=next(row for row in rows if row['title']=='General')
    assert bound['origin_type']=='instruction' and bound['origin_id'] and bound['agent_name']==agent['name']
    assert general['origin_type']=='knowledge_source' and general['agent_id'] is None and general['agent_name'] is None
    assert all(row['tenant_id']==A for row in rows)


@pytest.mark.parametrize('case',['foreign_channel','foreign_actor','inactive','archived','worker','admin_without_permission'])
def test_current_tenant_and_manage_permissions_are_required(creation,case):
    profile=PROFILE;payload=draft()
    if case=='foreign_channel':payload['scopes'][0]['channel_id']=CB
    elif case=='foreign_actor':profile={'tenant_id':B,'telegram_id':11}
    elif case=='inactive':creation.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,))
    elif case=='archived':creation.execute("UPDATE saas_tenants SET status='archived' WHERE id=%s::uuid",(A,))
    else:creation.execute('UPDATE saas_tenant_members SET role=%s WHERE tenant_id=%s::uuid',('worker' if case=='worker' else 'admin',A))
    creation.commit()
    with pytest.raises(TenantLinearError):agents.save_agent(profile,payload)
    assert creation.execute('SELECT count(*) AS n FROM saas_ai_agents')[0]['n']==1


def test_only_explicit_admin_grant_can_create_and_foreign_knowledge_cannot_attach(creation):
    creation.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s::uuid,33,'admin')",(A,))
    creation.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members','{\"33\":{\"ai_agents_manage\":true}}'::jsonb)",(A,));creation.commit()
    agent=agents.save_agent({'tenant_id':A,'telegram_id':33},draft())
    with pytest.raises(TenantLinearError):agents.save_knowledge({'tenant_id':B,'telegram_id':22},{'title':'Foreign','content':'Do not share','agent_id':agent['id']})
    assert creation.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n']==0


def test_multiple_channels_remain_explicit_and_foreign_connections_reject(creation):
    second=str(uuid.uuid4());creation.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Support')",(A,second));creation.commit()
    payload=draft();payload['scopes'].append({**payload['scopes'][0],'channel_id':second})
    saved=agents.save_agent(PROFILE,payload)
    assert {scope['channel_id'] for scope in saved['scopes']}=={CA,second}
    assert all(scope['channel_id'] and not scope['allow_group_messages'] for scope in saved['scopes'])
    with pytest.raises(TenantLinearError):agents.save_agent(PROFILE,draft(scopes=[{'provider':'instagram','channel_id':CA}]))


def test_separate_employee_retry_keys_do_not_expose_other_setup(creation):
    creation.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s::uuid,33,'owner')",(A,));creation.commit()
    data=draft();first=agents.save_agent(PROFILE,data)
    second=agents.save_agent({'tenant_id':A,'telegram_id':33},data)
    assert second['id']!=first['id']
