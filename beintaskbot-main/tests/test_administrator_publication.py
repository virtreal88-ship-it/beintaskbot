"""Real isolated PostgreSQL/WASM publication; no providers or production writes."""
import json
import uuid
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_simulation
from test_ai_learning_mcp import _setup
from test_instruction_library import doc
from test_knowledge_candidates import ready
import tenant_ai_administrator_approvals as approvals
import tenant_ai_administrator_drafts as drafts
import tenant_ai_administrator_apply as apply
import tenant_ai_learning_mcp as mcp
import tenant_instruction_library as lib
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def setup(db,monkeypatch):
    seed_simulation(db);_setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE,approvals.PUBLISH_SCOPE])
    cfg['AI_LEARNING_MCP_AGENT_ID']=AGENT
    lib.schema(db)
    db.commit()
    return db,cfg


def material(setup,kind='knowledge'):
    db,cfg=setup
    with db.cursor() as cur:
        if kind=='instruction':
            asset=str(uuid.uuid4())
            cur.execute('INSERT INTO saas_instruction_assets VALUES(%s::uuid,%s::uuid,%s,100,100,%s)',(A,asset,b'jpeg','a'*64))
            d=drafts.typed_create(cur,cfg,'instruction',{'title':'Test lesson','doc':doc(asset)})
        else:
            d=drafts.create(cur,cfg,{'kind':'knowledge','title':'Verified guidance','body':{'content':'Verified ERP instructions','evidence_ids':[]},'target':{'agent_ids':[]}})
        receipt=approvals.request(cur,PROFILE,d['id'],d['version']);approvals.approve(cur,PROFILE,receipt['id'],'approve')
        result=(apply.apply_instruction_draft if kind=='instruction' else apply.apply_knowledge_draft)(cur,PROFILE,receipt['id'],d['id'])
    key=result.get('lesson_id') or result['source_id']
    with db.cursor() as cur: d['_publication_hash']=approvals._publication_hash(approvals._publication_object(cur,A,kind,key))
    return d,key


def grant_data(d,key,kind='knowledge'):
    return dict(material_hash=d['_publication_hash'],draft_id=d['id'],version=d['version'],kind=kind,object_id=key,agent_id=AGENT,channel_id=CA,ttl_minutes=30,review_confirmed=True)


def run(cur,cfg,g,rollback=False):
    return mcp._tools(cur,cfg,'administrator_rollback_publication' if rollback else 'administrator_publish_reviewed',{'grant_id':g['grant_id']})


@pytest.mark.parametrize('kind',['knowledge','instruction'])
def test_publication_readback_idempotency_and_rollback(setup,kind):
    db,cfg=setup;d,key=material(setup,kind)
    with db.cursor() as cur:
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,key,kind))
        result=run(cur,cfg,g)
        assert result['published'] and not result['public_link_created'] and len(result['source_ids'])==1
        assert run(cur,cfg,g)==result
        cur.execute('SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,result['source_ids'][0]));source=cur.fetchone()
        assert source['active'] and str(source['agent_id'])==AGENT
        assert source['provenance']['approved_by']==11
        if kind=='instruction':
            cur.execute('SELECT state,public_hash FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,key));lesson=cur.fetchone()
            assert lesson['state']=='published' and lesson['public_hash'] is None
        assert run(cur,cfg,g,True)['rolled_back']
        assert run(cur,cfg,g,True)['rolled_back']
        cur.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(result['source_ids'][0],));assert not cur.fetchone()['active']
        with pytest.raises(TenantLinearError):run(cur,cfg,g)
    db.commit()


@pytest.mark.parametrize('block',['scope','tenant','agent','channel','revoked','expired','draft','material','disabled','owner'])
def test_fail_closed_before_publication(setup,monkeypatch,block):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,key))
        if block=='scope':cfg['granted_scopes']=[]
        elif block=='tenant':cfg['AI_LEARNING_MCP_TENANT_ID']=B;cfg['actor_id']=22
        elif block=='agent':cfg['AI_LEARNING_MCP_AGENT_ID']=str(uuid.uuid4())
        elif block=='channel':cfg['AI_LEARNING_MCP_CHANNEL_ID']=CB
        elif block=='revoked':approvals.publication_revoke(cur,PROFILE,g['grant_id'])
        elif block=='expired':cur.execute("UPDATE saas_ai_administrator_publication_grants SET expires_at=now()-interval '1 second'")
        elif block=='draft':cur.execute('UPDATE saas_ai_administrator_drafts SET version=version+1 WHERE id=%s::uuid',(d['id'],))
        elif block=='material':cur.execute("UPDATE saas_ai_knowledge_sources SET content='Changed after review' WHERE id=%s::uuid",(key,))
        elif block=='disabled':monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','false')
        elif block=='owner':cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        with pytest.raises(TenantLinearError):run(cur,cfg,g)
        cur.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources WHERE active');assert cur.fetchone()['n']==0
    db.commit()


def test_no_self_grant_and_explicit_owner_review_required(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):mcp._tools(cur,cfg,'administrator_publication_grant',grant_data(d,key))
        data=grant_data(d,key);data['review_confirmed']=False
        with pytest.raises(TenantLinearError):approvals.publication_grant(cur,PROFILE,data)
        with pytest.raises(TenantLinearError):approvals.publication_grant(cur,{'tenant_id':B,'telegram_id':22},grant_data(d,key))
    db.commit()


def test_rollback_conflict_and_scope_listing(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,key));result=run(cur,cfg,g)
        assert len(mcp._tools(cur,cfg,'administrator_publication_grants',{})['items'])==1
        cur.execute("UPDATE saas_ai_knowledge_sources SET title='Edited' WHERE id=%s::uuid",(result['source_ids'][0],))
        with pytest.raises(TenantLinearError):run(cur,cfg,g,True)
        cfg['AI_LEARNING_MCP_CHANNEL_ID']=CB
        assert not mcp._tools(cur,cfg,'administrator_publication_grants',{})['items']
    db.commit()


def test_publish_scope_discovery_and_strict_schema():
    assert approvals.PUBLISH_SCOPE in mcp._requested_scopes('ai_learning.read ai_learning.publish')
    for tool in approvals.publication_definitions():
        assert tool['inputSchema']['additionalProperties'] is False
        assert tool['securitySchemes'][0]['scopes']==[approvals.PUBLISH_SCOPE]
    assert 'owner-session grant' in mcp._consent_text({approvals.PUBLISH_SCOPE})[0]



def test_owner_preview_hash_prevents_review_race_and_duplicate_grants(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        options=approvals.publication_options(cur,PROFILE,d['id'])
        assert options['materials'][0]['material_hash']==d['_publication_hash']
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,key))
        assert approvals.publication_grant(cur,PROFILE,grant_data(d,key))==g
        cur.execute("UPDATE saas_ai_knowledge_sources SET title='Changed during review' WHERE id=%s::uuid",(key,))
        with pytest.raises(TenantLinearError):approvals.publication_grant(cur,PROFILE,grant_data(d,key))
    db.commit()


def test_publication_failure_rolls_back_review_sources_and_grant(setup,monkeypatch):
    db,cfg=setup;d,key=material(setup,'instruction')
    with db.cursor() as cur:g=approvals.publication_grant(cur,PROFILE,grant_data(d,key,'instruction'))
    db.commit()
    import tenant_ai_administrator_api as api
    monkeypatch.setattr(api,'_connect',lambda:db)
    original=approvals._audit
    def fail(cur,tenant,actor,event,*args):
        if event=='ai_administrator_material_published':raise RuntimeError('synthetic audit failure')
        return original(cur,tenant,actor,event,*args)
    monkeypatch.setattr(approvals,'_audit',fail)
    with pytest.raises(RuntimeError):api._with_connection(approvals.publication_execute,cfg,g['grant_id'])
    assert db.execute('SELECT state,version FROM saas_instruction_lessons WHERE id=%s::uuid',(key,))[0]=={'state':'draft','version':1}
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources WHERE active')
    assert db.execute('SELECT state FROM saas_ai_administrator_publication_grants')[0]['state']=='granted'
    db.commit()


def test_support_case_real_evidence_publish_and_archive_rollback(ready,monkeypatch):
    from test_knowledge_candidates import confirmed_case,candidate
    ready=confirmed_case(ready);db,_,_=ready
    _setup(monkeypatch,db);monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true');monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,approvals.PUBLISH_SCOPE]);cfg['AI_LEARNING_MCP_AGENT_ID']=AGENT
    value=candidate(ready)
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT));db.commit()
    with db.cursor() as cur:
        d=drafts.typed_create(cur,cfg,'support_case',{'title':'Reviewed case','problem':'Synthetic connectivity issue','evidence_message_ids':value['source_message_ids']})
        d['_publication_hash']=approvals._publication_hash(approvals._publication_object(cur,A,'support_case',value['id']))
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,value['id'],'support_case'))
        result=run(cur,cfg,g);assert result['published'] and len(result['source_ids'])==1
        cur.execute('SELECT status FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],));assert cur.fetchone()['status']=='approved'
        assert run(cur,cfg,g)==result
        assert run(cur,cfg,g,True)['rolled_back']
        cur.execute('SELECT status FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],));assert cur.fetchone()['status']=='archived'
        cur.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(result['source_ids'][0],));assert not cur.fetchone()['active']
    db.commit()


def test_uncertain_case_cannot_be_approved_by_delegation(ready,monkeypatch):
    from test_knowledge_candidates import candidate
    db,_,_=ready;_setup(monkeypatch,db);monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE]);cfg['AI_LEARNING_MCP_AGENT_ID']=AGENT
    value=candidate(ready)
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT));db.commit()
    with db.cursor() as cur:
        d=drafts.typed_create(cur,cfg,'support_case',{'title':'Uncertain case','problem':'Synthetic problem','evidence_message_ids':value['source_message_ids']})
        d['_publication_hash']=approvals._publication_hash(approvals._publication_object(cur,A,'support_case',value['id']))
        with pytest.raises(TenantLinearError):approvals.publication_grant(cur,PROFILE,grant_data(d,value['id'],'support_case'))
        cur.execute('SELECT id FROM saas_ai_administrator_publication_grants');assert not cur.fetchall()
    db.commit()


def test_http_owner_grant_mcp_publish_persist_and_oauth_revocation(setup,monkeypatch):
    import asyncio,logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_ai_administrator_api as api
    db,cfg=setup;d,key=material(setup);db.commit()
    monkeypatch.setattr(api,'_connect',lambda:db)
    monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET agent_id=%s::uuid,scope=%s',(AGENT,'ai_learning.read ai_learning.publish'));db.commit()
    async def check():
        app=web.Application();app.router.add_route('*','/approvals',api.handler(lambda req:PROFILE,'https://crm.test',logging.getLogger(__name__)));app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            args={'action':'publication_grant',**grant_data(d,key)}
            denied=await client.post('/approvals',headers={'Origin':'https://evil.test'},json=args);assert denied.status==403
            approved=await client.post('/approvals',headers={'Origin':'https://crm.test'},json=args);assert approved.status==200
            g=(await approved.json())['result']
            async def call():return await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'administrator_publish_reviewed','arguments':{'grant_id':g['grant_id']}}})
            response=await call();assert response.status==200
            publication=json.loads((await response.json())['result']['content'][0]['text']);assert publication['published']
            readback=await client.post('/mcp',headers={'Authorization':'Bearer read-only-test-token'},json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'knowledge_read','arguments':{'id':publication['source_ids'][0],'include_content':True}}})
            assert readback.status==200
            source=json.loads((await readback.json())['result']['content'][0]['text'])['items'][0]
            assert source['active'] and source['content']=='Verified ERP instructions'
            assert db.execute('SELECT state FROM saas_ai_administrator_publication_grants')[0]['state']=='consumed';db.commit()
            assert (await call()).status==200
            db.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now()');db.commit()
            assert (await call()).status==401
    asyncio.run(check())


def test_rollback_rejects_new_dependencies(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,key));run(cur,cfg,g)
        from tenant_ai_agents_store import save_knowledge
        added=save_knowledge(PROFILE,{'title':'Later dependency','kind':'text','content':'Later knowledge','active':False,'agent_id':AGENT},connection=db)
        cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE id=%s::uuid',(json.dumps({'publication_grant_id':g['grant_id']}),added['id']))
        with pytest.raises(TenantLinearError):run(cur,cfg,g,True)
    db.commit()


def test_multichannel_agent_cannot_expand_publication_scope(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        cur.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':CA},{'provider':'connect','channel_id':CB}]),AGENT))
        with pytest.raises(TenantLinearError):approvals.publication_grant(cur,PROFILE,grant_data(d,key))
    db.commit()


def test_original_draft_rollback_cannot_orphan_publication(setup):
    db,cfg=setup;d,key=material(setup)
    with db.cursor() as cur:
        cur.execute('SELECT approval_id FROM saas_ai_administrator_material_rollbacks WHERE object_id=%s::uuid',(key,));receipt=cur.fetchone()['approval_id']
        g=approvals.publication_grant(cur,PROFILE,grant_data(d,key))
        with pytest.raises(TenantLinearError):apply.rollback_knowledge_draft(cur,PROFILE,receipt)
        run(cur,cfg,g)
        with pytest.raises(TenantLinearError):apply.rollback_knowledge_draft(cur,PROFILE,receipt)
        run(cur,cfg,g,True)
        assert apply.rollback_knowledge_draft(cur,PROFILE,receipt)['rolled_back']
    db.commit()
