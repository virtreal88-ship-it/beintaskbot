"""Isolated owner-consented training policy lifecycle. No paid calls or sends."""
import json,uuid
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE
from test_administrator_publication import setup,material,grant_data,run
from test_knowledge_candidates import ready,candidate,confirmed_case
from test_ai_learning_mcp import _setup
import tenant_ai_administrator_approvals as approval
import tenant_ai_administrator_apply as application
import tenant_ai_administrator_drafts as drafts
import tenant_ai_learning_mcp as mcp
import tenant_ai_administrator_api as api
import tenant_instruction_library as lib
from tenant_linear_policy import TenantLinearError


def policy_data(**kw):
    return {'agent_id':AGENT,'channel_id':CA,'kinds':['knowledge','instruction','support_case'],
      'rights':['create','update','publish','rollback'],'ttl_hours':24,'max_operations':50,'max_per_hour':20,'consent_confirmed':True,**kw}


def policy(cur,**kw):return approval.training_policy_create(cur,PROFILE,policy_data(**kw))['policy_id']


def op(cur,cfg,p,action,**kw):
    return mcp._tools(cur,cfg,'administrator_training_'+action,{'policy_id':p,'request_id':str(uuid.uuid4()),**kw})


def target(result):return {k:result[k] for k in ('draft_id','object_id','version','material_hash')}


def trusted(setup,kind='knowledge'):
    db,cfg=setup;d,key=material(setup,kind)
    with db.cursor() as cur:
        if kind=='knowledge':
            g=approval.publication_grant(cur,PROFILE,grant_data(d,key));return run(cur,cfg,g)['source_ids'][0]
        row=lib.operation(PROFILE,'submit',{'expected_version':1},key,connection=db)
        lib.operation(PROFILE,'approve',{'expected_version':row['version'],'review_confirmed':True},key,connection=db)
        return key


@pytest.mark.parametrize('kind',['knowledge','instruction'])
def test_one_owner_policy_multiple_materials_update_publish_readback_rollback(setup,kind):
    db,cfg=setup;source=trusted(setup,kind)
    with db.cursor() as cur:
        p=policy(cur)
        first=op(cur,cfg,p,'create',kind=kind,source_id=source)
        second=op(cur,cfg,p,'create',kind=kind,source_id=source)
        assert first['object_id']!=second['object_id'] and first['eligible'] and second['eligible']
        updated=op(cur,cfg,p,'update',**target(first),source_id=source)
        assert updated['version']==first['version']+1
        result=op(cur,cfg,p,'publish',**target(updated))
        assert result['published'] and len(result['source_ids'])==1
        cur.execute('SELECT active,agent_id FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(result['source_ids'][0],));row=cur.fetchone()
        assert row['active'] and str(row['agent_id'])==AGENT
        cur.execute('SELECT delegation_policy_id FROM saas_ai_administrator_approvals WHERE tenant_id=%s::uuid AND draft_id=%s::uuid',(A,first['draft_id']));assert str(cur.fetchone()['delegation_policy_id'])==p
        if kind=='knowledge':
            assert op(cur,cfg,p,'create',kind='knowledge',source_id=result['source_ids'][0])['eligible']
        # Safe withdrawal is permitted even after delegation revocation.
        approval.training_policy_revoke(cur,PROFILE,p)
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'publish',**target(second))
        assert op(cur,cfg,p,'rollback',grant_id=result['grant_id'])['rolled_back']
        cur.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(result['source_ids'][0],));assert not cur.fetchone()['active']
    db.commit()


@pytest.mark.parametrize('block',['scope','tenant','agent','channel','expired','revoked','owner','kind','rights','limit','hourly'])
def test_policy_fail_closed(setup,monkeypatch,block):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:
        p=policy(cur,max_operations=1 if block=='limit' else 50,max_per_hour=1 if block=='hourly' else 20)
        if block in {'limit','hourly'}:op(cur,cfg,p,'create',kind='knowledge',source_id=source)
        elif block=='scope':cfg['granted_scopes']=[]
        elif block=='tenant':cfg['AI_LEARNING_MCP_TENANT_ID']=B;cfg['actor_id']=22
        elif block=='agent':cfg['AI_LEARNING_MCP_AGENT_ID']=str(uuid.uuid4())
        elif block=='channel':cfg['AI_LEARNING_MCP_CHANNEL_ID']=CB
        elif block=='expired':cur.execute("UPDATE saas_ai_training_policies SET expires_at=now()-interval '1 second'")
        elif block=='revoked':approval.training_policy_revoke(cur,PROFILE,p)
        elif block=='owner':cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        elif block=='kind':cur.execute("UPDATE saas_ai_training_policies SET kinds='[\"instruction\"]'::jsonb")
        elif block=='rights':cur.execute("UPDATE saas_ai_training_policies SET rights='[\"rollback\"]'::jsonb")
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'create',kind='knowledge',source_id=source)
    db.commit()


@pytest.mark.parametrize('apply_flag',['true','false'])
def test_owner_policy_is_ready_independently_of_legacy_global_apply_switch(setup,monkeypatch,apply_flag):
    """A policy is exact tenant consent, never a request to enable broad apply."""
    db,cfg=setup;source=trusted(setup)
    # Create the legacy material while its normal broad apply gate is on; the
    # assertion below then proves turning it off does not disable a policy.
    d,key=material((db,cfg))
    monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED',apply_flag)
    with db.cursor() as cur:
        p=policy(cur)
        listed=approval.training_policy_list(cur,PROFILE,cfg)
        assert listed['training_ready'] is True and listed['training_status']=='ready'
        assert listed['legacy_application_enabled']==(apply_flag=='true')
        prepared=op(cur,cfg,p,'create',kind='knowledge',source_id=source)
        published=op(cur,cfg,p,'publish',**target(prepared))
        assert published['published'] is True
        # The older per-material grant is still protected by its global gate.
        grant=approval.publication_grant(cur,PROFILE,grant_data(d,key))
        if apply_flag=='false':
            with pytest.raises(TenantLinearError):run(cur,cfg,grant)
            # The narrow bypass cannot be invoked with an ordinary receipt.
            cur.execute('SELECT id FROM saas_ai_administrator_approvals WHERE draft_id=%s::uuid',(d['id'],))
            with pytest.raises(TenantLinearError):
                application.apply_knowledge_draft(cur,PROFILE,cur.fetchone()['id'],d['id'],scoped_training_policy_id=p)
    db.commit()


def test_idempotency_and_changed_content_or_evidence_are_rejected(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:
        p=policy(cur);args={'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':source}
        first=approval.training_operation(cur,cfg,'create',args)
        assert approval.training_operation(cur,cfg,'create',args)==first
        with pytest.raises(TenantLinearError):approval.training_operation(cur,cfg,'create',{**args,'kind':'instruction'})
        cur.execute("UPDATE saas_ai_knowledge_sources SET content='Invented facts' WHERE id=%s::uuid",(first['object_id'],))
        latest=approval._publication_hash(approval._publication_object(cur,A,'knowledge',first['object_id']))
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'publish',**{**target(first),'material_hash':latest})
    db.commit()


def test_no_self_policy_or_extra_rights_and_owner_rbac(setup):
    db,cfg=setup
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):mcp._tools(cur,cfg,'administrator_training_policy_create',policy_data())
        with pytest.raises(TenantLinearError):approval.training_policy_create(cur,PROFILE,policy_data(consent_confirmed=False))
        with pytest.raises(TenantLinearError):approval.training_policy_create(cur,PROFILE,policy_data(rights=['active']))
        cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        with pytest.raises(TenantLinearError):approval.training_policy_create(cur,PROFILE,policy_data())
    db.commit()


def test_foreign_source_and_unverified_source_are_blocked(setup):
    db,cfg=setup;_,source=material(setup)
    with db.cursor() as cur:
        p=policy(cur)
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'create',kind='knowledge',source_id=source)
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'create',kind='knowledge',source_id=str(uuid.uuid4()))
    db.commit()


def test_administrator_draft_is_not_mistaken_for_a_verified_training_source(setup):
    db,cfg=setup
    with db.cursor() as cur:
        p=policy(cur)
        draft=drafts.create(cur,cfg,{'kind':'knowledge','title':'Private proposal',
          'body':{'content':'Unreviewed text','evidence_ids':[]},'target':{'agent_ids':[]}})
        with pytest.raises(TenantLinearError,match='reviewed material, not an administrator draft'):
            op(cur,cfg,p,'create',kind='knowledge',source_id=draft['id'])
        cur.execute('SELECT count(*) AS n FROM saas_ai_training_operations WHERE policy_id=%s::uuid',(p,))
        assert cur.fetchone()['n']==0
    db.commit()


def test_training_source_catalog_excludes_drafts_and_feeds_verified_create(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:
        p=policy(cur)
        draft=drafts.create(cur,cfg,{'kind':'knowledge','title':'Private proposal',
          'body':{'content':'Unreviewed text','evidence_ids':[]},'target':{'agent_ids':[]}})
        catalog=mcp._tools(cur,cfg,'administrator_training_sources',{'policy_id':p,'kind':'knowledge','limit':10})
        ids={item['source_id'] for item in catalog['items']}
        assert source in ids and draft['id'] not in ids
        selected=next(item for item in catalog['items'] if item['source_id']==source)
        assert selected['basis']['id']==source and len(selected['material_hash'])==64
        prepared=op(cur,cfg,p,'create',kind='knowledge',source_id=selected['source_id'])
        assert prepared['eligible'] and prepared['basis']==selected['basis']
        with pytest.raises(TenantLinearError):
            mcp._tools(cur,cfg,'administrator_training_sources',{'policy_id':p,'kind':'knowledge','limit':0})
    db.commit()


def test_policy_revoke_blocks_existing_unconsumed_grant(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        p=policy(cur);policy_row,_=approval.training_policy_guard(cur,cfg,p,'publish')
        g=approval.publication_grant(cur,PROFILE,grant_data(d,key),policy=policy_row)
        approval.training_policy_revoke(cur,PROFILE,p)
        with pytest.raises(TenantLinearError):run(cur,cfg,g)
    db.commit()


def case_setup(ready,monkeypatch,verified):
    if verified:ready=confirmed_case(ready)
    db,_,_=ready;_setup(monkeypatch,db);monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true');monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[mcp.DRAFT_SCOPE,mcp.PUBLISH_SCOPE]);cfg['AI_LEARNING_MCP_AGENT_ID']=AGENT
    value=candidate(ready)
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT));db.commit()
    import tenant_knowledge_candidates_store as cases
    if verified:value=cases.mutate(PROFILE,value['id'],{'expected_version':value['version'],'resolution_steps':['Проверьте соединение и повторите синхронизацию']},'edit')['candidate']
    return db,cfg,value


@pytest.mark.parametrize('verified',[True,False])
def test_historical_case_evidence_and_clarification(ready,monkeypatch,verified):
    db,cfg,value=case_setup(ready,monkeypatch,verified)
    with db.cursor() as cur:
        p=policy(cur)
        prepared=op(cur,cfg,p,'create',kind='support_case',source_id=value['id'])
        if verified:
            assert prepared['eligible']
            updated=op(cur,cfg,p,'update',**target(prepared),source_id=value['id'])
            result=op(cur,cfg,p,'publish',**target(updated));assert result['published']
            assert op(cur,cfg,p,'rollback',grant_id=result['grant_id'])['rolled_back']
        else:
            assert not prepared['eligible'] and prepared['clarification_reason']
            with pytest.raises(TenantLinearError):op(cur,cfg,p,'publish',**target(prepared))
            cur.execute('SELECT status FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],));assert cur.fetchone()['status']=='needs_clarification'
    db.commit()


def test_transaction_failure_rolls_back_internal_approvals_and_materials(setup,monkeypatch):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:p=policy(cur)
    db.commit();monkeypatch.setattr(api,'_connect',lambda:db)
    original=approval._audit
    def fail(cur,t,a,event,*args):
        if event=='ai_training_delegated_create':raise RuntimeError('synthetic receipt failure')
        return original(cur,t,a,event,*args)
    monkeypatch.setattr(approval,'_audit',fail)
    with pytest.raises(RuntimeError):api._with_connection(approval.training_operation,cfg,'create',{'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':source})
    assert not db.execute('SELECT * FROM saas_ai_training_operations')
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n']==2
    db.commit()


def test_http_single_owner_consent_then_multiple_mcp_publications(setup,monkeypatch):
    import asyncio,logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    db,cfg=setup;source=trusted(setup);db.commit()
    monkeypatch.setattr(api,'_connect',lambda:db);monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET agent_id=%s::uuid,scope=%s',(AGENT,'ai_learning.read ai_learning.publish ai_learning.drafts'));db.commit()
    async def check():
        app=web.Application();app.router.add_route('*','/owner',api.handler(lambda req:PROFILE,'https://crm.test',logging.getLogger(__name__)));app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            consent={'action':'training_policy_create',**policy_data()}
            assert (await client.post('/owner',json=consent,headers={'Origin':'https://evil.test'})).status==403
            response=await client.post('/owner',json=consent,headers={'Origin':'https://crm.test'});assert response.status==200
            p=(await response.json())['result']['policy_id']
            async def call(name,args):
                response=await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
                payload=await response.json();assert response.status==200,payload
                return json.loads(payload['result']['content'][0]['text'])
            # A draft/object identifier is not an authoritative source. It
            # must be reported as a normal MCP tool error, never a 404 that
            # makes ChatGPT terminate the session or blindly retry a write.
            invalid=await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':7,'method':'tools/call','params':{'name':'administrator_training_create','arguments':{'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':str(uuid.uuid4())}}})
            invalid_body=await invalid.json();assert invalid.status==200 and invalid_body['result']['isError'] is True
            assert json.loads(invalid_body['result']['content'][0]['text'])['error']['retry_safety']=='not_committed'
            assert not db.execute('SELECT * FROM saas_ai_training_operations')
            # A disconnect/crash after business validation but before commit
            # rolls the same request back. The caller may safely retry its
            # idempotency key and receives exactly one stored result.
            request_id=str(uuid.uuid4());args={'policy_id':p,'request_id':request_id,'kind':'knowledge','source_id':source}
            original=mcp._tool_response
            monkeypatch.setattr(mcp,'_tool_response',lambda *_: (_ for _ in ()).throw(RuntimeError('synthetic disconnect')))
            crashed=await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':8,'method':'tools/call','params':{'name':'administrator_training_create','arguments':args}})
            crash_body=await crashed.json();assert crashed.status==200 and crash_body['result']['isError'] is True
            assert json.loads(crash_body['result']['content'][0]['text'])['error']['retry_safety']=='not_committed'
            assert not db.execute('SELECT * FROM saas_ai_training_operations')
            monkeypatch.setattr(mcp,'_tool_response',original)
            prepared=await call('administrator_training_create',args)
            assert await call('administrator_training_create',args)==prepared
            for _ in range(2):
                prepared=await call('administrator_training_create',{'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':source})
                published=await call('administrator_training_publish',{'policy_id':p,'request_id':str(uuid.uuid4()),**target(prepared)})
                readback=await call('knowledge_read',{'id':published['source_ids'][0],'include_content':True})
                assert readback['items'][0]['active'] and readback['items'][0]['content']=='Verified ERP instructions'
            policies=await call('administrator_training_policies',{});assert len(policies['items'])==1 and len(policies['items'][0]['recent_operations'])==5
            response=await client.post('/owner',json={'action':'training_policy_revoke','policy_id':p},headers={'Origin':'https://crm.test'});assert response.status==200
            blocked=await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'administrator_training_create','arguments':{'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':source}}});assert blocked.status==200 and (await blocked.json())['result']['isError'] is True
            await call('administrator_training_rollback',{'policy_id':p,'request_id':str(uuid.uuid4()),'grant_id':published['grant_id']})
            db.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now()');db.commit()
            revoked=await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'administrator_training_policies','arguments':{}}});assert revoked.status==401
    asyncio.run(check())


@pytest.mark.parametrize('fixture_index',[0,1,2])
def test_real_historical_status_fixtures_cannot_become_verified_outcomes(ready,monkeypatch,fixture_index):
    from pathlib import Path
    from test_knowledge_candidates import model_card
    fixture=json.loads((Path(__file__).parent/'fixtures/ai_training_historical_redacted.json').read_text(encoding='utf-8'))['cases'][fixture_index]
    db,generation,_=ready
    card=model_card(confirmed=False);card['outcome']['status']=fixture['outcome_status'];card['work_status']=fixture['work_status'];card['cause']['status']=fixture['cause_status'];card['resolution_method']=fixture['resolution_method'];card['resolution_steps']=[]
    generation.return_value=(json.dumps({'cards':[card]},ensure_ascii=False),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    db,cfg,value=case_setup(ready,monkeypatch,False)
    with db.cursor() as cur:
        p=policy(cur);prepared=op(cur,cfg,p,'create',kind='support_case',source_id=value['id'])
        assert not prepared['eligible'] and prepared['clarification_reason']=='confirmation_required'
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'publish',**target(prepared))
    db.commit()


def test_transitive_evidence_withdrawal_and_draft_injection_are_blocked(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:
        p=policy(cur);prepared=op(cur,cfg,p,'create',kind='knowledge',source_id=source)
        result=op(cur,cfg,p,'publish',**target(prepared))
        cur.execute('UPDATE saas_ai_knowledge_sources SET active=FALSE WHERE id=%s::uuid',(source,))
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'create',kind='knowledge',source_id=result['source_ids'][0])
    db.commit()


def test_company_policy_is_explicit_unbounded_revocable_and_tenant_scoped(setup):
    db,cfg=setup
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):approval.training_company_policy_create(cur,PROFILE,{'consent_confirmed':False})
        policy_id=company_policy(cur,cfg)
        row=db.execute('SELECT scope_kind,agent_id,channel_id,max_operations,max_per_hour,expires_at FROM saas_ai_training_policies WHERE id=%s::uuid',(policy_id,))[0]
        assert row=={'scope_kind':'company','agent_id':None,'channel_id':None,'max_operations':None,'max_per_hour':None,'expires_at':None}
        with pytest.raises(TenantLinearError):approval.training_company_policy_create(cur,PROFILE,{'consent_confirmed':True})
        other_cfg={**cfg,'AI_LEARNING_MCP_TENANT_ID':B,'actor_id':22}
        with pytest.raises(TenantLinearError):approval.training_policy_guard(cur,other_cfg,policy_id,'publish',AGENT,CA)
        approval.training_policy_revoke(cur,PROFILE,policy_id)
        with pytest.raises(TenantLinearError):approval.training_policy_guard(cur,cfg,policy_id,'publish',AGENT,CA)
        assert approval.training_policy_list(cur,PROFILE,cfg)['training_status']=='owner_consent_required'
    db.commit()


def test_company_policy_publishes_one_exact_owner_draft_to_one_selected_agent(setup):
    db,cfg=setup
    second=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by,enabled,mode,role,scopes,expert_policy,model) VALUES(%s::uuid,%s::uuid,'Second company agent',11,TRUE,'automatic','primary',%s::jsonb,'{}'::jsonb,'test-model')",(second,A,json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':False}])))
    with db.cursor() as cur:
        policy_id=company_policy(cur,cfg)
        proposal=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Owner supplied rule','content':'Use lot number before stock count.','owner_note':'Owner provided this rule.'})
        listed=approval.training_sources(cur,cfg,{'policy_id':policy_id,'kind':'owner_knowledge_draft','target_agent_id':second,'target_channel_id':CA})
        item=next(x for x in listed['items'] if x['source_id']==proposal['id'])
        prepared=approval.training_operation(cur,cfg,'create',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':proposal['id'],'source_version':proposal['version'],'source_hash':item['source_hash'],'target_agent_id':second,'target_channel_id':CA})
        assert prepared['eligible'] and prepared['basis']['kind']=='owner_provided_draft'
        cur.execute('SELECT active,agent_id,provenance FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(prepared['object_id'],));private=cur.fetchone()
        assert private['active'] is False and private['agent_id'] is None
        assert private['provenance']['review_status']=='owner_delegated_owner_provided' and 'approved_by' not in private['provenance']
        changed=dict(target(prepared),target_agent_id=second,target_channel_id=CA)
        published=approval.training_operation(cur,cfg,'publish',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),**changed})
        assert published['published'] and len(published['source_ids'])==1
        cur.execute('SELECT active,agent_id,provenance FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(published['source_ids'][0],));live=cur.fetchone()
        assert live['active'] is True and str(live['agent_id'])==second
        assert live['provenance']['review_status']=='owner_delegated_owner_provided' and 'approved_by' not in live['provenance']
        assert live['provenance']['owner_provided_draft_hash']==item['source_hash']
        with pytest.raises(TenantLinearError):approval.training_operation(cur,cfg,'publish',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),**{**changed,'target_channel_id':CB}})
        approval.training_policy_revoke(cur,PROFILE,policy_id)
        with pytest.raises(TenantLinearError):approval.training_operation(cur,cfg,'publish',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),**changed})
        rolled=approval.training_operation(cur,cfg,'rollback',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),'grant_id':published['grant_id']})
        assert rolled['rolled_back']
    db.commit()


def test_company_owner_draft_hash_mutation_and_unlimited_operation_receipts(setup):
    db,cfg=setup
    with db.cursor() as cur:
        policy_id=company_policy(cur,cfg)
        proposal=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Exact owner draft','content':'Count the stock by lot.','owner_note':'Owner supplied.'})
        listed=approval.training_sources(cur,cfg,{'policy_id':policy_id,'kind':'owner_knowledge_draft','target_agent_id':AGENT,'target_channel_id':CA})
        item=listed['items'][0]
        db.execute('''INSERT INTO saas_ai_training_operations(policy_id,request_id,action,request_hash,result)
          SELECT %s::uuid,gen_random_uuid(),'historical','x','{}'::jsonb FROM generate_series(1,1005)''',(policy_id,))
        db.execute("UPDATE saas_ai_administrator_drafts SET body=jsonb_set(body,'{content}','\"Changed after listing\"'::jsonb) WHERE id=%s::uuid",(proposal['id'],))
        with pytest.raises(TenantLinearError):approval.training_operation(cur,cfg,'create',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':proposal['id'],'source_version':proposal['version'],'source_hash':item['source_hash'],'target_agent_id':AGENT,'target_channel_id':CA})
        assert approval.training_policy_list(cur,PROFILE,cfg)['items'][0]['active']
        assert db.execute('SELECT max_operations,max_per_hour,expires_at FROM saas_ai_training_policies WHERE id=%s::uuid',(policy_id,))[0]['expires_at'] is None
    db.commit()


def test_company_policy_publishes_one_evidence_checked_case_as_delegated(ready,monkeypatch):
    db,cfg,value=case_setup(ready,monkeypatch,True)
    with db.cursor() as cur:
        policy_id=company_policy(cur,cfg)
        prepared=approval.training_operation(cur,cfg,'create',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),
          'kind':'support_case','source_id':value['id'],'target_agent_id':AGENT,'target_channel_id':CA})
        assert prepared['eligible']
        args={'policy_id':policy_id,'request_id':str(uuid.uuid4()),**target(prepared),'target_agent_id':AGENT,'target_channel_id':CA}
        published=approval.training_operation(cur,cfg,'publish',args)
        assert published['published'] and len(published['source_ids'])==1
        source=db.execute('SELECT provenance,active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(published['source_ids'][0],))[0]
        assert source['active'] and source['provenance']['authorization_mode']=='owner_delegated_policy'
        assert source['provenance']['delegation_policy_id']==policy_id and 'approved_by' not in source['provenance']
        candidate=db.execute('SELECT reviewed_by,status FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],))[0]
        assert candidate['status']=='approved' and candidate['reviewed_by'] is None
        approval.training_policy_revoke(cur,PROFILE,policy_id)
        assert approval.training_operation(cur,cfg,'rollback',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),'grant_id':published['grant_id']})['rolled_back']
    db.commit()


def test_company_policy_instruction_is_logged_as_delegated_not_individually_approved(setup):
    db,cfg=setup;source=trusted(setup,'instruction')
    with db.cursor() as cur:
        policy_id=company_policy(cur,cfg)
        prepared=approval.training_operation(cur,cfg,'create',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),
          'kind':'instruction','source_id':source,'target_agent_id':AGENT,'target_channel_id':CA})
        published=approval.training_operation(cur,cfg,'publish',{'policy_id':policy_id,'request_id':str(uuid.uuid4()),**target(prepared),
          'target_agent_id':AGENT,'target_channel_id':CA})
        assert published['published']
        source_row=db.execute('SELECT provenance FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(published['source_ids'][0],))[0]
        assert source_row['provenance']['authorization_mode']=='owner_delegated_policy'
        assert source_row['provenance']['delegation_policy_id']==policy_id and 'approved_by' not in source_row['provenance']
        assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='instruction_delegated_approval'")[0]['n']==1
    db.commit()


def company_policy(cur,cfg):
    # Same identity/connection fields the HTTP authenticator derives, with a
    # real disposable token row rechecked and locked by the mutation guard.
    connection=str(uuid.uuid4())
    cur.execute("UPDATE saas_ai_learning_mcp_tokens SET agent_id=%s::uuid,oauth_connection_id=%s::uuid,scope=%s WHERE tenant_id=%s::uuid RETURNING id,token_hash",(cfg['AI_LEARNING_MCP_AGENT_ID'],connection,' '.join([mcp.TOOL_SCOPE,drafts.DRAFT_SCOPE,approval.PUBLISH_SCOPE]),A))
    row=cur.fetchone()
    cfg.update(oauth_token_id=str(row['id']),oauth_connection_id=connection,oauth_token_hash=row['token_hash'])
    return approval.training_company_policy_create(cur,PROFILE,{'consent_confirmed':True})['policy_id']


@pytest.mark.parametrize('kind',['knowledge','instruction'])
def test_company_reviewed_source_path_and_truthful_receipt(setup,kind):
    db,cfg=setup;source=trusted(setup,kind)
    with db.cursor() as cur:
        policy_id=company_policy(cur,cfg)
        listed=mcp._tools(cur,cfg,'administrator_training_sources',{'policy_id':policy_id,'kind':kind,'target_agent_id':AGENT,'target_channel_id':CA})
        assert source in [v['source_id'] for v in listed['items']]
        prepared=op(cur,cfg,policy_id,'create',kind=kind,source_id=source,target_agent_id=AGENT,target_channel_id=CA)
        receipt=db.execute('SELECT id,approved_by,delegation_policy_id FROM saas_ai_administrator_approvals WHERE draft_id=%s::uuid',(prepared['draft_id'],))[0]
        assert receipt['approved_by'] is None and str(receipt['delegation_policy_id'])==policy_id
        assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE entity_id=%s AND action='ai_administrator_approval_approved'",(str(receipt['id']),))[0]['n']==0
        published=op(cur,cfg,policy_id,'publish',**target(prepared),target_agent_id=AGENT,target_channel_id=CA)
        provenance=db.execute('SELECT provenance FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(published['source_ids'][0],))[0]['provenance']
        assert 'approved_by' not in provenance and provenance['authorization_mode']=='owner_delegated_policy'
    db.commit()


def evidence_draft(cur,cfg):
    cur.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s LIMIT 1',(A,'connect:'+CA))
    message=str(cur.fetchone()['id'])
    draft=drafts.typed_create(cur,cfg,'knowledge',{'title':'Full instruction draft','content':'Evidence-backed guidance. Owner addition: check stock. Caveat: outcome remains unconfirmed.','evidence_ids':[message],'agent_ids':[]})
    return draft,message


@pytest.mark.parametrize('mutation',[None,'draft','evidence','revoked','oauth','target_archived'])
def test_company_ordinary_evidence_draft_exact_review_and_withdrawal(setup,monkeypatch,mutation):
    from types import SimpleNamespace
    db,cfg=setup
    with db.cursor() as cur:
        policy_id=company_policy(cur,cfg)
        draft,message=evidence_draft(cur,cfg)
        items=mcp._tools(cur,cfg,'administrator_training_owner_drafts',{'policy_id':policy_id,'target_agent_id':AGENT,'target_channel_id':CA})['items']
        item=next(v for v in items if v['source_id']==draft['id'])
        assert item['basis']['kind']=='evidence_backed_draft'
        prepared=op(cur,cfg,policy_id,'create',kind='knowledge',source_id=draft['id'],source_version=draft['version'],source_hash=item['source_hash'],target_agent_id=AGENT,target_channel_id=CA)
    db.commit()
    if mutation=='oauth':
        monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
        cfg=mcp._authorize_token(SimpleNamespace(headers={'Authorization':'Bearer read-only-test-token'}))
    with db.cursor() as cur:
        if mutation=='draft':cur.execute("UPDATE saas_ai_administrator_drafts SET body=jsonb_set(body,'{content}','\"mutated\"'::jsonb) WHERE id=%s::uuid",(draft['id'],))
        elif mutation=='evidence':cur.execute("UPDATE saas_crm_messages SET body='mutated source' WHERE id=%s::uuid",(message,))
        elif mutation=='revoked':approval.training_policy_revoke(cur,PROFILE,policy_id)
        elif mutation=='oauth':cur.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now() WHERE id=%s::uuid',(cfg['oauth_token_id'],))
        if mutation in {'draft','evidence','revoked','oauth'}:
            with pytest.raises(TenantLinearError):op(cur,cfg,policy_id,'publish',**target(prepared),target_agent_id=AGENT,target_channel_id=CA)
            assert db.execute('SELECT count(*) AS n FROM saas_ai_administrator_publication_grants WHERE policy_id=%s::uuid',(policy_id,))[0]['n']==0
        else:
            published=op(cur,cfg,policy_id,'publish',**target(prepared),target_agent_id=AGENT,target_channel_id=CA)
            source=db.execute('SELECT content,provenance FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(published['source_ids'][0],))[0]
            assert 'Owner addition' in source['content'] and 'unconfirmed' in source['content']
            assert source['provenance']['review_status']=='owner_delegated_evidence_draft'
            assert 'approved_by' not in source['provenance'] and 'owner_provided_draft_hash' not in source['provenance']
            if mutation=='target_archived':
                cur.execute('UPDATE saas_ai_agents SET archived_at=now() WHERE id=%s::uuid',(AGENT,))
                cur.execute('DELETE FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA))
            approval.training_policy_revoke(cur,PROFILE,policy_id)
            assert op(cur,cfg,policy_id,'rollback',grant_id=published['grant_id'])['rolled_back']
    db.commit()


def test_company_draft_preview_hash_binds_evidence_before_prepare(setup):
    db,cfg=setup
    with db.cursor() as cur:
        p=company_policy(cur,cfg);draft,message=evidence_draft(cur,cfg)
        args={'policy_id':p,'target_agent_id':AGENT,'target_channel_id':CA}
        item=next(v for v in mcp._tools(cur,cfg,'administrator_training_owner_drafts',args)['items'] if v['source_id']==draft['id'])
        with pytest.raises(TenantLinearError):mcp._tools(cur,{**cfg,'granted_scopes':[approval.PUBLISH_SCOPE]},'administrator_training_owner_drafts',args)
        cur.execute("UPDATE saas_crm_messages SET body='Changed before preparation' WHERE id=%s::uuid",(message,))
        with pytest.raises(TenantLinearError,match='evidence changed'):
            op(cur,cfg,p,'create',kind='knowledge',source_id=draft['id'],source_version=draft['version'],source_hash=item['source_hash'],target_agent_id=AGENT,target_channel_id=CA)
        assert db.execute('SELECT count(*) AS n FROM saas_ai_training_operations WHERE policy_id=%s::uuid',(p,))[0]['n']==0
    db.commit()


def test_company_unconfirmed_case_remains_unconfirmed(ready,monkeypatch):
    db,cfg,value=case_setup(ready,monkeypatch,False)
    before=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],))[0]['content']
    with db.cursor() as cur:
        p=company_policy(cur,cfg)
        prepared=op(cur,cfg,p,'create',kind='support_case',source_id=value['id'],target_agent_id=AGENT,target_channel_id=CA)
        assert not prepared['eligible'] and prepared['clarification_reason']
        with pytest.raises(TenantLinearError):op(cur,cfg,p,'publish',**target(prepared),target_agent_id=AGENT,target_channel_id=CA)
        after=db.execute('SELECT content,status FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],))[0]
        assert after['status']=='needs_clarification'
        assert after['content']['outcome']==before['outcome']
        assert after['content'].get('work_status')==before.get('work_status')
    db.commit()


@pytest.mark.parametrize('mutation',[None,'media_identity','external_identity','receipt_result','receipt_coverage','receipt_provenance','receipt_withdrawn'])
def test_company_video_draft_binds_saved_receipt_and_attachment_version(setup,monkeypatch,mutation):
    import tenant_media_evidence as media
    db,cfg=setup
    media.create_schema(db)
    raw={'mediaId':'original-media','fileLength':100,'url':'https://private.example/attachment?token=secret'}
    with db.cursor() as cur:
        p=company_policy(cur,cfg)
        cur.execute('SELECT id,body FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s LIMIT 1',(A,'connect:'+CA))
        message=cur.fetchone();message_id=str(message['id']);body=message['body']
        external='synthetic-media-external'
        cur.execute("UPDATE saas_crm_messages SET message_type='video',external_id=%s,raw=%s::jsonb WHERE id=%s::uuid",(external,json.dumps(raw),message_id))
        ref={'id':message_id,'channel_id':CA,'external_id':external,'body_hash':media._body_hash(body),'media_kind':'video','media_version':media.media_version(raw),'role':'customer'}
        media.persist(cur,A,[ref],[{'id':message_id,'kind':'video','status':'error','code':'visual_provider_failed','audio_status':'processed','transcript':'Saved ASR v1','failure_stage':'visual_analysis'}])
        draft=drafts.typed_create(cur,cfg,'knowledge',{'title':'Partial video guidance','content':'Saved audio guidance; partial audio only, visual analysis failed. Outcome unconfirmed. Owner addition: check stock.','evidence_ids':[message_id],'agent_ids':[]})
        def no_processing(*args,**kwargs):raise AssertionError('Publication must only read the existing receipt in its transaction')
        monkeypatch.setattr(media,'reusable',no_processing)
        monkeypatch.setattr(media,'_connect',no_processing)
        args={'policy_id':p,'target_agent_id':AGENT,'target_channel_id':CA}
        listing=mcp._tools(cur,cfg,'administrator_training_owner_drafts',args)
        item=next(v for v in listing['items'] if v['source_id']==draft['id'])
        output=json.dumps(listing)
        assert external not in output and raw['url'] not in output and 'Saved ASR v1' not in output
        prepared=op(cur,cfg,p,'create',kind='knowledge',source_id=draft['id'],source_version=draft['version'],source_hash=item['source_hash'],target_agent_id=AGENT,target_channel_id=CA)
        if mutation=='media_identity':cur.execute("UPDATE saas_crm_messages SET raw=jsonb_set(raw,'{mediaId}','\"replacement\"'::jsonb) WHERE id=%s::uuid",(message_id,))
        elif mutation=='external_identity':cur.execute("UPDATE saas_crm_messages SET external_id='replacement-external' WHERE id=%s::uuid",(message_id,))
        elif mutation=='receipt_result':cur.execute("UPDATE saas_media_evidence_receipts SET result=jsonb_set(result,'{transcript}','\"Changed ASR\"'::jsonb) WHERE tenant_id=%s::uuid AND external_id=%s",(A,external))
        elif mutation=='receipt_coverage':cur.execute("UPDATE saas_media_evidence_receipts SET coverage=jsonb_set(coverage,'{audio}','\"unavailable_or_error\"'::jsonb) WHERE tenant_id=%s::uuid AND external_id=%s",(A,external))
        elif mutation=='receipt_provenance':cur.execute("UPDATE saas_media_evidence_receipts SET provenance='{}'::jsonb WHERE tenant_id=%s::uuid AND external_id=%s",(A,external))
        elif mutation=='receipt_withdrawn':cur.execute('DELETE FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,external))
        assert db.execute('SELECT body FROM saas_crm_messages WHERE id=%s::uuid',(message_id,))[0]['body']==body
        if mutation:
            with pytest.raises(TenantLinearError,match='evidence changed'):
                op(cur,cfg,p,'publish',**target(prepared),target_agent_id=AGENT,target_channel_id=CA)
            assert db.execute('SELECT count(*) AS n FROM saas_ai_administrator_publication_grants WHERE policy_id=%s::uuid',(p,))[0]['n']==0
        else:
            before=db.execute('SELECT * FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,external))[0]
            published=op(cur,cfg,p,'publish',**target(prepared),target_agent_id=AGENT,target_channel_id=CA)
            source=db.execute('SELECT content,provenance FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(published['source_ids'][0],))[0]
            assert source['content']==item['preview']['content']
            assert 'partial audio only, visual analysis failed' in source['content']
            assert source['provenance']['review_status']=='owner_delegated_evidence_draft'
            assert db.execute('SELECT * FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,external))[0]==before
    db.commit()
