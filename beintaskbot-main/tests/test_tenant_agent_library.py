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


def test_exact_knowledge_duplicates_can_be_previewed_archived_and_rolled_back(creation):
    agent=agents.save_agent(PROFILE,draft())
    payload={'kind':'txt','content':'Exact verified warehouse guidance.','active':True,'agent_id':agent['id']}
    first=agents.save_knowledge(PROFILE,{**payload,'title':'Canonical','setup_key':'dedup-canonical-key'})
    second=agents.save_knowledge(PROFILE,{**payload,'title':'Duplicate','setup_key':'dedup-duplicate-key'})
    preview=agents.knowledge_deduplication(PROFILE,{'action':'preview','canonical_id':first['id'],'duplicate_ids':[second['id']]})
    assert preview['eligible'] is True and preview['checks'][0]['chunks_match'] is True
    archived=agents.knowledge_deduplication(PROFILE,{'action':'archive','canonical_id':first['id'],'duplicate_ids':[second['id']],
        'expected_precondition':preview['precondition'],'request_key':str(uuid.uuid4())})
    assert archived['changed_ids']==[second['id']]
    row=creation.execute('SELECT active,content FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(second['id'],))[0]
    assert row=={'active':False,'content':'Exact verified warehouse guidance.'}
    rollback_preview=agents.knowledge_deduplication(PROFILE,{'action':'preview','canonical_id':first['id'],'duplicate_ids':[second['id']]})
    restored=agents.knowledge_deduplication(PROFILE,{'action':'rollback','canonical_id':first['id'],'duplicate_ids':[second['id']],
        'expected_precondition':rollback_preview['precondition'],'request_key':str(uuid.uuid4())})
    assert restored['action']=='rollback'
    assert creation.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(second['id'],))[0]['active'] is True


def test_standalone_knowledge_archives_and_restores_without_erasing_chunks(creation):
    agent=agents.save_agent(PROFILE,draft())
    source=agents.save_knowledge(PROFILE,{'title':'Temporary guidance','kind':'txt','content':'Keep historical evidence.','active':True,'agent_id':agent['id'],'setup_key':'archive-source'})
    assert agents.delete_knowledge(PROFILE,source['id'])=={'archived':True}
    stored=creation.execute('SELECT active,content FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]
    assert stored=={'active':False,'content':'Keep historical evidence.'}
    assert creation.execute('SELECT count(*) AS n FROM saas_ai_knowledge_chunks WHERE tenant_id=%s::uuid AND source_id=%s::uuid',(A,source['id']))[0]['n']>0
    assert agents.restore_knowledge(PROFILE,source['id'])=={'restored':True}
    assert creation.execute('SELECT active FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]['active'] is True


def test_archived_knowledge_exposes_only_the_safe_trash_marker(creation):
    agent=agents.save_agent(PROFILE,draft())
    source=agents.save_knowledge(PROFILE,{'title':'Deleted guidance','kind':'txt','content':'Historical evidence stays private.','active':True,'agent_id':agent['id'],'setup_key':'trash-marker'})
    agents.delete_knowledge(PROFILE,source['id'])
    row=next(item for item in agents.list_knowledge(PROFILE) if item['id']==source['id'])
    assert row['active'] is False and row['provenance']['archive']['kind']=='manual'
    assert set(row['provenance'])=={'archive'}


def test_knowledge_deduplication_fails_closed_for_content_or_owner_mismatch(creation):
    agent=agents.save_agent(PROFILE,draft())
    first=agents.save_knowledge(PROFILE,{'title':'Canonical','kind':'txt','content':'One','active':True,'agent_id':agent['id'],'setup_key':'dedup-source-one'})
    second=agents.save_knowledge(PROFILE,{'title':'Different','kind':'txt','content':'Two','active':True,'agent_id':agent['id'],'setup_key':'dedup-source-two'})
    result=agents.knowledge_deduplication(PROFILE,{'action':'preview','canonical_id':first['id'],'duplicate_ids':[second['id']]})
    assert result['eligible'] is False
    with pytest.raises(TenantLinearError):
        agents.knowledge_deduplication(PROFILE,{'action':'archive','canonical_id':first['id'],'duplicate_ids':[second['id']]})
    creation.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));creation.commit()
    with pytest.raises(TenantLinearError):
        agents.knowledge_deduplication(PROFILE,{'action':'preview','canonical_id':first['id'],'duplicate_ids':[second['id']]})


def test_knowledge_deduplication_rejects_stale_preview_precondition(creation):
    agent=agents.save_agent(PROFILE,draft())
    payload={'kind':'txt','content':'Verified same content','active':True,'agent_id':agent['id']}
    first=agents.save_knowledge(PROFILE,{**payload,'title':'Canonical','setup_key':'dedup-precondition-canonical'})
    second=agents.save_knowledge(PROFILE,{**payload,'title':'Duplicate','setup_key':'dedup-precondition-duplicate'})
    preview=agents.knowledge_deduplication(PROFILE,{'action':'preview','canonical_id':first['id'],'duplicate_ids':[second['id']]})
    creation.execute('UPDATE saas_ai_knowledge_sources SET title=%s,updated_at=now() WHERE id=%s::uuid',('Changed after preview',second['id']))
    creation.commit()
    with pytest.raises(TenantLinearError):
        agents.knowledge_deduplication(PROFILE,{'action':'archive','canonical_id':first['id'],'duplicate_ids':[second['id']],
            'expected_precondition':preview['precondition'],'request_key':str(uuid.uuid4())})


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
