"""Existing OAuth grants: bounded company reads, no privilege or secret leaks."""
import asyncio
import hashlib
import json
import uuid

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer

from test_tenant_support_inbox import A,B,CA,AGENT,PROFILE,database,db,seed_simulation,seed_simulation_run
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
import tenant_ai_administrator_read as admin
import tenant_ai_administrator_drafts as drafts
import tenant_ai_administrator_approvals as approvals
import tenant_ai_administrator_observations as observations
import tenant_ai_administrator_apply as apply
import tenant_ai_administrator_api as administrator_api
import tenant_ai_owner_review_component as owner_component
import tenant_instruction_library as instruction_library
from tenant_ai_testing_schema import create_schema as create_testing_schema


def prompt_body_with_completed_test(cur, version, instructions='Use verified evidence only.'):
    session_id,turn_id=uuid.uuid4(),uuid.uuid4()
    create_testing_schema(cur.connection)
    cur.execute('''INSERT INTO saas_ai_test_sessions(tenant_id,id,agent_id,created_by,scope)
      VALUES(%s::uuid,%s,%s::uuid,11,'{}'::jsonb)''',(A,session_id,AGENT))
    cur.execute('''INSERT INTO saas_ai_test_turns(tenant_id,id,session_id,input_hash,client_text,response,state,snapshot)
      VALUES(%s::uuid,%s,%s,%s,'synthetic input','synthetic response','completed',%s::jsonb)''',
      (A,turn_id,session_id,'test:'+str(turn_id),json.dumps({'agent_version':version})))
    return {'agent_id':AGENT,'expected_version':version,'instructions':instructions,'testing_turn_id':str(turn_id)}


def test_existing_connection_company_reads_and_immediate_owner_revocation(db,monkeypatch):
    token=_setup(monkeypatch,db)
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            async def call(name,args={}):
                return await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
            result=await call('channels_read')
            assert result.status==200
            payload=json.loads((await result.json())['result']['content'][0]['text'])
            assert len(payload['items'])==1 and payload['items'][0]['connect_channel_id']==CA
            denied=await call('channels_read',{'tenant_id':B});assert denied.status==400
            db.execute("UPDATE saas_tenant_members SET role='operator' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
            assert (await call('channels_read')).status==403
    asyncio.run(check())


def test_all_read_projections_are_valid_bounded_and_tenant_scoped(db,monkeypatch):
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    for name in admin.SPECS:
        with db.cursor() as cur:
            if name=='system_status_read':
                status=admin.read(cur,cfg,name,{})
                assert status['writes_enabled'] is False
                assert status['flags']['AI_LEARNING_MCP_DRAFTS_ENABLED'] is False
                assert status['flags']['AI_AUTONOMOUS_APPLY_ENABLED'] is False
                assert status['flags']['AI_AUTONOMOUS_IMPROVEMENT_ENABLED'] is False
                continue
            if name=='owner_management_eligibility_read':
                status=admin.read(cur,cfg,name,{})
                assert status['read_only'] is True
                assert status['chat_text_can_approve'] is False
                knowledge=admin.read(cur,cfg,name,{'material_kind':'knowledge'})
                assert knowledge['direct_link']=='unavailable_without_the_existing_owner_route_resolver'
                assert any(item.get('reason')=='requires_reviewed_source_and_exact_publication_grant' for item in knowledge['reasons'])
                continue
            if name=='simulation_queue_diagnostic_read':
                continue
            result=admin.read(cur,cfg,name,{'limit':1,'include_content':True})
            assert len(result['items'])<=1
            assert B not in json.dumps(result)
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']==2


def test_message_content_redacts_secrets_and_preserves_ids(db,monkeypatch):
    _setup(monkeypatch,db)
    db.execute("UPDATE saas_crm_messages SET body='password=private sk-abcdefghijklmnopqrst https://private.test/media?token=secret user@example.com +994501234567',raw='{\"api_key\":\"raw-secret\"}'::jsonb WHERE tenant_id=%s::uuid",(A,));db.commit()
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    with db.cursor() as cur:
        public=admin.read(cur,cfg,'messages_read',{})
        detailed=admin.read(cur,cfg,'messages_read',{'include_content':True})
    assert 'body' not in public['items'][0]
    body=detailed['items'][0]['body']
    assert all(value not in body for value in ['private','sk-','https://','example.com','994501234567'])
    assert str(uuid.UUID(detailed['items'][0]['id']))==detailed['items'][0]['id']
    assert 'raw' not in detailed['items'][0]


def test_owner_bootstrap_knowledge_has_no_fabricated_crm_evidence(db,monkeypatch):
    _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg();cfg['granted_scopes']=[drafts.DRAFT_SCOPE];cfg['actor_id']=11
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{
            'title':'Owner supplied setup note','content':'Use only the documented warehouse procedure.',
            'owner_note':'Confirmed by the company owner.'})
    assert draft['kind']=='knowledge'
    assert draft['body']['owner_provided']['verified_by']==11
    assert 'evidence_ids' not in draft['body']
    assert draft['target']['source_eligibility']=='owner_provided_unverified'
    cfg['actor_id']=22
    with db.cursor() as cur:
        with pytest.raises(Exception):
            drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'x','content':'x','owner_note':'x'})


def test_owner_component_is_app_only_metadata_and_manual_decision_is_exact_once(db,monkeypatch):
    """A chat result can prepare, but never itself approve, an owner draft."""
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+drafts.DRAFT_SCOPE)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    connection_id=str(uuid.uuid4())
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET oauth_connection_id=%s::uuid WHERE token_hash=%s',
               (connection_id,hashlib.sha256(token.encode()).hexdigest())); db.commit()
    token_id=str(db.execute('SELECT id FROM saas_ai_learning_mcp_tokens WHERE token_hash=%s',
                            (hashlib.sha256(token.encode()).hexdigest(),))[0]['id'])
    async def check_component_protocol():
        app=web.Application(); app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            listed=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            tool={x['name']:x for x in (await listed.json())['result']['tools']}['administrator_owner_review_component']
            assert tool['_meta']['ui']['visibility']==['model','app']
            resources=await client.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'resources/list'})
            assert (await resources.json())['result']['resources'][0]['uri']==owner_component.RESOURCE_URI
            html_result=await client.post('/mcp',json={'jsonrpc':'2.0','id':3,'method':'resources/read','params':{'uri':owner_component.RESOURCE_URI}})
            resource=(await html_result.json())['result']['contents'][0]
            assert resource['mimeType']=='text/html;profile=mcp-app' and 'callTool' in resource['text']
    asyncio.run(check_component_protocol())
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],oauth_connection_id=connection_id,oauth_token_id=token_id)
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Owner note','content':'Use verified procedure.','owner_note':'Owner supplied.'})
        prepared=owner_component.prepare(cur,cfg,{'draft_id':draft['id'],'version':draft['version']})
        secret=prepared['_component_meta']['owner_review']['capabilities']['approve']
        assert prepared['_component_meta']['owner_review']['exact_diff']['after']['title']=='Owner note'
        assert secret not in json.dumps({k:v for k,v in prepared.items() if k!='_component_meta'})
        wire=json.loads(mcp._tool_response(prepared,1).body.decode())['result']
        assert secret not in json.dumps(wire['content'])
        assert secret not in json.dumps(wire['structuredContent'])
        assert wire['_meta']['owner_review']['capabilities']['approve']==secret
        # Disabled by default even with a genuine owner and matching secret.
        with pytest.raises(Exception):
            owner_component.decide(cur,cfg,capability=secret,action='approve')
        monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
        with pytest.raises(Exception):
            owner_component.decide(cur,{**cfg,'oauth_connection_id':str(uuid.uuid4())},capability=secret,action='approve')
        result=owner_component.decide(cur,cfg,capability=secret,action='approve')
        assert result['state']=='approved'
        with pytest.raises(Exception):
            owner_component.decide(cur,cfg,capability=secret,action='approve')
    db.commit()


def test_owner_component_rejects_stale_draft_and_disallowed_policy_bypass(db,monkeypatch):
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+drafts.DRAFT_SCOPE); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
    connection_id=str(uuid.uuid4())
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET oauth_connection_id=%s::uuid WHERE token_hash=%s',
               (connection_id,hashlib.sha256(token.encode()).hexdigest())); db.commit()
    token_id=str(db.execute('SELECT id FROM saas_ai_learning_mcp_tokens WHERE token_hash=%s',
                            (hashlib.sha256(token.encode()).hexdigest(),))[0]['id'])
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],oauth_connection_id=connection_id,oauth_token_id=token_id)
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Stale','content':'Do this only after confirmation.','owner_note':'Owner supplied.'})
        prepared=owner_component.prepare(cur,cfg,{'draft_id':draft['id'],'version':draft['version']})
        cur.execute('UPDATE saas_ai_administrator_drafts SET title=%s WHERE tenant_id=%s::uuid AND id=%s::uuid',('Changed',A,draft['id']))
        with pytest.raises(Exception):
            owner_component.decide(cur,cfg,
                                   capability=prepared['_component_meta']['owner_review']['capabilities']['approve'],action='approve')
        prompt=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Prompt','body':{'instructions':'x'},'target':{}})
        with pytest.raises(Exception): owner_component.prepare(cur,cfg,{'draft_id':prompt['id'],'version':prompt['version']})
    db.commit()


def test_owner_component_http_completion_is_bearer_bound_and_does_not_publish(db,monkeypatch):
    """The real MCP transport must not echo a secret or trust UI connection IDs."""
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+drafts.DRAFT_SCOPE)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
    connection_id=str(uuid.uuid4())
    digest=hashlib.sha256(token.encode()).hexdigest()
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET oauth_connection_id=%s::uuid WHERE token_hash=%s',(connection_id,digest));db.commit()
    token_id=str(db.execute('SELECT id FROM saas_ai_learning_mcp_tokens WHERE token_hash=%s',(digest,))[0]['id'])
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],oauth_connection_id=connection_id,oauth_token_id=token_id)
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'HTTP owner note','content':'Keep source inactive.','owner_note':'Owner supplied.'})
    db.commit()
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            headers={'Authorization':'Bearer '+token}
            listed=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            by_name={item['name']:item for item in (await listed.json())['result']['tools']}
            assert by_name['administrator_owner_review_component_complete']['_meta']['ui']['visibility']==['app']
            prepared=await client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'administrator_owner_review_component','arguments':{'draft_id':draft['id'],'version':draft['version']}}})
            body=(await prepared.json())['result']; secret=body['_meta']['owner_review']['capabilities']['approve']
            assert secret not in json.dumps(body['content']) and secret not in json.dumps(body['structuredContent'])
            wrong=body['_meta']['owner_review']['capabilities']['reject']
            denied=await client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'administrator_owner_review_component_complete','arguments':{'action':'approve','capability':wrong}}})
            assert denied.status==409 and wrong not in (await denied.text())
            complete=await client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'administrator_owner_review_component_complete','arguments':{'action':'approve','capability':secret}}})
            value=json.loads((await complete.json())['result']['content'][0]['text'])
            assert value['state']=='approved'
    asyncio.run(check())
    assert db.execute('SELECT state FROM saas_ai_administrator_approvals WHERE tenant_id=%s::uuid AND draft_id=%s::uuid',(A,draft['id']))[0]['state']=='approved'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND title=%s',(A,'HTTP owner note'))[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid',(A,))[0]['n']==0


def test_owner_component_revoked_scope_expired_and_superseded_capabilities_fail_closed(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+drafts.DRAFT_SCOPE)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true');monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
    connection_id=str(uuid.uuid4())
    row=db.execute('SELECT id,token_hash FROM saas_ai_learning_mcp_tokens LIMIT 1')[0]
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET oauth_connection_id=%s::uuid WHERE id=%s::uuid',(connection_id,row['id']));db.commit()
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],oauth_connection_id=connection_id,oauth_token_id=str(row['id']))
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Supersede','content':'Keep inactive.','owner_note':'Owner supplied.'})
        first=owner_component.prepare(cur,cfg,{'draft_id':draft['id'],'version':1})
        second=owner_component.prepare(cur,cfg,{'draft_id':draft['id'],'version':1})
        with pytest.raises(Exception): owner_component.decide(cur,cfg,capability=first['_component_meta']['owner_review']['capabilities']['approve'],action='approve')
        db.execute("UPDATE saas_ai_learning_mcp_tokens SET scope='ai_learning.read' WHERE id=%s::uuid",(row['id'],))
        with pytest.raises(Exception): owner_component.decide(cur,cfg,capability=second['_component_meta']['owner_review']['capabilities']['approve'],action='approve')
        db.execute("UPDATE saas_ai_learning_mcp_tokens SET scope=%s,revoked_at=now() WHERE id=%s::uuid",(mcp.TOOL_SCOPE+' '+drafts.DRAFT_SCOPE,row['id']))
        with pytest.raises(Exception): owner_component.decide(cur,cfg,capability=second['_component_meta']['owner_review']['capabilities']['approve'],action='approve')
    db.commit()


def test_web_reject_invalidates_widget_before_rerequest(db,monkeypatch):
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+drafts.DRAFT_SCOPE)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true');monkeypatch.setenv('AI_OWNER_COMPONENT_APPROVAL_ENABLED','true')
    connection=str(uuid.uuid4()); digest=hashlib.sha256(token.encode()).hexdigest()
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET oauth_connection_id=%s::uuid WHERE token_hash=%s',(connection,digest));db.commit()
    token_id=str(db.execute('SELECT id FROM saas_ai_learning_mcp_tokens WHERE token_hash=%s',(digest,))[0]['id'])
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],oauth_connection_id=connection,oauth_token_id=token_id)
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Web reject','content':'Inactive only.','owner_note':'Owner.'})
        first=owner_component.prepare(cur,cfg,{'draft_id':draft['id'],'version':1})
        approvals.approve(cur,PROFILE,first['approval_id'],'reject')
        with pytest.raises(Exception): owner_component.decide(cur,cfg,capability=first['_component_meta']['owner_review']['capabilities']['approve'],action='approve')
        second=owner_component.prepare(cur,cfg,{'draft_id':draft['id'],'version':1})
        with pytest.raises(Exception): owner_component.decide(cur,cfg,capability=first['_component_meta']['owner_review']['capabilities']['reject'],action='reject')
        assert second['approval_state']=='pending'
    db.commit()


def test_owner_component_is_not_advertised_when_drafts_are_disabled(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.delenv('AI_LEARNING_MCP_DRAFTS_ENABLED',raising=False)
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            listed=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            assert 'administrator_owner_review_component' not in {x['name'] for x in (await listed.json())['result']['tools']}
            resources=await client.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'resources/list'})
            assert (await resources.json())['result']['resources']==[]
    asyncio.run(check())


def test_owner_provided_knowledge_remains_inactive_until_existing_apply_and_publish_gates(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true');monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE,approvals.PUBLISH_SCOPE],AI_LEARNING_MCP_AGENT_ID=AGENT,AI_LEARNING_MCP_CHANNEL_ID=CA)
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'owner_knowledge',{'title':'Owner verified for review','content':'Never invent a repair.','owner_note':'Owner confirms this is a draft, not CRM evidence.'})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']);approvals.approve(cur,PROFILE,receipt['id'],'approve')
        applied=apply.apply_knowledge_draft(cur,PROFILE,receipt['id'],draft['id'])
        assert applied['active'] is False and applied['agent_bound'] is False
        material=approvals._publication_object(cur,A,'knowledge',applied['source_id'])
        grant=approvals.publication_grant(cur,PROFILE,{'material_hash':approvals._publication_hash(material),'draft_id':draft['id'],'version':draft['version'],'kind':'knowledge','object_id':applied['source_id'],'agent_id':AGENT,'channel_id':CA,'ttl_minutes':30,'review_confirmed':True})
        published=mcp._tools(cur,cfg,'administrator_publish_reviewed',{'grant_id':grant['grant_id']})
        assert published['published'] is True and published['public_link_created'] is False
    db.commit()


def test_cursor_is_bound_to_tenant_tool_and_content_policy(db,monkeypatch):
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    marker=admin._page_key(cfg,'messages_read',{},1)
    with db.cursor() as cur:
        for name,args in [('channels_read',{'cursor':marker}),('messages_read',{'cursor':marker,'include_content':True}),('messages_read',{'cursor':marker+'bad'})]:
            with pytest.raises(Exception):admin.read(cur,cfg,name,args)
        with pytest.raises(Exception):admin.read(cur,{**cfg,'AI_LEARNING_MCP_TENANT_ID':B},'messages_read',{'cursor':marker})
    for args in [{'limit':True},{'limit':1000},{'sql':'SELECT 1'},{'include_content':'true'},{'id':1}]:
        with pytest.raises(Exception):admin.validate(args)


def test_read_tool_registry_has_strict_schema_no_write_tools():
    for tool in admin.definitions():
        assert tool['inputSchema']['additionalProperties'] is False
        assert tool['securitySchemes'][0]['scopes']==['ai_learning.read']
        assert tool['annotations']=={'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}


def test_simulation_queue_diagnostic_is_tenant_bound_read_only(db,monkeypatch):
    source = seed_simulation(db)
    queue_id = str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_simulation_queue(id,tenant_id,channel_id,conversation_id,lead_id,source_message_id,
        source_event_id,event_fingerprint,state,attempts) VALUES(%s::uuid,%s::uuid,%s::uuid,'client',41,%s::uuid,
        'issue-152','issue-152-fingerprint','queued',0)''', (queue_id,A,CA,source))
    db.execute("UPDATE saas_ai_agent_settings SET operating_mode='simulation' WHERE tenant_id=%s::uuid", (A,))
    db.commit()
    monkeypatch.setenv('AI_SIMULATION_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    with db.cursor() as cur:
        item=admin.read(cur,cfg,'simulation_queue_diagnostic_read',{'id':queue_id})
    assert item['id']==queue_id and item['attempts']==0 and item['read_only'] is True
    assert item['admission_eligible_now'] is True and item['blockers']==[]
    db.execute("UPDATE saas_ai_simulation_configs SET enabled=FALSE WHERE tenant_id=%s::uuid",(A,));db.commit()
    with db.cursor() as cur:
        stopped=admin.read(cur,cfg,'simulation_queue_diagnostic_read',{'id':queue_id})
    assert stopped['admission_eligible_now'] is False and 'simulation_disabled' in stopped['blockers']
    with db.cursor() as cur:
        with pytest.raises(Exception): admin.read(cur,{**cfg,'AI_LEARNING_MCP_TENANT_ID':B},'simulation_queue_diagnostic_read',{'id':queue_id})


def test_administrator_drafts_are_flagged_scoped_versioned_and_never_applied(db,monkeypatch):
    _setup(monkeypatch,db)
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        with pytest.raises(Exception): drafts.create(cur,cfg,{'kind':'knowledge','title':'Blocked','body':{}})
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    with db.cursor() as cur:
        value=drafts.create(cur,cfg,{'kind':'knowledge','title':'Draft knowledge','body':{'claim':'needs review'},'target':{}})
    db.commit()
    assert value['state']=='draft' and value['version']==1
    with db.cursor() as cur:
        changed=drafts.update(cur,cfg,{'id':value['id'],'expected_version':1,'body':{'claim':'edited'}})
        assert drafts.listing(cur,cfg,{})['items'][0]['id']==value['id']
        history=drafts.versions(cur,cfg,{'id':value['id']})['items']
        assert [item['version'] for item in history]==[1,2]
        assert history[0]['body']=={'claim':'needs review'} and history[1]['body']=={'claim':'edited'}
        restored=mcp._tools(cur,cfg,'administrator_draft_restore',{'id':value['id'],'version':1,'expected_version':2})
        assert restored['version']==3 and restored['body']=={'claim':'needs review'}
        assert [item['version'] for item in drafts.versions(cur,cfg,{'id':value['id']})['items']]==[1,2,3]
        with pytest.raises(Exception): drafts.update(cur,cfg,{'id':value['id'],'expected_version':1,'body':{}})
    db.commit()
    assert changed['version']==2


def test_typed_draft_tools_enforce_evidence_and_remain_nonpublic(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid LIMIT 1',(A,))
        evidence=str(cur.fetchone()['id'])
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
        prompt_body=prompt_body_with_completed_test(cur,cur.fetchone()['version'])
        prompt=mcp._tools(cur,cfg,'administrator_draft_agent_prompt_create',{'title':'Tested prompt',**prompt_body})
        knowledge=mcp._tools(cur,cfg,'administrator_draft_knowledge_create',{'title':'Evidence note','content':'Confirmed only.','evidence_ids':[evidence],'agent_ids':[AGENT]})
        case=mcp._tools(cur,cfg,'administrator_draft_support_case_create',{'title':'Case','problem':'Observed failure.','evidence_message_ids':[evidence]})
        scenario=mcp._tools(cur,cfg,'administrator_draft_testing_scenario_create',{'title':'Synthetic check','synthetic_input':'Synthetic request','expected':'Safe answer'})
        assert (prompt['state'],knowledge['state'],case['state'],scenario['state'])==('draft','draft','draft','draft')
        with pytest.raises(Exception):mcp._tools(cur,cfg,'administrator_draft_knowledge_create',{'title':'Bad','content':'x','evidence_ids':['not-a-uuid']})
        with pytest.raises(Exception):mcp._tools(cur,cfg,'administrator_draft_support_case_create',{'title':'Bad evidence','problem':'x','evidence_message_ids':[str(uuid.uuid4())]})
        with pytest.raises(Exception):mcp._tools(cur,cfg,'administrator_draft_agent_prompt_create',{'title':'Foreign','agent_id':str(uuid.uuid4()),'expected_version':1,'instructions':'x','testing_turn_id':str(uuid.uuid4())})
        with pytest.raises(Exception):mcp._tools(cur,cfg,'administrator_draft_agent_prompt_create',{'title':'Untested','agent_id':AGENT,'expected_version':prompt_body['expected_version'],'instructions':'x','testing_turn_id':str(uuid.uuid4())})
    db.commit()


def test_knowledge_separation_proposal_is_run_cited_version_bound_and_non_mutating(db,monkeypatch):
    run_id,_,_=seed_simulation_run(db)
    source_id=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_knowledge_sources(id,tenant_id,agent_id,title,kind,content,active,content_hash,created_by)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'Mixed guidance','text','Keep verified ERP facts with behaviour rules.',TRUE,'source-hash',11)''',(source_id,A,AGENT))
    db.execute('UPDATE saas_ai_simulation_runs SET knowledge_refs=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
               (json.dumps([{'source_id':source_id,'chunk_no':0}]),A,run_id))
    db.commit(); _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    request={'title':'Separate behaviour guidance from ERP facts','source_id':source_id,'source_content_hash':'source-hash',
             'simulation_run_id':run_id,'behavior_rules':'Ask clarifying questions before answering.','product_knowledge':'Warehouse moves require verified stock selection.',
             'rationale':'The cited historical Simulation Run retrieved a mixed source.'}
    with db.cursor() as cur:
        proposal=mcp._tools(cur,cfg,'administrator_draft_knowledge_separation_create',request)
        assert proposal['state']=='draft' and proposal['kind']=='knowledge'
        assert proposal['body']['source_id']==source_id and proposal['target']['requires_owner_review'] is True
        cur.execute('SELECT active,content,content_hash FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source_id))
        assert dict(cur.fetchone())=={'active':True,'content':'Keep verified ERP facts with behaviour rules.','content_hash':'source-hash'}
        with pytest.raises(Exception):
            mcp._tools(cur,cfg,'administrator_draft_knowledge_separation_create',{**request,'source_content_hash':'stale'})
        db.execute('UPDATE saas_ai_simulation_runs SET knowledge_refs=\'[]\'::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,run_id))
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_draft_knowledge_separation_create',request)
    db.commit()


def test_isolated_testing_comparison_is_aggregated_and_tenant_scoped(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[admin.READ_SCOPE]
    baseline,candidate=uuid.uuid4(),uuid.uuid4()
    create_testing_schema(db)
    with db.cursor() as cur:
        for ident in (baseline,candidate):
            cur.execute('INSERT INTO saas_ai_test_sessions(tenant_id,id,agent_id,created_by,scope) VALUES(%s::uuid,%s,%s::uuid,11,\'{}\'::jsonb)',(A,ident,AGENT))
        for session,state,tokens in ((baseline,'completed',12),(candidate,'completed',20),(candidate,'unknown',7)):
            ident=uuid.uuid4()
            cur.execute('''INSERT INTO saas_ai_test_turns(tenant_id,id,session_id,input_hash,client_text,response,state,total_tokens)
              VALUES(%s::uuid,%s,%s,%s,\'synthetic\',\'\',%s,%s)''',(A,ident,session,'test:'+str(ident),state,tokens))
        result=mcp._tools(cur,cfg,'administrator_testing_compare',{'baseline_session_id':str(baseline),'candidate_session_id':str(candidate)})
        assert result['delta']=={'turns':1,'completed':0,'unknown':1,'blocked_or_superseded':0,'total_tokens':15}
        with pytest.raises(Exception):mcp._tools(cur,{**cfg,'AI_LEARNING_MCP_TENANT_ID':B},'administrator_testing_compare',{'baseline_session_id':str(baseline),'candidate_session_id':str(candidate)})
    db.commit()


def test_approval_binds_exact_draft_version_and_is_single_use(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        value=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Draft','body':{}})
        receipt=approvals.request(cur,{'tenant_id':A,'telegram_id':11},value['id'],1)
        assert approvals.approve(cur,{'tenant_id':A,'telegram_id':11},receipt['id'],'approve')['state']=='approved'
        approvals.consume(cur,A,receipt['id'],value['id'],1,'test')
        with pytest.raises(Exception): approvals.consume(cur,A,receipt['id'],value['id'],1,'test')
    db.commit()


def test_owner_batch_approval_is_atomic_and_tenant_scoped(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        first=drafts.create(cur,cfg,{'kind':'knowledge','title':'One','body':{}}); second=drafts.create(cur,cfg,{'kind':'support_case','title':'Two','body':{}})
        a=approvals.request(cur,PROFILE,first['id'],first['version']); b=approvals.request(cur,PROFILE,second['id'],second['version'])
        assert approvals.approve_many(cur,PROFILE,[a['id'],b['id']])['count']==2
        with pytest.raises(Exception): approvals.approve_many(cur,PROFILE,[a['id'],b['id']])
    db.commit()


def test_mcp_can_request_and_read_but_never_decide_or_apply_an_approval(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        draft=drafts.create(cur,cfg,{'kind':'knowledge','title':'Draft','body':{'claim':'review'},'target':{}})
        receipt=mcp._tools(cur,cfg,'administrator_approval_request',{'draft_id':draft['id'],'version':draft['version']})
        listed=mcp._tools(cur,cfg,'administrator_approval_list',{})
        assert listed['items'][0]['id']==receipt['id'] and listed['items'][0]['state']=='pending'
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_approval_approve',{'approval_id':receipt['id']})
    db.commit()


def test_administrator_approval_and_live_apply_are_owner_only(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); version=cur.fetchone()['version']
        draft=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Prompt','body':prompt_body_with_completed_test(cur,version)})
        cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        with pytest.raises(Exception): approvals.request(cur,PROFILE,draft['id'],draft['version'])
        cur.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        cur.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        with pytest.raises(Exception): apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])
        cur.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
        assert apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])['rollback_available'] is True
    db.commit()


def test_live_prompt_apply_rejects_missing_or_stale_isolated_test_without_consuming_approval(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); version=cur.fetchone()['version']
        body={'agent_id':AGENT,'expected_version':version,'instructions':'Verified only.','testing_turn_id':str(uuid.uuid4())}
        draft=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Needs test','body':body})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        with pytest.raises(Exception): apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])
        cur.execute('SELECT state FROM saas_ai_administrator_approvals WHERE id=%s::uuid',(receipt['id'],))
        assert cur.fetchone()['state']=='approved'
    db.commit()


def test_owner_approval_http_surface_requires_same_origin_and_strict_actions(monkeypatch):
    seen=[]
    monkeypatch.setattr(administrator_api,'_with_connection',lambda fn,*args,**kwargs: seen.append((fn,args,kwargs)) or {'items':[]})
    profile={'tenant_id':A,'telegram_id':11,'role':'owner'}
    async def check():
        app=web.Application(); app.router.add_get('/approvals',administrator_api.handler(lambda _:profile,'https://crm.pro.az',__import__('logging').getLogger(__name__)))
        app.router.add_post('/approvals',administrator_api.handler(lambda _:profile,'https://crm.pro.az',__import__('logging').getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/approvals')).status==200
            assert (await client.post('/approvals',json={'action':'approve','approval_id':str(uuid.uuid4())})).status==403
            response=await client.post('/approvals',headers={'Origin':'https://crm.pro.az'},json={'action':'approve','approval_id':str(uuid.uuid4()),'extra':True})
            assert response.status==400
    asyncio.run(check())
    assert seen and seen[0][0] is approvals.listing


def test_observations_are_deduplicated_and_disabled_by_default(db,monkeypatch):
    _setup(monkeypatch,db)
    assert observations.enabled() is False
    with db.cursor() as cur:
        first=observations.enqueue(cur,A,'simulation',{'run_id':'safe'})
        second=observations.enqueue(cur,A,'simulation',{'run_id':'safe'})
        cur.execute('SELECT count(*) AS n FROM saas_ai_administrator_observations WHERE tenant_id=%s::uuid',(A,))
        assert first==second and cur.fetchone()['n']==1
    db.commit()


def test_observation_projection_is_tenant_scoped_and_payload_requires_content_scope(db,monkeypatch):
    _setup(monkeypatch,db)
    with db.cursor() as cur:
        observations.enqueue(cur,A,'diagnostic',{'kind':'testing_scenario','title':'safe','body':{'secret':'omit-me'},'target':{}})
    db.commit(); cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE]
    with db.cursor() as cur:
        public=admin.read(cur,cfg,'administrator_observations_read',{})
        assert public['items'] and 'payload' not in public['items'][0]
        with pytest.raises(Exception):admin.read(cur,cfg,'administrator_observations_read',{'include_content':True})
        detailed=admin.read(cur,{**cfg,'granted_scopes':[admin.READ_SCOPE,admin.CONTENT_SCOPE]},'administrator_observations_read',{'include_content':True})
    assert detailed['items'][0]['payload']['title']=='safe'
    assert 'secret' not in detailed['items'][0]['payload']['body']


def test_observation_worker_creates_only_one_nonpublic_draft_and_bounds_recovery(db,monkeypatch):
    _setup(monkeypatch,db); monkeypatch.setenv('AI_AUTONOMOUS_IMPROVEMENT_ENABLED','true')
    monkeypatch.setattr(observations,'_connect',lambda:db)
    with db.cursor() as cur:
        observations.ensure_schema(db)
        observations.enqueue(cur,A,'safe-test',{'kind':'testing_scenario','title':'Safe diagnostic','body':{'reason':'unknown'},'target':{'agent_id':AGENT}},'test-1')
    db.commit()
    item=observations.claim_next(); assert item and item['tenant_id']==A
    observations.process(item)
    draft=db.execute("SELECT id,kind,title,state FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid ORDER BY created_at DESC LIMIT 1",(A,))[0]
    assert {key:value for key,value in draft.items() if key!='id'}=={'kind':'testing_scenario','title':'Safe diagnostic','state':'draft'}
    assert db.execute('SELECT version,title FROM saas_ai_administrator_draft_versions WHERE tenant_id=%s::uuid AND draft_id=%s::uuid',(A,draft['id']))[0]=={'version':1,'title':'Safe diagnostic'}
    audit=db.execute("SELECT action,payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid ORDER BY created_at DESC LIMIT 1",(A,))[0]
    assert audit['action']=='ai_administrator_observation_draft_created' and audit['payload']['source']=='safe-test'
    db.execute("UPDATE saas_ai_administrator_observations SET state='processing',attempts=%s,lease_until=now()-interval '1 minute' WHERE tenant_id=%s::uuid",(observations.MAX_ATTEMPTS,A));db.commit()
    assert observations.claim_next() is None
    assert db.execute("SELECT state FROM saas_ai_administrator_observations WHERE tenant_id=%s::uuid",(A,))[0]['state']=='blocked'


def test_owner_approved_prompt_application_has_versioned_rollback(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); version=cur.fetchone()['version']
        draft=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'Prompt','body':prompt_body_with_completed_test(cur,version)})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        result=apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])
        assert result['rollback_available'] is True
        restored=apply.rollback_agent_prompt(cur,PROFILE,receipt['id'])
        assert restored['rolled_back'] is True
    db.commit()


def test_mcp_apply_and_rollback_require_a_separate_scope_and_one_time_receipt(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version,instructions FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); agent=cur.fetchone()
        draft=drafts.create(cur,cfg,{'kind':'agent_improvement','title':'MCP prompt','body':prompt_body_with_completed_test(cur,agent['version'],'MCP verified instruction.')})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        with pytest.raises(Exception):
            mcp._tools(cur,{**cfg,'granted_scopes':[drafts.DRAFT_SCOPE]},'administrator_apply_agent_prompt',{'approval_id':receipt['id'],'draft_id':draft['id']})
        applied=mcp._tools(cur,cfg,'administrator_apply_agent_prompt',{'approval_id':receipt['id'],'draft_id':draft['id']})
        assert applied['rollback_available'] is True
        cur.execute('SELECT instructions FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
        assert cur.fetchone()['instructions']=='MCP verified instruction.'
        with pytest.raises(Exception):
            mcp._tools(cur,cfg,'administrator_apply_agent_prompt',{'approval_id':receipt['id'],'draft_id':draft['id']})
        rolled=mcp._tools(cur,cfg,'administrator_rollback_agent_prompt',{'approval_id':receipt['id']})
        assert rolled['rolled_back'] is True
        cur.execute('SELECT instructions FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
        assert cur.fetchone()['instructions']==agent['instructions']
    db.commit()


def test_synthetic_observation_to_tested_owner_approval_apply_and_rollback(db,monkeypatch):
    """The durable learning loop remains a draft-only flow until an owner acts."""
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_AUTONOMOUS_IMPROVEMENT_ENABLED','true')
    monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    monkeypatch.setattr(observations,'_connect',lambda:db)
    with db.cursor() as cur:
        observations.ensure_schema(db)
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
        version=cur.fetchone()['version']
        observations.enqueue(cur,A,'synthetic_e2e',{
            'kind':'agent_improvement','title':'Improve ERP escalation',
            'body':prompt_body_with_completed_test(cur,version,'Escalate only with verified evidence.'),
            'target':{'agent_id':AGENT},
        },'e2e-1')
    db.commit()
    observations.process(observations.claim_next())
    with db.cursor() as cur:
        cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND title=%s',(A,'Improve ERP escalation'))
        draft=cur.fetchone()
        assert draft['state']=='draft'
        cur.execute('SELECT version FROM saas_ai_administrator_draft_versions WHERE tenant_id=%s::uuid AND draft_id=%s::uuid',(A,draft['id']))
        assert cur.fetchone()['version']==1
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version'])
        approvals.approve(cur,PROFILE,receipt['id'],'approve')
        applied=apply.apply_agent_prompt(cur,PROFILE,receipt['id'],draft['id'])
        assert applied['rollback_available'] is True
        assert apply.rollback_agent_prompt(cur,PROFILE,receipt['id'])['rolled_back'] is True
    db.commit()


def test_owner_approved_knowledge_apply_is_inactive_and_can_be_rolled_back(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE]
    with db.cursor() as cur:
        draft=drafts.create(cur,cfg,{'kind':'knowledge','title':'Verified draft','body':{'content':'Verified ERP guidance.','evidence_ids':[]},'target':{'agent_ids':[]}})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        applied=mcp._tools(cur,cfg,'administrator_apply_knowledge_draft',{'approval_id':receipt['id'],'draft_id':draft['id']})
        assert applied['active'] is False and applied['published'] is False and applied['agent_bound'] is False
        cur.execute('SELECT active,agent_id,provenance FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,applied['source_id']))
        source=cur.fetchone(); assert source['active'] is False and source['agent_id'] is None and source['provenance']['review_only'] is True
        assert mcp._tools(cur,cfg,'administrator_rollback_knowledge_draft',{'approval_id':receipt['id']})['rolled_back'] is True
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_rollback_knowledge_draft',{'approval_id':receipt['id']})
    db.commit()


def test_owner_approved_instruction_apply_stays_draft_and_rolls_back(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    instruction_library.schema(db); asset=str(uuid.uuid4()); db.execute('INSERT INTO saas_instruction_assets VALUES(%s::uuid,%s::uuid,%s,100,100,%s)',(A,asset,b'jpeg','a'*64)); db.commit()
    doc={'schema_version':1,'title':'Safe lesson','operation':'erp.safe','language':'az','slides':[{'id':'s1','order':1,'title':'Step','explanation':'Read only','note':'No save','image':{'asset_id':asset+'.jpg','width':100,'height':100,'sha256':'a'*64},'areas':[],'playback':{'duration_ms':5000,'transition_ms':0,'focus':[],'return_to_overview':True}}],'subtitles':{'slides':[[{'at_ms':0,'text':'Read only'}]]}}
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['granted_scopes']=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE]
    with db.cursor() as cur:
        draft=drafts.typed_create(cur,cfg,'instruction',{'title':'Safe lesson','doc':doc})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        applied=mcp._tools(cur,cfg,'administrator_apply_instruction_draft',{'approval_id':receipt['id'],'draft_id':draft['id']})
        assert applied['state']=='draft' and applied['published'] is False
        cur.execute('SELECT state,version FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,applied['lesson_id'])); assert cur.fetchone()['state']=='draft'
        assert mcp._tools(cur,cfg,'administrator_rollback_instruction_draft',{'approval_id':receipt['id']})['state']=='archived'
    db.commit()


def test_trigger_approval_apply_rollback_is_atomic(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg['actor_id']=11; cfg['AI_LEARNING_MCP_AGENT_ID']=AGENT; cfg['granted_scopes']=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE]
    with db.cursor() as cur:
        cur.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); version=cur.fetchone()['version']
        args={'agent_id':AGENT,'channel_id':CA,'expected_agent_version':version,'trigger':{'name':'Safe handoff','trigger_type':'phrase','condition':{'phrase':'help'},'actions':['handoff'],'enabled':False}}
        draft=mcp._tools(cur,cfg,'administrator_draft_trigger_create',args)
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
    db.commit()
    with db.cursor() as cur:
        applied=mcp._tools(cur,cfg,'administrator_apply_trigger_draft',{'approval_id':receipt['id'],'draft_id':draft['id']})
        assert applied['enabled'] is False
    db.rollback()
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_triggers')[0]['n']==0
    assert db.execute('SELECT state FROM saas_ai_administrator_approvals WHERE id=%s::uuid',(receipt['id'],))[0]['state']=='approved'
    with db.cursor() as cur:
        result=mcp._tools(cur,cfg,'administrator_apply_trigger_draft',{'approval_id':receipt['id'],'draft_id':draft['id']})
        assert result['enabled'] is False
        assert mcp._tools(cur,cfg,'administrator_rollback_trigger_draft',{'approval_id':receipt['id']})['rolled_back']
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_apply_trigger_draft',{'approval_id':receipt['id'],'draft_id':draft['id']})
    db.commit()

@pytest.mark.parametrize('fault',['enabled','crm','channel','version','role'])
def test_trigger_proposal_rejects_unsafe_or_stale_scope(db,monkeypatch,fault):
    seed_simulation(db); _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],AI_LEARNING_MCP_AGENT_ID=AGENT)
    args={'agent_id':AGENT,'channel_id':CA,'expected_agent_version':1,'trigger':{'name':'Safe','trigger_type':'phrase','condition':{'phrase':'help'},'actions':['handoff'],'enabled':False}}
    if fault=='enabled': args['trigger']['enabled']=True
    if fault=='crm': args['trigger']['actions']=['update_phone']
    if fault=='channel': args['channel_id']=str(uuid.uuid4())
    if fault=='version': args['expected_agent_version']=999
    if fault=='role': db.execute("UPDATE saas_tenant_members SET role='worker' WHERE tenant_id=%s::uuid",(A,))
    with db.cursor() as cur:
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_draft_trigger_create',args)
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_triggers')[0]['n']==0

@pytest.mark.parametrize('language',['ru','az','en'])
def test_approved_agent_config_apply_rollback_preserves_mode_scope(db,monkeypatch,language):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE],AI_LEARNING_MCP_AGENT_ID=AGENT)
    with db.cursor() as cur:
        cur.execute('SELECT version,language,mode,scopes,enabled FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); before=cur.fetchone()
        draft=mcp._tools(cur,cfg,'administrator_draft_agent_config_create',{'agent_id':AGENT,'channel_id':CA,'expected_agent_version':before['version'],'patch':{'language':language}})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        result=mcp._tools(cur,cfg,'administrator_apply_agent_config',{'approval_id':receipt['id'],'draft_id':draft['id']})
        assert result['version']==before['version']+1
        cur.execute('SELECT language,mode,scopes,enabled FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT)); after=cur.fetchone()
        assert after=={**{k:before[k] for k in ('mode','scopes','enabled')},'language':language}
        assert mcp._tools(cur,cfg,'administrator_rollback_agent_config',{'approval_id':receipt['id']})['rolled_back']
    db.commit()

@pytest.mark.parametrize('patch',[{'mode':'automatic'},{'enabled':True},{'scopes':[]},{'model':'other'},{'safety':{}},{'expert_policy':{'expert_agent_ids':[str(uuid.uuid4())]}}])
def test_agent_config_rejects_capability_expansion(db,monkeypatch,patch):
    seed_simulation(db); _setup(monkeypatch,db); monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE],AI_LEARNING_MCP_AGENT_ID=AGENT)
    with db.cursor() as cur:
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_draft_agent_config_create',{'agent_id':AGENT,'channel_id':CA,'expected_agent_version':1,'patch':patch})


def test_agent_config_stale_rollback_and_cross_tenant_are_blocked(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE],AI_LEARNING_MCP_AGENT_ID=AGENT)
    with db.cursor() as cur:
        draft=mcp._tools(cur,cfg,'administrator_draft_agent_config_create',{'agent_id':AGENT,'channel_id':CA,'expected_agent_version':1,'patch':{'language':'ru'}})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version']); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        with pytest.raises(Exception): mcp._tools(cur,{**cfg,'AI_LEARNING_MCP_TENANT_ID':B,'actor_id':22},'administrator_apply_agent_config',{'approval_id':receipt['id'],'draft_id':draft['id']})
        mcp._tools(cur,cfg,'administrator_apply_agent_config',{'approval_id':receipt['id'],'draft_id':draft['id']})
        cur.execute('UPDATE saas_ai_agents SET version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
        with pytest.raises(Exception): mcp._tools(cur,cfg,'administrator_rollback_agent_config',{'approval_id':receipt['id']})
    db.commit()


def test_mcp_http_discovery_and_instruction_dispatch(db,monkeypatch):
    _setup(monkeypatch,db)
    async def check():
        app=web.Application(); app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            tools=(await response.json())['result']['tools']; by_name={item['name']:item for item in tools}
            for kind in ('knowledge_draft','instruction_draft','trigger_draft','agent_config'):
                for verb in ('apply','rollback'):
                    tool=by_name['administrator_'+verb+'_'+kind]
                    assert tool['inputSchema']['additionalProperties'] is False
                    assert tool['securitySchemes'][0]['scopes']==[mcp.APPLY_SCOPE]
            assert by_name['administrator_historical_replay_preflight']['annotations']['readOnlyHint']
    asyncio.run(check())
    marker=[]
    monkeypatch.setattr(drafts,'typed_create',lambda *args:marker.append(args[2]) or {})
    with db.cursor() as cur: mcp._tools(cur,{},'administrator_draft_instruction_create',{})
    assert marker==['instruction']


def test_knowledge_rollback_rejects_changed_source(db,monkeypatch):
    seed_simulation(db); _setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true'); monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    cfg=mcp._require_cfg(); cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE])
    with db.cursor() as cur:
        draft=drafts.create(cur,cfg,{'kind':'knowledge','title':'Review','body':{'content':'Evidence only','evidence_ids':[]},'target':{'agent_ids':[]}})
        receipt=approvals.request(cur,PROFILE,draft['id'],1); approvals.approve(cur,PROFILE,receipt['id'],'approve')
        applied=apply.apply_knowledge_draft(cur,PROFILE,receipt['id'],draft['id'])
        cur.execute('UPDATE saas_ai_knowledge_sources SET active=TRUE WHERE id=%s::uuid',(applied['source_id'],))
        with pytest.raises(Exception): apply.rollback_knowledge_draft(cur,PROFILE,receipt['id'])
    db.commit()
