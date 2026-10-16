"""Sandbox regressions against real PostgreSQL semantics (isolated PGlite)."""
import asyncio
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_ai_testing_store as store
import tenant_ai_testing_service as service
import tenant_ai_testing_catalog as catalog
import tenant_ai_agent_test_mode as binding
import tenant_ai_agent_connect as connect
import tenant_ai_agents_store as agents
from tenant_ai_testing_budget import TOKEN_USAGE_SQL
from tenant_linear_policy import TenantLinearError


def uid():return str(uuid.uuid4())

@pytest.fixture
def sandbox(db,monkeypatch):
    for module in (store,binding,catalog):monkeypatch.setattr(module,'_connect',lambda:db)
    monkeypatch.setattr(store,'runtime_flag',lambda _:True)
    monkeypatch.setattr(connect,'runtime_flag',lambda _:True)
    monkeypatch.setattr(store,'choose_api_credential',lambda *args:('test-key','platform'))
    monkeypatch.setattr(store,'platform_model',lambda:'fixture-model')
    monkeypatch.setattr(store,'platform_service_ready',lambda:True)
    monkeypatch.setattr(store,'platform_model_allowed',lambda model:model=='fixture-model')
    monkeypatch.setattr(store,'platform_token_limit',lambda _:0)
    job=seed_job(db)
    db.execute("UPDATE saas_ai_agents SET instructions='Agent rule',language='ru',model='fixture-model',scopes=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA,'instructions':'Channel rule'}]),A))
    db.execute("UPDATE saas_ai_agent_settings SET company_instructions='Company rule' WHERE tenant_id=%s::uuid",(A,));db.commit()
    return db


def new(scope=0):return store.create(PROFILE,{'id':uid(),'agent_id':AGENT,'scope_index':scope})['session']['id']
def message(text='Internet',request=None):return {'request_id':request or uid(),'text':text}
def run(id,data=None):return asyncio.run(service.turn(PROFILE,id,data or message()))
def count(db,table):return db.execute('SELECT count(*) AS n FROM '+table)[0]['n']
def production(db):
    tables=('saas_crm_messages','saas_ai_agent_jobs','saas_ai_conversation_state','saas_ai_agent_drafts','saas_tenant_webhook_events','saas_support_tickets','saas_tenant_notifications','saas_ai_agent_settings','saas_ai_agents')
    result={table:db.execute('SELECT * FROM '+table+' ORDER BY tenant_id')[::] for table in tables};db.commit();return result


def test_real_model_rules_knowledge_and_no_external_side_effects(sandbox,monkeypatch):
    db=sandbox
    source=uid();foreign=uid()
    for tenant,id,title,active in [(A,source,'Router manual',True),(B,foreign,'Foreign secret',True),(A,uid(),'Inactive secret',False)]:
        db.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,content_hash,created_by,active) VALUES(%s::uuid,%s::uuid,%s,'text','Internet router reset','hash',%s,%s)",(id,tenant,title,11 if tenant==A else 22,active))
        db.execute("INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content) VALUES(%s::uuid,%s::uuid,0,'Internet router reset')",(tenant,id))
    db.commit();before=production(db);generate=Mock(return_value=('Перезагрузите роутер.',{'total_tokens':120}));monkeypatch.setattr(service,'generate',generate)
    id=new();result=run(id)
    args,kw=generate.call_args
    assert args[:3]==('test-key','fixture-model','reply')
    assert all(rule in kw['instructions'] for rule in ('Company rule','Agent rule','Channel rule','Server safety policy'))
    assert kw['images']==[] and kw['with_usage'] is True and kw['extra_context']['simulation'] is True
    trace=result['turns'][0]['trace'];assert [k['title'] for k in trace['knowledge']]==['Router manual']
    assert trace['real_actions']==[] and result['turns'][0]['response']=='Перезагрузите роутер.'
    assert production(db)==before
    assert db.execute('SELECT lead_id,status,reserved_calls,result FROM saas_chat_ai_runs')[0]=={'lead_id':0,'status':'completed','reserved_calls':1,'result':None}
    tokens=db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'];assert int(tokens)==120
    assert 'test-key' not in json.dumps(result) and 'snapshot' not in result['turns'][0]


def test_idempotent_message_no_second_generation_and_no_reuse(sandbox,monkeypatch):
    generate=Mock(return_value=('ok',{'total_tokens':15}));monkeypatch.setattr(service,'generate',generate)
    id=new();data=message();first=run(id,data);assert run(id,data)==first;assert generate.call_count==1
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,id,{**data,'text':'different'})
    assert count(sandbox,'saas_ai_test_turns')==1


def test_provider_failure_retains_quota_and_is_never_replayed(sandbox,monkeypatch):
    generate=Mock(side_effect=RuntimeError('provider secret'));monkeypatch.setattr(service,'generate',generate)
    id=new(None);data=message();result=run(id,data)
    assert result['turns'][0]['state']=='unknown' and result['turns'][0]['response']==''
    assert result['turns'][0]['reserved_tokens']>1800
    assert 'provider secret' not in json.dumps(result)
    run(id,data);assert generate.call_count==1
    assert sandbox.execute('SELECT reserved_calls,status FROM saas_chat_ai_runs')[0]=={'reserved_calls':1,'status':'unknown'}


def add_trigger(db,kind,actions,condition=None,name='Trigger'):
    db.execute('INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,trigger_type,name,actions,condition,enabled) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,TRUE)',(A,uid(),AGENT,kind,name,json.dumps(actions),json.dumps(condition or {})));db.commit()


def test_actual_triggers_actions_are_simulated(sandbox,monkeypatch):
    db=sandbox;add_trigger(db,'new_inquiry',['create_task'],name='First');add_trigger(db,'phone_received',['update_phone'],name='Phone');add_trigger(db,'phrase',['move_stage','webhook'],{'phrase':'internet','target_stage_id':77},'Phrase')
    generate=Mock(return_value=('ok',{'total_tokens':5}));monkeypatch.setattr(service,'generate',generate)
    before=production(db);id=new();result=run(id,message('Internet +994501234567'))
    trace=result['turns'][0]['trace'];assert {t['name'] for t in trace['triggers']}=={'First','Phone','Phrase'}
    assert generate.call_args.kwargs['extra_context']['trigger_actions']==trace['actions']
    assert trace['model']=='fixture-model'
    assert {a['action'] for a in trace['actions']}=={'create_task','update_phone','move_stage','webhook'}
    assert all(a['simulation'] and a['state']=='simulated' for a in trace['actions']) and trace['real_actions']==[]
    result=run(id,message('Internet'));assert {t['name'] for t in result['turns'][-1]['trace']['triggers']}=={'Phrase'}
    assert production(db)==before


@pytest.mark.parametrize('source',['customer','trigger','after_reply'])
def test_handoff_is_private_and_resume_only_new_messages(sandbox,monkeypatch,source):
    if source!='customer':add_trigger(sandbox,'phrase',['handoff_after_reply' if source=='after_reply' else 'handoff'],{'phrase':'Escalate'})
    generate=Mock(return_value=('Ответ.',{'total_tokens':10}));monkeypatch.setattr(service,'generate',generate)
    before=production(sandbox);id=new();result=run(id,message('manager please' if source=='customer' else 'Escalate'))
    assert result['session']['state']=='human';assert generate.call_count==(1 if source=='after_reply' else 0)
    result=run(id,message('private human input'));assert result['turns'][-1]['state']=='blocked'
    store.control(PROFILE,id,{'action':'resume','expected_revision':result['session']['revision']})
    run(id,message('Next new input'));history=generate.call_args.args[3]
    assert 'private human input' not in str(history)
    if source!='after_reply':assert 'manager please' not in str(history) and 'Escalate' not in str(history)
    assert production(sandbox)==before


def test_pause_inflight_cancels_output_without_refund_or_prod_mutation(sandbox):
    before=production(sandbox);id=new();item=store.reserve(PROFILE,id,message())
    paused=store.control(PROFILE,id,{'action':'pause','expected_revision':1})
    store.finish(item,'must not surface',{'total_tokens':40});result=store.read(PROFILE,id)
    assert result['session']['state']=='paused' and not result['session']['pending_turn']
    assert result['turns'][0]['state']=='superseded' and result['turns'][0]['response']==''
    assert result['turns'][0]['total_tokens']==40
    with pytest.raises(TenantLinearError):store.control(PROFILE,id,{'action':'resume','expected_revision':1})
    assert production(sandbox)==before


@pytest.mark.parametrize('change',[
    "UPDATE saas_ai_agent_settings SET emergency_stop=TRUE",
    "UPDATE saas_ai_agents SET version=version+1",
    "UPDATE saas_ai_agent_settings SET updated_at=now()+interval '1 second'",
    "UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=11",
    "UPDATE saas_tenants SET status='archived'",
])
def test_generation_rechecks_live_permissions_and_snapshots(sandbox,change):
    id=new();item=store.reserve(PROFILE,id,message());sandbox.execute(change);sandbox.commit()
    store.finish(item,'discard',{'total_tokens':10})
    row=sandbox.execute('SELECT state,response,total_tokens FROM saas_ai_test_turns')[0]
    assert row=={'state':'superseded','response':'','total_tokens':10}


@pytest.mark.parametrize('case',['tenant','other_actor','archived','revoked','worker','admin'])
def test_sessions_strict_tenant_actor_and_live_manage_permissions(sandbox,case):
    id=new();profile=PROFILE
    if case=='tenant':profile={'tenant_id':B,'telegram_id':22}
    elif case=='other_actor':
        sandbox.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s::uuid,33,'owner')",(A,));profile={**PROFILE,'telegram_id':33}
    elif case=='archived':sandbox.execute("UPDATE saas_tenants SET status='archived' WHERE id=%s::uuid",(A,))
    elif case=='revoked':sandbox.execute('UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=11')
    else:sandbox.execute('UPDATE saas_tenant_members SET role=%s WHERE telegram_id=11',('worker' if case=='worker' else 'admin',))
    sandbox.commit()
    for operation in (lambda:store.read(profile,id),lambda:store.reserve(profile,id,message()),lambda:store.control(profile,id,{'action':'resume','expected_revision':1})):
        with pytest.raises(TenantLinearError):operation()


def test_foreign_agent_scope_and_session_id_reuse_denied(sandbox):
    with pytest.raises(TenantLinearError):store.create({'tenant_id':B,'telegram_id':22},{'id':uid(),'agent_id':AGENT})
    with pytest.raises(TenantLinearError):store.create(PROFILE,{'id':uid(),'agent_id':AGENT,'scope_index':9})
    id=new()
    with pytest.raises(TenantLinearError):store.create(PROFILE,{'id':id,'agent_id':AGENT,'scope_index':None})
    with pytest.raises(TenantLinearError):store.create(PROFILE,{'id':uid(),'agent_id':AGENT,'scope':{'channel_id':CB}})


@pytest.mark.parametrize('reason',['emergency_stop','company_ai_disabled','agent_disabled','schedule_closed','reply_limit','server_ai_disabled'])
def test_stop_conditions_skip_provider(sandbox,monkeypatch,reason):
    id=new();db=sandbox
    if reason=='emergency_stop':db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE')
    elif reason=='company_ai_disabled':db.execute('UPDATE saas_ai_agent_settings SET enabled=FALSE')
    elif reason=='agent_disabled':db.execute('UPDATE saas_ai_agents SET enabled=FALSE')
    elif reason=='schedule_closed':monkeypatch.setattr(store,'is_open',lambda _:False)
    elif reason=='reply_limit':db.execute('UPDATE saas_ai_test_sessions SET reply_count=20')
    else:monkeypatch.setattr(store,'runtime_flag',lambda _:False)
    db.commit();generate=Mock();monkeypatch.setattr(service,'generate',generate)
    result=run(id);assert result['turns'][0]['trace']['reason']==reason;generate.assert_not_called()
    if reason in {'emergency_stop','company_ai_disabled','agent_disabled','server_ai_disabled'}:
        result=store.control(PROFILE,id,{'action':'pause','expected_revision':result['session']['revision']})
        with pytest.raises(TenantLinearError):store.control(PROFILE,id,{'action':'resume','expected_revision':result['session']['revision']})


def test_shared_call_budget_includes_virtual_real_chat_and_agent_runs(sandbox):
    id=new();item=store.reserve(PROFILE,id,message());store.finish(item,'ok',{'total_tokens':2})
    sandbox.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected','{\"daily_limit\":2}'::jsonb)",(A,))
    sandbox.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls) VALUES(%s::uuid,%s::uuid,11,41,'hash','v','completed',1)",(A,uid()));sandbox.commit()
    with pytest.raises(TenantLinearError) as error:store.reserve(PROFILE,id,message())
    assert error.value.status==429 and count(sandbox,'saas_ai_test_turns')==1


def test_token_cap_and_agent_deletion_cannot_erase_usage(sandbox,monkeypatch):
    id=new();monkeypatch.setattr(store,'platform_token_limit',lambda _:10)
    with pytest.raises(TenantLinearError) as error:store.reserve(PROFILE,id,message())
    assert error.value.status==429
    monkeypatch.setattr(store,'platform_token_limit',lambda _:1000000);item=store.reserve(PROFILE,id,message());store.finish(item)
    reserved=int(sandbox.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens']);sandbox.commit();assert reserved>1800
    monkeypatch.setattr(store,'platform_token_limit',lambda _:reserved+1)
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,new(),message())
    with pytest.raises(TenantLinearError):agents.delete_agent(PROFILE,AGENT)
    assert int(sandbox.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==reserved


def test_key_or_model_not_available_is_clear_no_generation(sandbox,monkeypatch):
    id=new();monkeypatch.setattr(store,'choose_api_credential',lambda *args:('','company'))
    with pytest.raises(TenantLinearError) as error:store.reserve(PROFILE,id,message())
    assert error.value.status==409
    assert count(sandbox,'saas_ai_test_turns')==0
    sandbox.commit();monkeypatch.setattr(store,'choose_api_credential',lambda *args:('key','platform'));monkeypatch.setattr(store,'platform_model_allowed',lambda _:False)
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,id,message())


def test_connect_selected_agent_exact_route_and_queue(sandbox):
    db=sandbox;other=uid()
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,enabled,created_by,scopes) SELECT tenant_id,%s::uuid,'Second',TRUE,11,scopes FROM saas_ai_agents WHERE id=%s::uuid",(other,AGENT))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected')",(A,));db.commit()
    data={'provider':'connect','agent_id':AGENT,'channel_id':CA,'conversation_id':'client','enabled':True,'emergency_stop':False}
    binding.save_test_mode(PROFILE,data);assert binding.read_test_mode(PROFILE)['connect']['agent_id']==AGENT
    with db.cursor() as cur:
        assert connect.test_allowed(cur,A,CA,'client',agent_id=AGENT)
        assert not connect.test_allowed(cur,A,CA,'client',agent_id=other)
        assert not connect.test_allowed(cur,A,CB,'client',agent_id=AGENT)
        assert not connect.test_allowed(cur,A,CA,'another',agent_id=AGENT)
        assert not connect.test_allowed(cur,B,CA,'client',agent_id=AGENT)
    db.commit()
    assert connect.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id='bound-only',event_id='e',text='Internet')==1
    assert {str(r['agent_id']) for r in db.execute('SELECT agent_id FROM saas_ai_agent_jobs')}=={AGENT}
    db.commit()
    for delta in ({'agent_id':uid()},{'channel_id':CB},{'conversation_id':'another'}):
        with pytest.raises(TenantLinearError):binding.save_test_mode(PROFILE,{**data,**delta})


def test_kommo_agent_boundary_keeps_full_strict_route(sandbox,monkeypatch):
    db=sandbox;monkeypatch.setattr(binding,'COMPANY_PHONE','+15555550102');monkeypatch.setattr(binding,'CLIENT_PHONE','+15555550101')
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain) VALUES(%s::uuid,'kommo','connected','test.kommo.com')",(A,));db.commit()
    data={'agent_id':AGENT,'company_phone':'+15555550102','client_phone':'+15555550101','company_phone_attested':True,'channel_id':'wa','lead_id':7,'contact_id':8,'talk_id':'9','chat_id':'chat','enabled':False}
    result=binding.save_test_mode(PROFILE,data);assert result['agent_id']==AGENT
    # Saving an unverified binding cannot enable it and cannot stop Connect jobs.
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE provider='connect'")[0]['state']=='queued';db.commit()
    with pytest.raises(TenantLinearError):binding.save_test_mode(PROFILE,{**data,'enabled':True})
    db.execute('UPDATE saas_ai_agent_test_mode SET enabled=TRUE,verified=TRUE WHERE tenant_id=%s::uuid',(A,));db.commit()
    job={'tenant_id':A,'provider':'kommo','agent_id':AGENT,'lead_id':7,'talk_id':'9','chat_id':'chat','channel_id':'wa','payload':{'contact_id':8}}
    assert binding.allowed_job(job)
    for change in ({'agent_id':uid()},{'chat_id':'foreign'},{'channel_id':'other'},{'payload':{'contact_id':99}},{'lead_id':42}):assert not binding.allowed_job({**job,**change})


def test_catalog_scopes_company_and_rechecks_access(sandbox):
    db=sandbox
    with pytest.raises(TenantLinearError):catalog.catalog(PROFILE)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain) VALUES(%s::uuid,'kommo','connected','own.kommo.com')",(A,));db.commit()
    calls=[]
    async def provider(tenant,method,path,**kw):
        calls.append((tenant,method,path))
        if path.startswith('leads/'):return {'id':7,'_embedded':{'contacts':[{'id':8,'name':'Own contact'}]}}
        return {'_embedded':{'talks':[{'id':9,'entity_id':7,'entity_type':'lead','chat_id':'own-chat','origin':'wa'}]}}
    result=asyncio.run(catalog.kommo_context(PROFILE,provider,lead_id='7'))
    assert result['contacts'][0]['id']==8 and result['talks'][0]['chat_id']=='own-chat'
    assert all(tenant==A and method=='GET' for tenant,method,path in calls)
    with pytest.raises(TenantLinearError):asyncio.run(catalog.kommo_context({'tenant_id':B,'telegram_id':22},provider,lead_id='7'))


def test_old_inflight_timeout_is_not_replayed_and_new_request_can_proceed(sandbox):
    id=new();first=store.reserve(PROFILE,id,message('old request'))
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,id,message('too soon'))
    sandbox.execute("UPDATE saas_ai_test_turns SET updated_at=now()-interval '6 minutes'");sandbox.commit()
    second=store.reserve(PROFILE,id,message('new request'))
    assert 'old request' not in str(second['lines'])
    store.finish(first,'late result',{'total_tokens':20})
    store.finish(second,'current',{'total_tokens':30})
    result=store.read(PROFILE,id)
    assert result['turns'][0]['state']=='unknown' and result['turns'][0]['response']==''
    assert result['turns'][1]['state']=='completed' and result['turns'][1]['response']=='current'


def test_shared_limit_also_counts_real_agent_runs(sandbox):
    sandbox.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected','{\"daily_limit\":1}'::jsonb)",(A,))
    sandbox.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,reserved_calls) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'fixture-model','completed',1)",(A,uid(),AGENT));sandbox.commit()
    with pytest.raises(TenantLinearError) as error:store.reserve(PROFILE,new(),message())
    assert error.value.status==429


def test_worker_configuration_rechecks_selected_test_agent(sandbox,monkeypatch):
    import tenant_ai_agent_run_store as runs
    monkeypatch.setattr(runs,'_connect',lambda:sandbox);monkeypatch.setattr(runs,'platform_key_ready',lambda *args:True)
    monkeypatch.setattr(runs,'platform_model_allowed',lambda _:True)
    lease=uid();sandbox.execute("UPDATE saas_ai_agent_jobs SET state='processing',lease_token=%s::uuid,lease_until=now()+interval '3 minutes'",(lease,))
    sandbox.execute("UPDATE saas_tenant_webhook_events SET payload='{\"test_mode\":true}'::jsonb")
    sandbox.execute("INSERT INTO saas_ai_agent_connect_test_mode(tenant_id,channel_id,conversation_id,agent_id,enabled,verified,updated_by) VALUES(%s::uuid,%s,'client',%s::uuid,TRUE,TRUE,11)",(A,CA,AGENT));sandbox.commit()
    job={'tenant_id':A,'agent_id':AGENT,'provider':'connect','channel_id':CA,'conversation_id':'client','lease_token':lease,'revision':1}
    assert runs.load_agent_config(job)['mode']=='manual'
    other=uid();sandbox.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled) VALUES(%s::uuid,%s::uuid,'Second',11,TRUE)",(A,other));sandbox.execute('UPDATE saas_ai_agent_connect_test_mode SET agent_id=%s::uuid',(other,));sandbox.commit()
    assert runs.load_agent_config(job,with_block_reason=True)==(None,'test_binding_mismatch')
    # Even coincident Kommo identifiers cannot authorize a Connect job.
    sandbox.execute("UPDATE saas_ai_agent_jobs SET talk_id='9'")
    sandbox.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain) VALUES(%s::uuid,'kommo','connected','test.kommo.com')",(A,))
    sandbox.execute("INSERT INTO saas_ai_agent_test_mode(tenant_id,agent_id,account_domain,channel_id,lead_id,contact_id,talk_id,chat_id,enabled,verified,updated_by,company_phone,client_phone,company_phone_attested) VALUES(%s::uuid,%s::uuid,'test.kommo.com',%s,41,8,'9','client',TRUE,TRUE,11,'+15555550102','+15555550101',TRUE)",(A,AGENT,CA))
    sandbox.execute("UPDATE saas_tenant_webhook_events SET payload=payload||'{\"contact_id\":8}'::jsonb");sandbox.commit()
    assert runs.load_agent_config(job,with_block_reason=True)==(None,'test_binding_mismatch')


@pytest.mark.parametrize('foreign',[False,True])
def test_encrypted_company_provider_is_tenant_bound(sandbox,monkeypatch,foreign):
    from cryptography.fernet import Fernet
    box=Fernet(Fernet.generate_key());monkeypatch.setattr(store,'_fernet',lambda:box)
    monkeypatch.setattr(store,'choose_api_credential',lambda tenant,agent,provider,key,model:(key,'company'))
    secret=box.encrypt(json.dumps({'tenant_id':B if foreign else A,'provider':'chat_ai','api_key':'private-company-key'}).encode())
    metadata={'enabled':True,'consent':True,'model':'fixture-model','models':['fixture-model'],'daily_limit':5}
    sandbox.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata,secrets) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb,%s)",(A,json.dumps(metadata),secret));sandbox.commit()
    id=new()
    if foreign:
        with pytest.raises(TenantLinearError) as error:store.reserve(PROFILE,id,message())
        assert error.value.status==503
    else:
        item=store.reserve(PROFILE,id,message());assert item['api_key']=='private-company-key'
        store.finish(item,'ok',{'total_tokens':20});assert 'private-company-key' not in json.dumps(store.read(PROFILE,id))


def test_platform_reservations_block_existing_post_voice_budget(sandbox,monkeypatch):
    import tenant_connect_voice as voice
    monkeypatch.setattr(voice,'_connect',lambda:sandbox)
    id=new();store.reserve(PROFILE,id,message())
    tokens=int(sandbox.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens']);sandbox.commit()
    monkeypatch.setattr(voice,'platform_token_limit',lambda _:tokens)
    assert not voice.generation_budget_available({'tenant_id':A},{'credential_source':'platform'})


def test_kommo_crm_commit_boundaries_recheck_selected_agent(sandbox,monkeypatch):
    import tenant_ai_agent_crm_actions as crm
    monkeypatch.setattr(crm,'_connect',lambda:sandbox);monkeypatch.setattr(crm,'runtime_flag',lambda _:True)
    lease=uid();sandbox.execute("UPDATE saas_ai_agents SET mode='automatic'")
    sandbox.execute('UPDATE saas_ai_agent_settings SET crm_actions_enabled=TRUE')
    sandbox.execute("UPDATE saas_ai_agent_jobs SET provider='kommo',talk_id='9',channel_id='wa',state='processing',lease_token=%s::uuid",(lease,))
    sandbox.execute("UPDATE saas_ai_conversation_state SET provider='kommo'")
    sandbox.execute("UPDATE saas_tenant_webhook_events SET provider='kommo',payload='{\"contact_id\":8}'::jsonb")
    sandbox.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain) VALUES(%s::uuid,'kommo','connected','test.kommo.com')",(A,))
    sandbox.execute("INSERT INTO saas_ai_agent_test_mode(tenant_id,agent_id,account_domain,channel_id,lead_id,contact_id,talk_id,chat_id,enabled,verified,updated_by,company_phone,client_phone,company_phone_attested) VALUES(%s::uuid,%s::uuid,'test.kommo.com','wa',41,8,'9','client',TRUE,TRUE,11,'+15555550102','+15555550101',TRUE)",(A,AGENT));sandbox.commit()
    job={'tenant_id':A,'agent_id':AGENT,'provider':'kommo','conversation_id':'client','lease_token':lease,'revision':1,'event_fingerprint':'evt'}
    assert crm._action_still_allowed(job)
    assert crm._reserve_action(job,'first','create_task',{'text':'test'})[1]=='running'
    other=uid();sandbox.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled) VALUES(%s::uuid,%s::uuid,'Second',11,TRUE)",(A,other));sandbox.execute('UPDATE saas_ai_agent_test_mode SET agent_id=%s::uuid',(other,));sandbox.commit()
    assert not crm._action_still_allowed(job)
    with pytest.raises(TenantLinearError):crm._reserve_action(job,'second','create_task',{'text':'test'})
    assert count(sandbox,'saas_ai_agent_action_runs')==1


@pytest.mark.parametrize('operating_mode,selected',[('active',False),('active',True),('test',False),('test',True)])
def test_saving_disabled_kommo_binding_never_cancels_active_or_other_contacts(sandbox,monkeypatch,operating_mode,selected):
    db=sandbox;monkeypatch.setattr(binding,'COMPANY_PHONE','+15555550102');monkeypatch.setattr(binding,'CLIENT_PHONE','+15555550101')
    db.execute('UPDATE saas_ai_agent_settings SET operating_mode=%s',(operating_mode,))
    db.execute("UPDATE saas_ai_agent_jobs SET provider='kommo',conversation_id=%s,chat_id=%s,lead_id=7,talk_id='9',channel_id='wa'",('chat' if selected else 'other','chat' if selected else 'other'))
    db.execute("UPDATE saas_tenant_webhook_events SET provider='kommo',state='queued'")
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain) VALUES(%s::uuid,'kommo','connected','test.kommo.com')",(A,));db.commit()
    binding.save_test_mode(PROFILE,{'agent_id':AGENT,'company_phone':'+15555550102','client_phone':'+15555550101','company_phone_attested':True,'channel_id':'wa','lead_id':7,'contact_id':8,'talk_id':'9','chat_id':'chat','enabled':False})
    expected='skipped' if operating_mode=='test' and selected else 'queued'
    assert db.execute('SELECT state FROM saas_ai_agent_jobs')[0]['state']==expected
    assert db.execute('SELECT state FROM saas_tenant_webhook_events')[0]['state']==expected


def test_schema_repeat_preserves_private_history_and_existing_bindings(sandbox,monkeypatch):
    from tenant_ai_testing_schema import create_schema
    monkeypatch.setattr(service,'generate',Mock(return_value=('Answer',{'total_tokens':12})))
    id=new();before=run(id)
    sandbox.execute("INSERT INTO saas_ai_agent_connect_test_mode(tenant_id,channel_id,conversation_id,enabled,verified) VALUES(%s::uuid,%s::uuid,'client',FALSE,FALSE)",(A,CA));sandbox.commit()
    for _ in range(2):
        create_schema(sandbox);sandbox.commit()
    assert store.read(PROFILE,id)==before
    old=sandbox.execute('SELECT agent_id,enabled,verified FROM saas_ai_agent_connect_test_mode WHERE tenant_id=%s::uuid',(A,))[0]
    assert old=={'agent_id':None,'enabled':False,'verified':False}
    constraints=sandbox.execute("SELECT conname FROM pg_constraint WHERE conrelid='saas_ai_test_sessions'::regclass AND contype='f'")
    assert len(constraints)==2
