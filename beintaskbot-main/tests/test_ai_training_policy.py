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
