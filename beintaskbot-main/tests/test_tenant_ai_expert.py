"""Isolated PostgreSQL/WASM regression tests; provider is always fake."""
import asyncio
import json
import time
import uuid
from pathlib import Path
from unittest.mock import patch
import pytest
from test_integrated_connect_release import flow
from test_tenant_support_inbox import database, db, seed_job, A, B, CA, CB, AGENT, PROFILE, manual
import tenant_ai_expert_service as service
import tenant_ai_expert_policy as policy
import tenant_ai_expert_tools as tools
import tenant_ai_agents_store as agents
import tenant_ai_agents_policy as agent_policy
import tenant_support_inbox_store as inbox
from tenant_linear_policy import TenantLinearError
from tenant_ai_testing_budget import TOKEN_USAGE_SQL

EXPERT=str(uuid.UUID(int=401))
GOOD={'cause':'Hypothesis: sync unavailable','evidence':['No diagnostic API is connected.'],'confidence':'low',
    'safe_actions':['Ask for the exact error code.'],'additional_information':'Application version',
    'requires_human':False,'ticket_summary':'Sync requires verification'}

@pytest.fixture
def expert(db,monkeypatch):
    monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(service,'assert_job_allowed',lambda job:None)
    monkeypatch.setattr(service,'knowledge_context',lambda *args,**kw:[])
    monkeypatch.setattr(service,'choose_api_credential',lambda *args:('test-only','company'))
    monkeypatch.setattr(service,'generate',lambda *args,**kw:(json.dumps(GOOD),{'input_tokens':80,'output_tokens':20,'total_tokens':100}))
    job=seed_job(db)
    cfg=policy.policy({'expert_agent_id':EXPERT,'tools':['system_status']})
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,role,model) VALUES(%s::uuid,%s::uuid,'Diagnostic expert',11,TRUE,'expert','test-model')",(A,EXPERT))
    db.execute("UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(cfg),A,AGENT))
    db.execute("UPDATE saas_ai_agent_jobs SET state='processing',lease_token=%s::uuid,lease_until=now()+interval '3 minutes',revision=1 WHERE tenant_id=%s::uuid",(job['lease_token'],A))
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb)",(A,json.dumps({'enabled':True,'consent':True,'model':'test-model','daily_limit':50})))
    run=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test-model','generating')",(A,run,AGENT))
    row=db.execute("SELECT a.version,s.updated_at AS settings_version,i.updated_at AS config_version,cs.revision FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id JOIN saas_tenant_integrations i ON i.tenant_id=a.tenant_id AND i.provider='chat_ai' JOIN saas_ai_conversation_state cs ON cs.tenant_id=a.tenant_id WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid",(A,AGENT))[0]
    parent={'run_id':run,'agent_version':row['version'],'settings_version':str(row['settings_version']),'config_version':str(row['config_version']),'conversation_revision':row['revision']}
    db.commit()
    return db,job,parent

def perform(expert):
    db,job,parent=expert
    reservation=service.reserve(job,parent)
    return service.execute(job,parent,reservation,'Sync fails',[{'speaker':'CLIENT','text':'Error 42'}])

def test_real_reservation_structured_result_usage_and_idempotency(expert):
    db,job,parent=expert
    first=service.reserve(job,parent)
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==12000;db.commit()
    response=service.execute(job,parent,first,'Sync fails',[{'speaker':'CLIENT','text':'Error 42'}])
    assert response['status']=='completed' and response['result']==GOOD
    assert service.reserve(job,parent)['existing']==response
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==100
    assert db.execute('SELECT count(*) AS n FROM saas_ai_expert_tasks')[0]['n']==1
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_drafts')[0]['n']==0
    assert db.execute('SELECT state FROM saas_ai_conversation_state')[0]['state']=='active'


def test_expert_returns_only_server_verified_source_quotes(expert,monkeypatch):
    db,job,parent=expert
    source={'source_id':'tenant-source-1','title':'Returns policy',
        'content':'Returns accepted within fourteen days with a receipt.'}
    monkeypatch.setattr(service,'knowledge_context',lambda *args,**kwargs:[source])
    quoted={**GOOD,'evidence':['Returns accepted within fourteen days with a receipt.']}
    monkeypatch.setattr(service,'generate',lambda *args,**kwargs:(json.dumps(quoted),{'total_tokens':23}))
    response=perform(expert)
    assert response['result']==quoted
    assert response['grounded_sources']==[{'source_id':'tenant-source-1','title':'Returns policy',
        'quote':'Returns accepted within fourteen days with a receipt.'}]
    assert service.reserve(job,parent)['existing']==response
    stored=db.execute('SELECT grounded_sources FROM saas_ai_expert_tasks')[0]['grounded_sources']
    assert stored==response['grounded_sources']

@pytest.mark.parametrize('state',['human','paused'])
def test_human_pause_before_and_during_expert(expert,state):
    db,job,parent=expert
    reservation=service.reserve(job,parent)
    db.execute('UPDATE saas_ai_conversation_state SET state=%s,revision=revision+1 WHERE tenant_id=%s::uuid',(state,A));db.commit()
    with pytest.raises(TenantLinearError):service.reserve(job,parent)
    result=service.execute(job,parent,reservation,'x',[])
    assert result['status']=='stale' and result['result'] is None
    assert db.execute('SELECT state FROM saas_ai_conversation_state')[0]['state']==state

@pytest.mark.parametrize('change',[
    "UPDATE saas_ai_agent_settings SET emergency_stop=TRUE",
    "UPDATE saas_ai_agent_settings SET enabled=FALSE",
    "UPDATE saas_ai_agents SET enabled=FALSE WHERE role='expert'",
    "UPDATE saas_tenants SET status='archived'",
    "UPDATE saas_ai_agents SET role='expert',expert_policy='{}' WHERE role='primary'",
])
def test_live_permissions_and_stop(expert,change):
    db,job,parent=expert;db.execute(change);db.commit()
    with pytest.raises(TenantLinearError):service.reserve(job,parent)
    assert db.execute('SELECT count(*) AS n FROM saas_ai_expert_tasks')[0]['n']==0

def test_foreign_tenant_channel_expert_and_parent(expert):
    db,job,parent=expert
    for patch in ({'tenant_id':B},{'channel_id':CB},{'agent_id':EXPERT}):
        with pytest.raises(TenantLinearError):service.reserve({**job,**patch},parent)
    db.execute('UPDATE saas_ai_agents SET tenant_id=%s::uuid,created_by=22 WHERE id=%s::uuid',(B,EXPERT));db.commit()
    with pytest.raises(TenantLinearError):service.reserve(job,parent)

@pytest.mark.parametrize('mode',['calls','tokens','per_dialogue'])
def test_limits_prevent_model_call(expert,monkeypatch,mode):
    db,job,parent=expert
    if mode=='calls':
        db.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{daily_limit}','1') WHERE provider='chat_ai'")
        # Metadata changed: refresh the trusted parent version for this admission test.
    elif mode=='tokens':
        monkeypatch.setattr(service,'choose_api_credential',lambda *a:('test-only','platform'))
        monkeypatch.setattr(service,'platform_model_allowed',lambda *a:True)
        monkeypatch.setattr(service,'platform_token_limit',lambda *a:100)
    else:
        for index in range(2):
            task=str(uuid.uuid4())
            db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',%s,'test-model','completed')",(A,task,EXPERT,index+10))
            db.execute("INSERT INTO saas_ai_expert_tasks(tenant_id,id,parent_run_id,principal_agent_id,expert_agent_id,provider,channel_id,conversation_id,expert_version,deadline) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,'connect',%s,'client',1,now())",(A,task,task,AGENT,EXPERT,CA))
    db.commit()
    with pytest.raises(TenantLinearError) as error:service.reserve(job,parent)
    assert error.value.status==429

def test_model_error_retains_unknown_budget_and_never_retries(expert,monkeypatch):
    db,job,parent=expert
    monkeypatch.setattr(service,'generate',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('private provider body')))
    first=perform(expert)
    assert first['status']=='failed' and first['result'] is None
    assert service.reserve(job,parent)['existing']==first
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==12000
    assert 'private' not in db.execute('SELECT error_code FROM saas_ai_expert_tasks')[0]['error_code']

def test_invalid_model_result_still_accounts_usage(expert,monkeypatch):
    db,job,parent=expert
    monkeypatch.setattr(service,'generate',lambda *a,**kw:('{"private_reasoning":"discard"}',{'total_tokens':99}))
    response=perform(expert)
    assert response['status']=='failed' and response['result'] is None
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==99

def test_expired_result_not_returned_to_primary(expert):
    db,job,parent=expert;reservation=service.reserve(job,parent)
    db.execute("UPDATE saas_ai_expert_tasks SET deadline=now()-interval '1 second'");db.commit()
    response=service.execute(job,parent,reservation,'x',[])
    assert response['status']=='timeout' and response['result'] is None

def test_context_selection_bounded_and_read_only_registry():
    cfg=policy.policy({'expert_agent_id':EXPERT,'max_context_chars':1200})
    value=service.bounded_context({'tenant_id':A,'provider':'connect','channel_id':CA,'conversation_id':'client'},cfg,'problem'*500,[{'speaker':'CLIENT','text':'x'*2000,'media_url':'SECRET'}]*30,[],[])
    assert len(json.dumps(value,ensure_ascii=False))<=1200
    assert 'SECRET' not in json.dumps(value)
    assert tools.diagnose('system_status',tenant_id=A,channel_id=CA,conversation_id='client')['status']=='integration_unavailable'
    assert tools.mock_diagnose('system_status','synthetic fixture')['simulation'] is True
    for tool in ('sql','shell','webhook','send','../../secrets'):
        with pytest.raises(ValueError):tools.diagnose(tool,tenant_id=A,channel_id=CA,conversation_id='client')

def test_agent_role_policy_and_recursion():
    assert agent_policy.agent({'name':'expert','role':'expert'},creating=True)['role']=='expert'
    assert agent_policy.trigger({'name':'escalation','trigger_type':'phrase','condition':{'phrase':'complex'},'actions':['call_expert']},creating=True)['actions']==['call_expert']
    with pytest.raises(TenantLinearError):policy.policy({'expert_agent_id':EXPERT,'tools':['sql']})
    with pytest.raises(TenantLinearError):policy.policy({'expert_agent_id':EXPERT,'max_calls':100})
    with pytest.raises(TenantLinearError):policy.validate_binding(None,A,EXPERT,{'role':'expert','expert_policy':{'expert_agent_id':AGENT},'scopes':[],'mode':'manual'})
    with pytest.raises(TenantLinearError):policy.validate_binding(None,A,EXPERT,{'role':'primary','expert_policy':{'expert_agent_id':EXPERT}})

def test_settings_use_existing_crud_and_permissions(expert,monkeypatch):
    db,job,parent=expert
    # Existing Chat AI model approval remains required; platform mock only for this isolated CRUD test.
    monkeypatch.setattr(agents,'platform_service_ready',lambda:True)
    monkeypatch.setattr(agents,'platform_model_allowed',lambda m:True)
    expert_agent=next(a for a in agents.list_agents(PROFILE) if a['id']==EXPERT)
    assert expert_agent['role']=='expert' and expert_agent['scopes']==[]
    updated=agents.save_agent(PROFILE,{'name':'Renamed expert','expected_version':expert_agent['version']},agent_id=EXPERT)
    assert updated['role']=='expert' and updated['name']=='Renamed expert'
    with pytest.raises(TenantLinearError):agents.save_agent({'tenant_id':B,'telegram_id':22},{'name':'Foreign','expected_version':updated['version']},agent_id=EXPERT)
    with pytest.raises(TenantLinearError):agents.save_agent(PROFILE,{'role':'primary','expected_version':updated['version']},agent_id=EXPERT)

def test_ticket_history_is_internal_tenant_and_channel_scoped(expert):
    db,job,parent=expert;perform(expert)
    ticket=manual()['ticket']['id'];detail=inbox.detail(PROFILE,ticket)
    assert detail['expert_results'][0]['result']==GOOD
    with pytest.raises(TenantLinearError):inbox.detail({'tenant_id':B,'telegram_id':22},ticket)
    db.execute('UPDATE saas_ai_expert_tasks SET channel_id=%s',(CB,));db.commit()
    assert inbox.detail(PROFILE,ticket)['expert_results']==[]

def test_request_only_explicit_trigger_and_hard_timeout(monkeypatch):
    monkeypatch.setattr(service,'selected_helpers',lambda *a: [EXPERT])
    async def scenario():
        called=[]
        def reserve(*a):called.append(True);return {'task_id':'test','config':{'timeout_seconds':0.01}}
        monkeypatch.setattr(service,'reserve',reserve)
        monkeypatch.setattr(service,'execute',lambda *a:(time.sleep(0.05) or {'status':'completed'}))
        assert await service.request_expert({}, {},problem='x',lines=[],triggers=[]) is None
        assert not called
        result=await service.request_expert({}, {},problem='x',lines=[],triggers=[{'actions':['call_expert']}])
        assert result['status']=='timeout';await asyncio.sleep(0.06)
    asyncio.run(scenario())

def test_no_external_action_imports_or_pilot_changes():
    root=Path(__file__).resolve().parents[1]
    source=(root/'tenant_ai_expert_service.py').read_text()
    assert not any(name in source for name in ('send_ai_reply','kommo_request','tenant_linear_api','requests.post','subprocess'))
    editor=(root/'docs/tenant-ai-expert-editor.js').read_text()
    assert 'innerHTML' in editor and '/webapp' not in editor


def test_group_contract_revision_fencing(expert,monkeypatch):
    import sys,types
    db,job,parent=expert;group='120363000000001@g.us'
    db.execute("UPDATE saas_ai_agent_jobs SET conversation_id=%s,chat_id=%s",(group,group))
    db.execute("UPDATE saas_ai_conversation_state SET conversation_id=%s",(group,));db.commit()
    job={**job,'conversation_id':group,'chat_id':group}
    with pytest.raises(TenantLinearError):service.reserve(job,parent)
    calls=[];revision=[4]
    def authority(cur,tenant,channel,conversation,agent_id=None,expected_revision=None):
        calls.append((tenant,channel,conversation,agent_id,expected_revision))
        if expected_revision!=revision[0]:return None
        return {'revision':revision[0],'primary_agent_id':AGENT,'sending_channel_id':CA}
    import tenant_ai_group_coordination as coordination
    monkeypatch.setattr(coordination,'authority',authority)
    monkeypatch.setattr(coordination,'job_allowed',lambda cur,job: True)
    job['payload']={'group_revision':4}
    with pytest.raises(TenantLinearError):service.reserve(job,parent)
    job['payload']={'group_revision':4,'group_event_key':'server-verified'}
    receipt=service.reserve(job,parent)
    assert calls[-1]==(A,CA,group,AGENT,4)
    revision[0]=5
    response=service.execute(job,parent,receipt,'diagnose',[])
    assert response['status']=='stale' and response['result'] is None

def test_expert_crud_creation_retry_selection_and_no_channels(expert,monkeypatch):
    db,job,parent=expert
    monkeypatch.setattr(agents,'platform_service_ready',lambda:True)
    monkeypatch.setattr(agents,'platform_model_allowed',lambda m:True)
    data={'name':'New diagnostic expert','role':'expert','model':'test-model','setup_key':'expert_creation_retry_key'}
    created=agents.save_agent(PROFILE,data)
    assert created['role']=='expert' and created['mode']=='manual' and not created['enabled'] and created['scopes']==[]
    assert agents.save_agent(PROFILE,data)['id']==created['id']
    with pytest.raises(TenantLinearError):agents.save_agent(PROFILE,{**data,'setup_key':'different_retry_key','mode':'automatic'})
    with pytest.raises(TenantLinearError):agents.save_agent(PROFILE,{'name':'Bad expert','role':'expert','model':'test-model','scopes':[{'provider':'connect','channel_id':CA}]})
    primary=next(a for a in agents.list_agents(PROFILE) if a['id']==AGENT)
    cfg=policy.policy({'expert_agent_id':created['id']})
    with pytest.raises(TenantLinearError):agents.save_agent(PROFILE,{'expert_policy':cfg,'expected_version':primary['version']},agent_id=AGENT)
    agents.save_agent(PROFILE,{'enabled':True,'expected_version':created['version']},agent_id=created['id'])
    assert agents.save_agent(PROFILE,{'expert_policy':cfg,'expected_version':primary['version']},agent_id=AGENT)['expert_policy']==cfg

@pytest.mark.parametrize('field',['cause','ticket_summary','safe_actions'])
def test_secret_like_model_output_rejected(field):
    data={**GOOD,field:['api_key=secret-value'] if field=='safe_actions' else 'api_key=secret-value'}
    with pytest.raises(ValueError):policy.result(data)


@pytest.mark.parametrize('outcome',['advisory','human','takeover'])
def test_full_primary_worker_expert_round_trip(flow,monkeypatch,outcome):
    import tenant_ai_agent_worker as worker
    import tenant_ai_agent_connect as connect
    from unittest.mock import Mock
    db=flow['db']
    monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(service,'choose_api_credential',lambda *a:('test-expert-key','platform'))
    monkeypatch.setattr(service,'platform_model_allowed',lambda *a:True)
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:0)
    cfg=policy.policy({'expert_agent_id':EXPERT})
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,role,model) VALUES(%s::uuid,%s::uuid,'Expert',11,TRUE,'expert','test-model')",(A,EXPERT))
    db.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s::uuid',(json.dumps(cfg),AGENT))
    db.execute("INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions,enabled) VALUES(%s::uuid,%s::uuid,%s::uuid,'Escalation','phrase',%s::jsonb,%s::jsonb,TRUE)",(A,str(uuid.uuid4()),AGENT,json.dumps({'phrase':'complex'}),json.dumps(['call_expert'])));db.commit()
    def expert_generate(*args,**kwargs):
        if outcome=='takeover':connect.mark_operator_reply(A,41,CA,'client')
        return json.dumps({**GOOD,'requires_human':outcome=='human'}),{'total_tokens':12}
    generation=Mock(side_effect=expert_generate);monkeypatch.setattr(service,'generate',generation)
    flow['inbound']('text','complex problem');flow['process']()
    generation.assert_called_once()
    assert db.execute('SELECT count(*) AS n FROM saas_ai_expert_tasks')[0]['n']==1
    if outcome=='advisory':
        flow['generate'].assert_called_once();flow['send'].assert_called_once()
        assert flow['generate'].call_args.kwargs['extra_context']['internal_expert_advisory']['result']==GOOD
    else:
        flow['generate'].assert_not_called();flow['send'].assert_not_called()
        assert db.execute('SELECT state FROM saas_ai_conversation_state')[0]['state']=='human'
        if outcome=='human':assert db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==1
    assert not db.execute("SELECT 1 FROM saas_tenant_integrations WHERE provider='kommo'")


def test_expert_knowledge_scope_uses_existing_search(expert,monkeypatch):
    import tenant_ai_agent_run_store as runs
    db,job,parent=expert
    monkeypatch.setattr(runs,'_connect',lambda:db)
    monkeypatch.setattr(service,'knowledge_context',runs.knowledge_context)
    for title,agent in [('Expert knowledge',EXPERT),('Primary private',AGENT),('Company shared',None)]:
        agents.save_knowledge(PROFILE,{'title':title,'kind':'text','content':'sync '+title,'agent_id':agent,'active':True})
    agents.save_knowledge({'tenant_id':B,'telegram_id':22},{'title':'Foreign tenant','kind':'text','content':'sync Foreign tenant','active':True})
    received=[]
    def generate(*args,**kw):
        received.extend(kw['extra_context']['knowledge']);return json.dumps(GOOD),{'total_tokens':10}
    monkeypatch.setattr(service,'generate',generate)
    receipt=service.reserve(job,parent);response=service.execute(job,parent,receipt,'sync',[])
    assert response['status']=='completed'
    assert {row['title'] for row in received}=={'Expert knowledge','Company shared'}


def test_context_budget_failure_before_model_releases_reservation(expert,monkeypatch):
    from unittest.mock import Mock
    db,job,parent=expert
    db.execute("UPDATE saas_ai_agents SET instructions=%s WHERE id=%s::uuid",('Ж'*12000,EXPERT));db.commit()
    generation=Mock();monkeypatch.setattr(service,'generate',generation)
    response=perform(expert)
    assert response['status']=='failed';generation.assert_not_called()
    assert db.execute('SELECT error_code FROM saas_ai_expert_tasks')[0]['error_code']=='expert_context_budget'
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==0

@pytest.mark.parametrize('options,expected_tokens,expected_timeout',[({},1800,(5,60)),({'max_output_tokens':500,'request_timeout':7},500,(5,7))])
def test_existing_provider_output_and_timeout_contract(monkeypatch,options,expected_tokens,expected_timeout):
    import tenant_chat_ai_provider as provider
    from unittest.mock import Mock
    response=Mock(status_code=200)
    response.json.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'internal result'}]}],'usage':{'input_tokens':10,'output_tokens':5}}
    post=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',post)
    text,usage=provider.generate('synthetic-test-key','approved-model','expert',[],'',with_usage=True,**options)
    assert text=='internal result' and usage['total_tokens']==15
    assert post.call_args.kwargs['json']['max_output_tokens']==expected_tokens
    assert post.call_args.kwargs['timeout']==expected_timeout
    assert post.call_args.kwargs['json']['store'] is False
