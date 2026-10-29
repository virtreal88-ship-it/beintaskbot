"""Regression coverage for the externally consumable, read-only Learning MCP."""
import asyncio
import hashlib
import json
import logging
import os
import uuid
import pytest
from urllib.parse import parse_qs, urlparse

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_tenant_support_inbox import A, CA, PROFILE, database, db, seed_job, seed_simulation_run
import tenant_ai_learning_mcp as mcp
import tenant_ai_dialogue_merge as dialogue_merge
import tenant_ai_agents_store as agents


AGENT='67da1058-2ffb-48fa-bfd9-d3d01c1990d1'
CHATGPT_CLIENT='https://chatgpt.com/oauth/JOSnsTD-M4Nm/client.json'
CHATGPT_REDIRECT='https://chatgpt.com/connector/oauth/JOSnsTD-M4Nm'


def test_mcp_is_fail_closed_without_explicit_railway_configuration(monkeypatch):
    for key in ('AI_LEARNING_MCP_ENABLED','AI_LEARNING_MCP_ISSUER','AI_LEARNING_MCP_TENANT_ID',
                'AI_LEARNING_MCP_AGENT_ID','AI_LEARNING_MCP_CHANNEL_ID','AI_LEARNING_MCP_CURSOR_SECRET'):
        monkeypatch.delenv(key, raising=False)
    import pytest
    with pytest.raises(Exception):
        mcp._require_cfg()


def test_cursor_is_opaque_signed_and_context_redaction(monkeypatch):
    monkeypatch.setenv('AI_LEARNING_MCP_ENABLED','true'); monkeypatch.setenv('AI_LEARNING_MCP_ISSUER','https://crm.test')
    monkeypatch.setenv('AI_LEARNING_MCP_TENANT_ID',A); monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
    monkeypatch.setenv('AI_LEARNING_MCP_CHANNEL_ID',CA); monkeypatch.setenv('AI_LEARNING_MCP_CURSOR_SECRET','secret')
    cfg=mcp._require_cfg(); cursor=mcp._cursor(cfg,'2026-10-23T10:00:00+00:00',uuid.uuid4())
    assert mcp._parse_cursor(cfg,cursor)[0].startswith('2026-10-23')
    assert '[phone]' in mcp._redact('call +994501234567') and '[redacted]' in mcp._redact('api_key=private')


def test_dialog_assignment_requires_separate_owner_oauth_scope():
    scopes=mcp._requested_scopes(mcp.TOOL_SCOPE+' '+mcp.DIALOG_ASSIGN_SCOPE)
    assert mcp.DIALOG_ASSIGN_SCOPE in scopes
    text,button=mcp._consent_text(scopes)
    assert 'leaves the dialog paused' in text and 'never activates automatic replies' in text
    assert button=='Allow dialog assignment'
    with pytest.raises(Exception):
        mcp._requested_scopes(mcp.TOOL_SCOPE+' arbitrary.write')


def _setup(monkeypatch, database, scope=None):
    monkeypatch.setattr(mcp, '_connect', lambda: database)
    monkeypatch.setenv('AI_LEARNING_MCP_ENABLED','true')
    monkeypatch.setenv('AI_LEARNING_MCP_ISSUER','https://crm.test')
    monkeypatch.setenv('AI_LEARNING_MCP_TENANT_ID',A)
    monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
    monkeypatch.setenv('AI_LEARNING_MCP_CHANNEL_ID',CA)
    monkeypatch.setenv('AI_LEARNING_MCP_CURSOR_SECRET','test-only-cursor-secret')
    mcp.ensure_mcp_schema(database)
    token='read-only-test-token'
    database.execute('''INSERT INTO saas_ai_learning_mcp_tokens(id,tenant_id,token_hash,agent_id,channel_id,client_id,scope,expires_at,created_by)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,'https://chatgpt.com/oauth/client.json',%s,now()+interval '1 hour',11)''',
      (str(uuid.uuid4()),A,hashlib.sha256(token.encode()).hexdigest(),AGENT,CA,scope or mcp.TOOL_SCOPE))
    database.commit()
    return token


def test_mcp_requires_token_and_returns_only_scoped_runs(db, monkeypatch):
    token=_setup(monkeypatch,db)
    run,_,_=seed_simulation_run(db,outcome='virtual_reply',events=[{'type':'provider_result_unknown'}])
    db.execute('UPDATE saas_ai_simulation_runs SET primary_agent_id=%s::uuid WHERE id=%s::uuid',(AGENT,run)); db.commit()
    async def check():
        app=web.Application(); app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            listed=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            assert listed.status==200 and (await listed.json())['result']['tools'][0]['annotations']['readOnlyHint'] is True
            assert (await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'simulation_runs','arguments':{}}})).status==401
            response=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'simulation_runs','arguments':{'limit':1}}})
            data=await response.json(); text=data['result']['content'][0]['text']
            assert response.status==200 and run in text and A not in text
            assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='ai_learning_mcp_read'")[0]['n']==1
    asyncio.run(check())


def test_mcp_context_uses_snapshot_or_honest_cutoff_reconstruction(db, monkeypatch):
    _setup(monkeypatch,db)
    run,source,queue=seed_simulation_run(db)
    db.execute("UPDATE saas_crm_messages SET happened_at='2026-10-24T12:00:00Z',created_at='2026-10-24T12:00:00Z' WHERE id=%s::uuid",(source,))
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type,happened_at,created_at)
      VALUES(%s::uuid,%s::uuid,41,'after-run',%s,'outgoing','Operator','FUTURE REPLY','text','2026-10-24T12:01:00Z','2026-10-24T12:01:00Z')''',
      (str(uuid.uuid4()),A,'connect:'+CA))
    db.execute('UPDATE saas_ai_simulation_runs SET primary_agent_id=%s::uuid WHERE id=%s::uuid',(AGENT,run)); db.commit()
    cfg=mcp._require_cfg()
    with db.cursor() as cur:
        legacy=mcp._runs(cur,cfg,limit=10,include_context=True)['items']
    item=next(value for value in legacy if value['run_id']==run)
    assert item['context_provenance']=='legacy_cutoff_reconstruction'
    assert all('FUTURE REPLY' not in value['text'] for value in item['context'])
    assert item['context'][-1]['role']=='customer'
    snapshot=[{'role':'client','author_type':'participant','author':'Group member','text':'Actual model input','at':'2026-10-24T12:00:00Z'}]
    db.execute('UPDATE saas_ai_simulation_runs SET context_snapshot=%s::jsonb WHERE id=%s::uuid',(json.dumps(snapshot),run)); db.commit()
    with db.cursor() as cur:
        captured=mcp._runs(cur,cfg,limit=10,include_context=True)['items']
    item=next(value for value in captured if value['run_id']==run)
    assert item['context_provenance']=='captured_model_input'
    assert item['context']==[{'role':'participant','at':'2026-10-24T12:00:00Z','text':'Actual model input','attachment':False}]


def test_cursor_is_signed_and_rate_limit_is_enforced(db, monkeypatch):
    token=_setup(monkeypatch,db)
    cfg=mcp._require_cfg()
    with __import__('pytest').raises(Exception): mcp._parse_cursor(cfg,'not-a-cursor')
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET requests_in_window=60 WHERE token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),)); db.commit()
    request=type('Request',(),{'headers':{'Authorization':'Bearer '+token}})()
    with __import__('pytest').raises(Exception): mcp._authorize_token(request)


def test_chatgpt_cimd_identity_is_pinned_to_its_published_client_and_redirect():
    mcp._chatgpt_client(CHATGPT_CLIENT, CHATGPT_REDIRECT)
    import pytest
    with pytest.raises(Exception):
        mcp._chatgpt_client('https://chatgpt.com/oauth/other.json', CHATGPT_REDIRECT)
    with pytest.raises(Exception):
        mcp._chatgpt_client(CHATGPT_CLIENT, 'https://chatgpt.com/connector/other')


def test_oauth_metadata_advertises_cimd_pkce_refresh_and_exact_mcp_resource(db, monkeypatch):
    _setup(monkeypatch,db)
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request: None):
            getattr(app.router, 'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            response=await client.get('/.well-known/oauth-authorization-server')
            data=await response.json()
            assert data['client_id_metadata_document_supported'] is True
            assert data['code_challenge_methods_supported']==['S256']
            assert data['token_endpoint_auth_methods_supported']==['none']
            assert data['grant_types_supported']==['authorization_code','refresh_token']
            assert data['revocation_endpoint']=='https://crm.test/oauth/revoke'
            assert mcp.OFFLINE_SCOPE in data['scopes_supported']
            assert mcp.KNOWLEDGE_DEDUP_SCOPE in data['scopes_supported']
            assert mcp.APPLY_SCOPE in data['scopes_supported']
            assert mcp.DIALOGUE_MERGE_SCOPE in data['scopes_supported']
            assert mcp.DIALOG_ASSIGN_SCOPE in data['scopes_supported']
            assert mcp.SUPPORT_CASE_SCOPE in data['scopes_supported']
            assert mcp.SUPPORT_MEDIA_SCOPE in data['scopes_supported']
            assert mcp.AGENT_MANAGE_SCOPE in data['scopes_supported']
            protected=await client.get('/.well-known/oauth-protected-resource/mcp/ai-learning')
            assert (await protected.json())['resource']=='https://crm.test/mcp/ai-learning'
            listed=await client.post('/mcp/ai-learning',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            definitions={item['name']:item for item in (await listed.json())['result']['tools']}
            assert {'whatsapp_dialogue_duplicate_candidates','whatsapp_dialogue_merge',
                    'whatsapp_dialogue_owner_merge_preview','whatsapp_dialogue_owner_merge',
                    'support_dialog_agent_options','support_dialog_agent_assign','support_cases_list',
                    'support_case_detail','support_case_confirm_publish','support_case_edit_publish',
                    'support_case_edit_boundaries','support_case_reject_delete','support_case_delete_status','support_agents_list',
                    'support_agent_archive_transfer','support_agent_archive_status','support_agent_delete_preview','support_agent_permanent_delete'} <= set(definitions)
            assert {'support_media_snapshot_preview','support_media_campaign_prepare','support_media_campaign_read','support_media_campaign_list',
                    'support_media_campaign_start','support_media_campaign_control'} <= set(definitions)
            assert definitions['support_media_campaign_list']['securitySchemes']==[{'type':'oauth2','scopes':[mcp.SUPPORT_MEDIA_SCOPE]}]
            source=definitions['support_media_case_source_inspect']
            assert source['securitySchemes']==[{'type':'oauth2','scopes':[mcp.SUPPORT_MEDIA_SCOPE]}]
            assert source['annotations']['readOnlyHint'] is True
            assert set(source['inputSchema']['required'])=={'case_id','message_id','campaign_id','expected_version'}
            verified_source=definitions['support_media_case_source_verify']
            assert verified_source['securitySchemes']==[{'type':'oauth2','scopes':[mcp.SUPPORT_MEDIA_SCOPE]}]
            assert verified_source['annotations']['openWorldHint'] is True
            assert definitions['whatsapp_dialogue_owner_merge']['securitySchemes']==[{'type':'oauth2','scopes':[mcp.DIALOGUE_MERGE_SCOPE]}]
            assert definitions['whatsapp_dialogue_owner_merge_preview']['annotations']['readOnlyHint'] is True
            assert definitions['support_media_campaign_list']['annotations']['readOnlyHint'] is True
            assert definitions['support_media_campaign_recover_failed']['securitySchemes']==[{'type':'oauth2','scopes':[mcp.SUPPORT_MEDIA_SCOPE]}]
            assert definitions['support_media_campaign_recover_failed']['annotations']['destructiveHint'] is True
            assert definitions['support_dialog_agent_assign']['securitySchemes']==[{'type':'oauth2','scopes':[mcp.DIALOG_ASSIGN_SCOPE]}]
            assert definitions['support_case_reject_delete']['annotations']['destructiveHint'] is True
            assert definitions['support_agent_archive_transfer']['annotations']['destructiveHint'] is True
            assert definitions['support_agent_permanent_delete']['annotations']['destructiveHint'] is True
    asyncio.run(check())


def test_campaign_inventory_is_tenant_scoped_paginated_and_requires_media_scope(db,monkeypatch):
    _setup(monkeypatch,db)
    from test_tenant_support_inbox import B
    ids=[str(uuid.uuid4()) for _ in range(4)]; other_agent=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Night',11),(%s::uuid,%s::uuid,'Other',22)",(A,AGENT,B,other_agent))
    for i,key in enumerate(ids):
        tenant,actor,agent=(A,11,AGENT) if i<3 else (B,22,other_agent)
        db.execute('''INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,max_calls,max_tokens,max_cost_microusd,created_at)
          VALUES(%s::uuid,%s::uuid,%s,'[]'::jsonb,%s::uuid,'stopped',10,24000,10000000,'2026-01-01'::timestamptz + %s * interval '1 second')''',(tenant,key,actor,agent,i))
    db.commit()
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.SUPPORT_MEDIA_SCOPE]}
    with db.cursor() as cur:
        with pytest.raises(Exception): mcp._tools(cur,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_media_campaign_list',{})
        first=mcp._tools(cur,cfg,'support_media_campaign_list',{'limit':2})
        assert [item['id'] for item in first['campaigns']]==[ids[2],ids[1]]
        assert first['campaigns'][0]['agent_name']=='Night' and first['campaigns'][0]['max_cost_microusd']==10000000
        second=mcp._tools(cur,cfg,'support_media_campaign_list',{'before':first['next_before'],'limit':2})
        assert [item['id'] for item in second['campaigns']]==[ids[0]] and second['next_before'] is None
        with pytest.raises(Exception): mcp._tools(cur,cfg,'support_media_campaign_list',{'before':ids[3]})
        with pytest.raises(Exception): mcp._tools(cur,cfg,'support_media_campaign_list',{'limit':True})
        with pytest.raises(Exception): mcp._tools(cur,{**cfg,'actor_id':22},'support_media_campaign_list',{})


def test_campaign_inventory_http_scope_and_audit(db,monkeypatch):
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.SUPPORT_MEDIA_SCOPE)
    other='inventory-read-only-token'
    db.execute('''INSERT INTO saas_ai_learning_mcp_tokens(id,tenant_id,token_hash,agent_id,channel_id,client_id,scope,expires_at,created_by)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,'https://chatgpt.com/oauth/client.json',%s,now()+interval '1 hour',11)''',
      (str(uuid.uuid4()),A,hashlib.sha256(other.encode()).hexdigest(),AGENT,CA,mcp.TOOL_SCOPE));db.commit()
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'support_media_campaign_list','arguments':{}}}
            denied=await client.post('/mcp',headers={'Authorization':'Bearer '+other},json=body)
            assert denied.status==403
            allowed=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json=body)
            assert allowed.status==200
            payload=await allowed.json()
            assert json.loads(payload['result']['content'][0]['text'])=={'campaigns':[],'next_before':None}
        assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='mcp_support_media_campaign_list'")[0]['n']==1
    asyncio.run(check())


def test_mcp_current_case_media_tools_use_fixed_grant_and_exact_start_confirmation(db,monkeypatch):
    _setup(monkeypatch,db)
    import tenant_knowledge_campaigns as campaigns
    campaign_id=str(uuid.uuid4());snapshot='a'*64;agent=str(uuid.uuid4());channel=str(uuid.uuid4())
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.SUPPORT_MEDIA_SCOPE]}
    profile={'tenant_id':A,'telegram_id':11};seen=[]
    monkeypatch.setattr(campaigns,'preview_existing_cases',lambda p,acknowledge_prior_unknown=False:(seen.append(('preview',p,acknowledge_prior_unknown)) or {'case_count':4,'eligible_count':2,'retry_unknown_count':int(acknowledge_prior_unknown)}))
    monkeypatch.setattr(campaigns,'prepare',lambda p,data:(seen.append(('prepare',p,data.copy())) or campaign_id))
    current={'id':campaign_id,'version':7,'status':'awaiting_budget','channel_ids':[channel],
        'max_calls':8,'max_tokens':80000,'media_policy':{'allow_external_media':True,'allow_paid_asr':False,
            'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':4},
        'max_cost_microusd':10_000_000,'video_model':'gpt-4.1','photo_model':'gpt-4.1-2025-04-14',
        'progress':{'mode':'case_media_batch','snapshot_hash':snapshot}}
    monkeypatch.setattr(campaigns,'get',lambda p,cid:(seen.append(('read',p,str(cid))) or {'campaign':current}))
    monkeypatch.setattr(campaigns,'approve',lambda p,cid,data:(seen.append(('start',p,str(cid),data.copy())) or {'campaign':{'id':cid,'status':'running'}}))
    with db.cursor() as cur:
        assert mcp._tools(cur,cfg,'support_media_snapshot_preview',{})['eligible_count']==2
        assert mcp._tools(cur,cfg,'support_media_snapshot_preview',{'acknowledge_prior_unknown':True})['retry_unknown_count']==1
        with pytest.raises(Exception):mcp._tools(cur,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_media_snapshot_preview',{})
        with pytest.raises(Exception):mcp._tools(cur,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_media_campaign_recover_failed',{'campaign_id':campaign_id,'expected_version':1,'request_id':str(uuid.uuid4())})
    prepared=mcp._tools(None,cfg,'support_media_campaign_prepare',{'request_id':campaign_id,
        'channel_ids':[channel],'analysis_agent_id':agent,'max_calls':8,'max_tokens':80000,
        'media_policy':current['media_policy'],'video_model':'gpt-4.1'})
    assert prepared['approval_required'] is True and prepared['provider_calls_started'] is False
    assert seen[-1][2]['max_cost_microusd']==10_000_000 and seen[-1][2]['all_existing_cases'] is True
    with pytest.raises(Exception):mcp._tools(None,cfg,'support_media_campaign_start',{
        'campaign_id':campaign_id,'expected_version':7,'snapshot_hash':'b'*64,'confirm_external_processing':True})
    with pytest.raises(Exception):mcp._tools(None,cfg,'support_media_campaign_start',{
        'campaign_id':campaign_id,'expected_version':7,'snapshot_hash':snapshot,'confirm_external_processing':False,'acknowledge_prior_unknown':False})
    assert not any(call[0]=='start' for call in seen)
    started=mcp._tools(None,cfg,'support_media_campaign_start',{
        'campaign_id':campaign_id,'expected_version':7,'snapshot_hash':snapshot,'confirm_external_processing':True,'acknowledge_prior_unknown':False})
    assert started['campaign']['status']=='running'
    assert seen[-1][3]['max_cost_microusd']==10_000_000
    assert seen[-1][3]['all_cases_snapshot_hash']==snapshot
    assert seen[-1][3]['photo_model']=='gpt-4.1-2025-04-14'


def test_mcp_dialog_assignment_uses_pinned_channel_and_existing_guarded_service(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.DIALOG_ASSIGN_SCOPE)
    import tenant_operator_assignment as assignment
    profile={'tenant_id':A,'telegram_id':11}
    options={'conversation_id':'synthetic-customer-5101','lead_id':5101}
    seen=[]
    monkeypatch.setattr(assignment,'options',lambda p,data:(seen.append(('read',p,data.copy())) or
        {'allowed':True,'revision':7,'state':'paused','agents':[{'id':AGENT,'name':'Night'}]}))
    monkeypatch.setattr(assignment,'assign',lambda p,data:(seen.append(('write',p,data.copy())) or
        {'agent_id':data['agent_id'],'state':'paused','revision':8,'automatic_replies_enabled':False}))
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_CHANNEL_ID':CA,'actor_id':11,
         'granted_scopes':[mcp.TOOL_SCOPE,mcp.DIALOG_ASSIGN_SCOPE]}
    with db.cursor() as cur:
        preview=mcp._tools(cur,cfg,'support_dialog_agent_options',options)
        assert preview['revision']==7 and preview['agents'][0]['id']==AGENT
    result=mcp._tools(None,cfg,'support_dialog_agent_assign',{
        **options,'agent_id':AGENT,'expected_revision':7,'request_id':str(uuid.uuid4())})
    assert result['state']=='paused' and result['automatic_replies_enabled'] is False
    assert seen[0][2]=={'provider':'connect','channel_id':CA,**options,'manage':True}
    assert seen[1][2]['expected_revision']==7 and seen[1][2]['channel_id']==CA
    with pytest.raises(Exception):
        mcp._tools(None,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_dialog_agent_assign',{
            **options,'agent_id':AGENT,'expected_revision':7,'request_id':str(uuid.uuid4())})


def test_mcp_group_assignment_parity_uses_owner_preview_and_fresh_confirmation(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.DIALOG_ASSIGN_SCOPE)
    import tenant_operator_assignment as assignment
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_CHANNEL_ID':CA,'actor_id':11,
      'granted_scopes':[mcp.TOOL_SCOPE,mcp.DIALOG_ASSIGN_SCOPE]}
    preview={'preview_token':'a'*64,'eligible':[{'kind':'group'}],
      'summary':{'group_eligible':1},'requires_owner_confirmation':True,'automatic_replies_enabled':False}
    seen=[]
    monkeypatch.setattr(assignment,'bulk_preview',lambda profile,channel,agent:(seen.append(('preview',profile,channel,agent)) or preview))
    monkeypatch.setattr(assignment,'bulk_apply',lambda profile,data:(seen.append(('apply',profile,data.copy())) or {'total':1,'assigned':1,'skipped':0}))
    with db.cursor() as cur:
        result=mcp._tools(cur,cfg,'support_dialog_agent_bulk_preview',{'channel_id':CA,'agent_id':AGENT})
        assert result['summary']['group_eligible']==1
        with pytest.raises(Exception):mcp._tools(cur,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_dialog_agent_bulk_preview',{'channel_id':CA,'agent_id':AGENT})
        applied=mcp._tools(cur,cfg,'support_dialog_agent_bulk_assign',{'channel_id':CA,'agent_id':AGENT,
          'preview_token':'a'*64,'batch_id':str(uuid.uuid4()),'confirm_exact_preview':True})
    assert applied['assigned']==1 and seen[0][2:]==(CA,AGENT)
    assert seen[1][2]['confirmed'] is True and seen[1][2]['preview_token']=='a'*64


def test_mcp_case_extraction_status_exposes_only_safe_processing_fields(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.SUPPORT_CASE_SCOPE)
    import tenant_knowledge_candidates_store as cases
    candidate_id=str(uuid.uuid4());secret='https://example.invalid/private?token=secret'
    monkeypatch.setattr(cases,'detail',lambda profile,key:{'candidate':{'id':key,'extraction_id':str(uuid.uuid4()),
      'status':'needs_clarification','version':3,'media_analysis_status':'partial','media_coverage':{'complete':False},
      'media_processing':[{'id':'message-a','status':'partial','code':'video_observation','extraction_status':'completed','complete':False,'url':secret,'observation':'private'}]},'evidence':[]})
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.SUPPORT_CASE_SCOPE]}
    with db.cursor() as cur:
        status=mcp._tools(cur,cfg,'support_case_extraction_status',{'candidate_id':candidate_id})
    assert status['extraction_status']=='completed' and status['media_processing']==[
      {'id':'message-a','status':'partial','code':'video_observation','extraction_status':'completed','complete':False}]
    assert secret not in json.dumps(status) and 'private' not in json.dumps(status)


def test_mcp_support_case_tools_reuse_existing_owner_mutations_and_confirm_exact_delete(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.SUPPORT_CASE_SCOPE)
    import tenant_knowledge_candidates_store as cases
    candidate=str(uuid.uuid4());source_agent=AGENT
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,
         'granted_scopes':[mcp.TOOL_SCOPE,mcp.SUPPORT_CASE_SCOPE]}
    profile={'tenant_id':A,'telegram_id':11};calls=[]
    monkeypatch.setattr(cases,'listing',lambda p,args:(calls.append(('list',p,args.copy())) or {'candidates':[],'next_before':None}))
    monkeypatch.setattr(cases,'detail',lambda p,cid:(calls.append(('detail',p,cid)) or {'id':cid,'outcome':{'status':'unknown'}}))
    monkeypatch.setattr(cases,'mutate',lambda p,cid,data,action:(calls.append((action,p,cid,data.copy())) or {'candidate':{'id':cid}}))
    with db.cursor() as cur:
        assert mcp._tools(cur,cfg,'support_cases_list',{'limit':10})['candidates']==[]
        assert mcp._tools(cur,cfg,'support_case_detail',{'candidate_id':candidate})['outcome']['status']=='unknown'
    mcp._tools(None,cfg,'support_case_edit_publish',{'candidate_id':candidate,'expected_version':3,
        'agent_ids':[source_agent],'fields':{'owner_additions':'Verified owner guidance'}})
    assert calls[-1]==('publish_guidance',profile,candidate,{'expected_version':3,'agent_ids':[source_agent],'owner_additions':'Verified owner guidance'})
    start_message,end_message=str(uuid.uuid4()),str(uuid.uuid4())
    mcp._tools(None,cfg,'support_case_edit_boundaries',{'candidate_id':candidate,'expected_version':4,
        'start_message_id':start_message,'end_message_id':end_message})
    assert calls[-1]==('boundaries',profile,candidate,{'expected_version':4,
        'start_message_id':start_message,'end_message_id':end_message})
    with pytest.raises(Exception):
        mcp._tools(None,cfg,'support_case_edit_boundaries',{'candidate_id':candidate,'expected_version':4,
            'start_message_id':start_message,'end_message_id':end_message,'allow_outside_tenant':True})
    mcp._tools(None,cfg,'support_case_confirm_publish',{'candidate_id':candidate,'expected_version':3,'agent_ids':[source_agent]})
    assert calls[-1][0]=='approve'
    mcp._tools(None,cfg,'support_case_reject_delete',{'candidate_id':candidate,'confirm_candidate_id':candidate,
        'expected_version':3,'linked_knowledge_action':'retain'})
    assert calls[-1][0]=='delete' and calls[-1][3]['confirm_candidate_id']==candidate
    with pytest.raises(Exception):
        mcp._tools(None,cfg,'support_case_edit_publish',{'candidate_id':candidate,'expected_version':3,
            'agent_ids':[source_agent],'fields':{'product':'not an editable primary field'}})
    with pytest.raises(Exception):
        mcp._tools(None,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_cases_list',{})


def test_mcp_support_case_delete_status_reads_only_exact_audit_receipt(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.SUPPORT_CASE_SCOPE)
    target=str(uuid.uuid4());mcp._audit(db.cursor(),A,11,'knowledge_candidate_deleted','knowledge_candidate',target,{'linked_knowledge_action':'none'})
    db.commit()
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.SUPPORT_CASE_SCOPE]}
    with db.cursor() as cur:
        status=mcp._tools(cur,cfg,'support_case_delete_status',{'candidate_id':target})
    assert status['candidate_id']==target and status['deleted'] is True and status['deleted_at']


def test_support_case_mcp_consent_discloses_chatgpt_content_transfer_and_combined_assignment():
    text,button=mcp._consent_text({mcp.SUPPORT_CASE_SCOPE,mcp.DIALOG_ASSIGN_SCOPE,mcp.AGENT_MANAGE_SCOPE})
    assert 'sent to ChatGPT' in text and 'leaves it paused' in text and 'permanently remove one exact agent' in text
    media_text,_=mcp._consent_text({mcp.TOOL_SCOPE,mcp.SUPPORT_MEDIA_SCOPE})
    assert '$10' in media_text and 'exact snapshot' in media_text and 'external providers' in media_text
    assert mcp.SUPPORT_MEDIA_SCOPE in mcp._requested_scopes(mcp.TOOL_SCOPE+' '+mcp.SUPPORT_MEDIA_SCOPE)
    assert button=='Allow support-case management'


def test_mcp_case_delete_unknown_result_is_reported_without_retry_permission(monkeypatch):
    monkeypatch.setattr(mcp,'_authorize_token',lambda request:{'actor_id':11})
    monkeypatch.setattr(mcp,'_tools',lambda *args:(_ for _ in ()).throw(RuntimeError('synthetic post-commit transport failure')))
    app=web.Application();app.router.add_post('/mcp/ai-learning',mcp.mcp)
    async def check():
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/mcp/ai-learning',json={'jsonrpc':'2.0','id':4,'method':'tools/call',
                'params':{'name':'support_case_reject_delete','arguments':{}}})
            payload=await response.json();result=payload['result']
            error=json.loads(result['content'][0]['text'])['error']
            assert result['isError'] is True and error['retry_safety']=='outcome_unknown'
    asyncio.run(check())


def test_mcp_agent_archive_tools_use_scoped_store_and_omit_prompts(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.AGENT_MANAGE_SCOPE)
    import tenant_ai_agents_store as store
    profile={'tenant_id':A,'telegram_id':11};calls=[]
    monkeypatch.setattr(store,'list_agents',lambda p:(calls.append(('list',p)) or [
        {'id':AGENT,'name':'Night','role':'primary','enabled':False,'mode':'manual','model':'gpt-4.1','version':4,'assigned_dialog_count':2,'instructions':'private prompt','scopes':[{'provider':'connect','channel_id':CA,'allow_groups':False}]}]))
    monkeypatch.setattr(store,'delete_agent',lambda p,agent_id,**kwargs:(calls.append(('archive',p,agent_id,kwargs)) or
        {'archived':True,'replacement_agent_id':kwargs['replacement_agent_id'],'transferred_dialogs':2}))
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.AGENT_MANAGE_SCOPE]}
    with db.cursor() as cur:
        listed=mcp._tools(cur,cfg,'support_agents_list',{})
        assert listed['agents']==[{'id':AGENT,'name':'Night','role':'primary','enabled':False,'mode':'manual','model':'gpt-4.1','version':4,'assigned_dialog_count':2,
            'scopes':[{'provider':'connect','channel_id':CA,'allow_groups':False}]}]
        assert 'instructions' not in listed['agents'][0]
    replacement=str(uuid.uuid4())
    result=mcp._tools(None,cfg,'support_agent_archive_transfer',{'agent_id':AGENT,'replacement_agent_id':replacement,'expected_version':4})
    assert result['archived'] and result['transferred_dialogs']==2
    assert calls[-1]==('archive',profile,AGENT,{'replacement_agent_id':replacement,'expected_version':4})
    with pytest.raises(Exception):
        mcp._tools(None,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'support_agent_archive_transfer',
            {'agent_id':AGENT,'replacement_agent_id':replacement,'expected_version':4})


def test_mcp_agent_permanent_delete_requires_preview_version_and_exact_target(db,monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.AGENT_MANAGE_SCOPE)
    import tenant_ai_agents_store as store
    profile={'tenant_id':A,'telegram_id':11};calls=[]
    monkeypatch.setattr(store,'preview_agent_deletion',lambda p,agent_id:(calls.append(('preview',p,agent_id)) or {
        'agent_id':agent_id,'version':7,'confirmation_required':True,'irreversible':True,
        'impact':{'assigned_dialogs':2,'runs':3,'knowledge_sources':1}}))
    monkeypatch.setattr(store,'permanently_delete_agent',lambda p,agent_id,**kwargs:(calls.append(('delete',p,agent_id,kwargs)) or {
        'deleted':True,'agent_id':agent_id,'replayed':False}))
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.AGENT_MANAGE_SCOPE]}
    preview=mcp._tools(None,cfg,'support_agent_delete_preview',{'agent_id':AGENT})
    assert preview['irreversible'] and preview['impact']['runs']==3
    result=mcp._tools(None,cfg,'support_agent_permanent_delete',{
        'agent_id':AGENT,'expected_version':7,'confirm_agent_id':AGENT})
    assert result['deleted']
    assert calls[-1]==('delete',profile,AGENT,{'expected_version':7,'confirm_agent_id':AGENT})
    with pytest.raises(Exception):mcp._tools(None,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},
        'support_agent_permanent_delete',{'agent_id':AGENT,'expected_version':7,'confirm_agent_id':AGENT})


def test_owner_mcp_knowledge_duplicate_preview_archive_rollback_is_scoped_and_idempotent(db, monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.KNOWLEDGE_DEDUP_SCOPE)
    seed_job(db)
    canonical,duplicate=str(uuid.uuid4()),str(uuid.uuid4())
    content='Exact confirmed ERP warehouse instruction.'
    digest=hashlib.sha256(content.encode()).hexdigest()
    for source,title in ((canonical,'Canonical'),(duplicate,'Duplicate')):
        db.execute('''INSERT INTO saas_ai_knowledge_sources(id,tenant_id,agent_id,title,kind,content,content_hash,active,created_by)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,'txt',%s,%s,TRUE,11)''',(source,A,AGENT,title,content,digest))
        db.execute('''INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content)
          VALUES(%s::uuid,%s::uuid,0,%s)''',(A,source,content))
    db.commit()
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE,mcp.KNOWLEDGE_DEDUP_SCOPE]}
    with db.cursor() as cur:
        preview=mcp._tools(cur,cfg,'knowledge_duplicate_preview',{'canonical_id':canonical,'duplicate_ids':[duplicate]})
        assert preview['eligible'] is True and len(preview['precondition'])==64
        request_key=str(uuid.uuid4())
        archived=mcp._tools(cur,cfg,'knowledge_duplicate_archive',{'canonical_id':canonical,'duplicate_ids':[duplicate],
            'precondition':preview['precondition'],'request_key':request_key})
        replay=mcp._tools(cur,cfg,'knowledge_duplicate_archive',{'canonical_id':canonical,'duplicate_ids':[duplicate],
            'precondition':preview['precondition'],'request_key':request_key})
        assert archived['changed_ids']==[duplicate] and replay['idempotent_replay'] is True
        current=mcp._tools(cur,cfg,'knowledge_duplicate_preview',{'canonical_id':canonical,'duplicate_ids':[duplicate]})
        restored=mcp._tools(cur,cfg,'knowledge_duplicate_rollback',{'canonical_id':canonical,'duplicate_ids':[duplicate],
            'precondition':current['precondition'],'request_key':str(uuid.uuid4())})
        assert restored['changed_ids']==[duplicate]
    assert db.execute('SELECT active FROM saas_ai_knowledge_sources WHERE id=%s::uuid',(duplicate,))[0]['active'] is True
    with db.cursor() as cur:
        with pytest.raises(Exception):
            mcp._tools(cur,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'knowledge_duplicate_preview',{'canonical_id':canonical,'duplicate_ids':[duplicate]})


def test_mcp_dialogue_merge_is_provider_proven_private_and_reversible(db, monkeypatch):
    _setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.DIALOGUE_MERGE_SCOPE)
    dialogue_merge.ensure_schema(db)
    jid,lid='994501234567@s.whatsapp.net','80427641475293@lid'
    jid_lead,lid_lead=5101,5102
    for conversation,lead in ((jid,jid_lead),(lid,lid_lead)):
        db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind)
          VALUES(%s::uuid,%s::uuid,%s,%s,'contact')''',(A,CA,conversation,lead))
    for index,(lead,direction,conversation) in enumerate(((jid_lead,'incoming',jid),(lid_lead,'outgoing',lid))):
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw)
          VALUES(%s::uuid,%s::uuid,%s,%s,'connect:'||%s::text,%s,'private message',%s::jsonb)''',
          (str(uuid.uuid4()),A,lead,'connect:identity-'+str(index),CA,direction,
           json.dumps({'conversationId':conversation,'directIdentity':{'jid':jid,'lid':lid}})))
    db.commit()
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_CHANNEL_ID':CA,
         'AI_LEARNING_MCP_CURSOR_SECRET':'test-only-cursor-secret','actor_id':11,
         'granted_scopes':[mcp.TOOL_SCOPE,mcp.DIALOGUE_MERGE_SCOPE]}
    with db.cursor() as cur:
        preview=mcp._tools(cur,cfg,'whatsapp_dialogue_duplicate_candidates',{})['items']
        candidate=next(item for item in preview if item['eligible'])
        assert candidate['canonical']=={'n':1,'incoming':1,'outgoing':0}
        assert candidate['split']=={'n':1,'incoming':0,'outgoing':1}
        assert jid not in json.dumps(candidate) and lid not in json.dumps(candidate)
        before=db.execute('SELECT id,kommo_lead_id,external_id FROM saas_crm_messages WHERE kommo_lead_id=ANY(%s::bigint[]) ORDER BY id',([jid_lead,lid_lead],))
        merge_request=str(uuid.uuid4())
        merged=mcp._tools(cur,cfg,'whatsapp_dialogue_merge',{'operation':'merge','pair_id':candidate['pair_id'],
          'precondition':candidate['precondition'],'request_id':merge_request})
        assert merged['state']=='merged' and merged['logical_only'] and merged['preserved_message_ids']
        replay=mcp._tools(cur,cfg,'whatsapp_dialogue_merge',{'operation':'merge','pair_id':candidate['pair_id'],
          'precondition':candidate['precondition'],'request_id':merge_request})
        assert replay['idempotent_replay'] is True
        assert db.execute('SELECT id,kommo_lead_id,external_id FROM saas_crm_messages WHERE kommo_lead_id=ANY(%s::bigint[]) ORDER BY id',([jid_lead,lid_lead],))==before
        current=mcp._tools(cur,cfg,'whatsapp_dialogue_duplicate_candidates',{'pair_id':candidate['pair_id']})['items'][0]
        rolled=mcp._tools(cur,cfg,'whatsapp_dialogue_merge',{'operation':'rollback','pair_id':candidate['pair_id'],
          'precondition':current['precondition'],'request_id':str(uuid.uuid4())})
        assert rolled['state']=='rolled_back'
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action IN ('ai_dialogue_merge_merge','ai_dialogue_merge_rollback')")[0]['n']==2
    with db.cursor() as cur:
        with pytest.raises(Exception):
            mcp._tools(cur,{**cfg,'granted_scopes':[mcp.TOOL_SCOPE]},'whatsapp_dialogue_merge',{'operation':'merge','pair_id':candidate['pair_id'],
              'precondition':candidate['precondition'],'request_id':str(uuid.uuid4())})
    other='994507654321@s.whatsapp.net'
    db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind)
      VALUES(%s::uuid,%s::uuid,%s,5103,'contact')''',(A,CA,other))
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,raw)
      VALUES(%s::uuid,%s::uuid,5103,'connect:ambiguous','connect:'||%s::text,'incoming',%s::jsonb)''',
      (str(uuid.uuid4()),A,CA,json.dumps({'conversationId':lid,'directIdentity':{'jid':other,'lid':lid}})))
    db.commit()
    with db.cursor() as cur:
        conflicted=mcp._tools(cur,cfg,'whatsapp_dialogue_duplicate_candidates',{'pair_id':candidate['pair_id']})['items'][0]
    assert conflicted['eligible'] is False and 'provider_identity_ambiguous' in conflicted['conflicts']


def test_draft_scope_requires_reauthorization_and_has_accurate_consent(db, monkeypatch):
    token=_setup(monkeypatch,db)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    monkeypatch.setattr(mcp, '_authorize', lambda cur, profile: (A, 11))
    verifier='v'*43
    challenge=mcp._b64(hashlib.sha256(verifier.encode()).digest())
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request: {'role':'owner'}):
            getattr(app.router, 'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            denied=await client.post('/mcp/ai-learning',headers={'Authorization':'Bearer '+token},json={
                'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':'administrator_draft_list','arguments':{}}})
            assert denied.status==403 and (await denied.json())['error']=='Additional OAuth scope required.'
            query={'response_type':'code','client_id':CHATGPT_CLIENT,'redirect_uri':CHATGPT_REDIRECT,
                   'scope':mcp.TOOL_SCOPE+' '+mcp.DRAFT_SCOPE,'state':'drafts','code_challenge':challenge,
                   'code_challenge_method':'S256','resource':'https://crm.test/mcp/ai-learning'}
            consent=await client.get('/oauth/authorize',params=query)
            body=await consent.text()
            assert consent.status==200
            assert 'Allow draft proposal access' in body
            assert 'cannot send messages, publish knowledge, change settings, or apply a prompt' in body
            allowed=await client.post('/oauth/authorize',data={key:value for key,value in query.items() if key != 'response_type'},allow_redirects=False)
            code=parse_qs(urlparse(allowed.headers['Location']).query)['code'][0]
            issued=await client.post('/oauth/token',data={'grant_type':'authorization_code','code':code,
                'code_verifier':verifier,'client_id':CHATGPT_CLIENT,'redirect_uri':CHATGPT_REDIRECT})
            granted=await issued.json()
            assert issued.status==200 and set(granted['scope'].split())=={mcp.TOOL_SCOPE,mcp.DRAFT_SCOPE}
            listed=await client.post('/mcp/ai-learning',headers={'Authorization':'Bearer '+granted['access_token']},json={
                'jsonrpc':'2.0','id':2,'method':'tools/call',
                'params':{'name':'administrator_draft_list','arguments':{}}})
            assert listed.status==200
    asyncio.run(check())


def test_apply_scope_requires_reauthorization_and_advertises_approved_application(db, monkeypatch):
    token=_setup(monkeypatch,db)
    monkeypatch.setattr(mcp, '_authorize', lambda cur, profile: (A, 11))
    verifier='v'*43;challenge=mcp._b64(hashlib.sha256(verifier.encode()).digest())
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request: {'role':'owner'}):
            getattr(app.router, 'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            denied=await client.post('/mcp/ai-learning',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'administrator_apply_agent_prompt','arguments':{'approval_id':str(uuid.uuid4()),'draft_id':str(uuid.uuid4())}}})
            assert denied.status==403 and (await denied.json())['error']=='Additional OAuth scope required.'
            query={'response_type':'code','client_id':CHATGPT_CLIENT,'redirect_uri':CHATGPT_REDIRECT,
                   'scope':mcp.TOOL_SCOPE+' '+mcp.APPLY_SCOPE,'state':'apply','code_challenge':challenge,
                   'code_challenge_method':'S256','resource':'https://crm.test/mcp/ai-learning'}
            consent=await client.get('/oauth/authorize',params=query)
            assert consent.status==200 and 'Allow approved administrator application' in await consent.text()
            listed=await client.post('/mcp/ai-learning',json={'jsonrpc':'2.0','id':2,'method':'tools/list'})
            names={item['name'] for item in (await listed.json())['result']['tools']}
            assert {'administrator_apply_agent_prompt','administrator_rollback_agent_prompt'}<=names
    asyncio.run(check())


@pytest.mark.parametrize('client_id,redirect_uri',[(CHATGPT_CLIENT,CHATGPT_REDIRECT),(mcp.CHATGPT_CLIENT_ID,mcp.CHATGPT_REDIRECT_URI)])
def test_chatgpt_cimd_authorization_code_pkce_and_refresh_rotation(db, monkeypatch, client_id, redirect_uri):
    _setup(monkeypatch,db)
    monkeypatch.setattr(mcp, '_authorize', lambda cur, profile: (A, 11))
    verifier='v'*43
    challenge=mcp._b64(hashlib.sha256(verifier.encode()).digest())
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request: {'role':'owner'}):
            getattr(app.router, 'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            query={'response_type':'code','client_id':client_id,'redirect_uri':redirect_uri,
                   'scope':mcp.TOOL_SCOPE+' '+mcp.OFFLINE_SCOPE,'state':'state','code_challenge':challenge,
                   'code_challenge_method':'S256'}
            consent=await client.get('/oauth/authorize',params=query)
            assert consent.status==200 and 'Allow read-only access' in await consent.text()
            allowed=await client.post('/oauth/authorize',data={key:value for key,value in query.items() if key != 'response_type'},allow_redirects=False)
            assert allowed.status==302, await allowed.text()
            code=parse_qs(urlparse(allowed.headers['Location']).query)['code'][0]
            issued=await client.post('/oauth/token',data={'grant_type':'authorization_code','code':code,
                'code_verifier':verifier,'client_id':client_id,'redirect_uri':redirect_uri})
            token=await issued.json()
            assert issued.status==200 and token['scope']==mcp.TOOL_SCOPE+' '+mcp.OFFLINE_SCOPE and token.get('refresh_token')
            monkeypatch.setenv('AI_LEARNING_MCP_TENANT_ID',str(uuid.UUID(int=102)))
            denied=await client.post('/oauth/token',data={'grant_type':'refresh_token','refresh_token':token['refresh_token'],'client_id':client_id})
            assert denied.status==400
            monkeypatch.setenv('AI_LEARNING_MCP_TENANT_ID',A)
            refreshed=await client.post('/oauth/token',data={'grant_type':'refresh_token','refresh_token':token['refresh_token'],'client_id':client_id})
            rotated=await refreshed.json()
            assert refreshed.status==200 and rotated['access_token'] != token['access_token'] and rotated['refresh_token'] != token['refresh_token']
            reused=await client.post('/oauth/token',data={'grant_type':'refresh_token','refresh_token':token['refresh_token'],'client_id':client_id})
            assert reused.status==400
    asyncio.run(check())


def test_oauth_revocation_invalidates_access_and_refresh_without_disclosure(db, monkeypatch):
    _setup(monkeypatch,db);monkeypatch.setattr(mcp, '_authorize', lambda cur, profile: (A, 11))
    verifier='v'*43;challenge=mcp._b64(hashlib.sha256(verifier.encode()).digest())
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request: {'role':'owner'}):
            getattr(app.router,'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            query={'response_type':'code','client_id':CHATGPT_CLIENT,'redirect_uri':CHATGPT_REDIRECT,
                   'scope':mcp.TOOL_SCOPE+' '+mcp.OFFLINE_SCOPE,'state':'revoke','code_challenge':challenge,'code_challenge_method':'S256'}
            allowed=await client.post('/oauth/authorize',data={key:value for key,value in query.items() if key != 'response_type'},allow_redirects=False)
            code=parse_qs(urlparse(allowed.headers['Location']).query)['code'][0]
            issued=await client.post('/oauth/token',data={'grant_type':'authorization_code','code':code,'code_verifier':verifier,'client_id':CHATGPT_CLIENT,'redirect_uri':CHATGPT_REDIRECT})
            grant=await issued.json();assert grant['refresh_token']
            revoked=await client.post('/oauth/revoke',data={'client_id':CHATGPT_CLIENT,'token':grant['refresh_token'],'token_type_hint':'refresh_token'})
            assert revoked.status==200 and not await revoked.text()
            denied=await client.post('/mcp/ai-learning',headers={'Authorization':'Bearer '+grant['access_token']},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'simulation_runs','arguments':{}}})
            assert denied.status==401
            refreshed=await client.post('/oauth/token',data={'grant_type':'refresh_token','refresh_token':grant['refresh_token'],'client_id':CHATGPT_CLIENT})
            assert refreshed.status==400
            unknown=await client.post('/oauth/revoke',data={'client_id':CHATGPT_CLIENT,'token':'unknown'})
            assert unknown.status==200
    asyncio.run(check())


def test_both_official_cimd_forms_and_exact_callback_binding():
    import pytest
    mcp._chatgpt_client(mcp.CHATGPT_CLIENT_ID,mcp.CHATGPT_REDIRECT_URI)
    assert mcp._chatgpt_client(CHATGPT_CLIENT)==CHATGPT_REDIRECT
    for client,redirect in [
        (CHATGPT_CLIENT,mcp.CHATGPT_REDIRECT_URI),(mcp.CHATGPT_CLIENT_ID,CHATGPT_REDIRECT),
        (CHATGPT_CLIENT,CHATGPT_REDIRECT+'?next=https://evil.test'),(CHATGPT_CLIENT,CHATGPT_REDIRECT+'/'),
        (CHATGPT_CLIENT,CHATGPT_REDIRECT+'#fragment'),(CHATGPT_CLIENT,CHATGPT_REDIRECT.replace('JOSnsTD-M4Nm','other')),
        (CHATGPT_CLIENT.replace('chatgpt.com','chatgpt.com.evil.test'),CHATGPT_REDIRECT),
        (CHATGPT_CLIENT.replace('chatgpt.com','user@chatgpt.com'),CHATGPT_REDIRECT),
        (CHATGPT_CLIENT.replace('chatgpt.com','chatgpt.com:443'),CHATGPT_REDIRECT),
        (CHATGPT_CLIENT+'?x=1',CHATGPT_REDIRECT),(CHATGPT_CLIENT+'#x',CHATGPT_REDIRECT),
        (CHATGPT_CLIENT.replace('https:','http:'),CHATGPT_REDIRECT),
        (CHATGPT_CLIENT.replace('JOSnsTD-M4Nm','%2e%2e'),CHATGPT_REDIRECT),
        ('https://chatgpt.com/oauth/../client.json',CHATGPT_REDIRECT),
        ('https://chatgpt.com/oauth/é/client.json',CHATGPT_REDIRECT)]:
        with pytest.raises(Exception):mcp._chatgpt_client(client,redirect)


def test_observed_authorize_request_reaches_consent_and_rejects_bad_pkce_resource(db,monkeypatch):
    _setup(monkeypatch,db)
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request:{'role':'owner'}):
            getattr(app.router,'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            query={'response_type':'code','client_id':CHATGPT_CLIENT,'redirect_uri':CHATGPT_REDIRECT,
                   'scope':'ai_learning.read offline_access','code_challenge':'a'*43,
                   'code_challenge_method':'S256','resource':'https://crm.test/mcp/ai-learning','state':'synthetic'}
            response=await client.get('/oauth/authorize',params=query)
            assert response.status==200
            assert CHATGPT_CLIENT in await response.text() and CHATGPT_REDIRECT in await response.text()
            for changes in [{'redirect_uri':mcp.CHATGPT_REDIRECT_URI},{'code_challenge_method':'plain'},
                            {'code_challenge':'a'},{'code_challenge':'a'*44},{'resource':'https://evil.test/mcp'},
                            {'scope':'ai_learning.write'},{'response_type':'token'}]:
                response=await client.get('/oauth/authorize',params={**query,**changes});assert response.status==400,changes
    asyncio.run(check())


def test_authorize_still_rejects_non_owner_and_other_tenant(db,monkeypatch):
    _setup(monkeypatch,db)
    async def check():
        for role,tenant in [('member',A),('owner',str(uuid.UUID(int=102)))]:
            monkeypatch.setattr(mcp,'_authorize',lambda cur,profile:(tenant,11))
            app=web.Application()
            app.router.add_post('/oauth/authorize',lambda request:mcp.authorize(request,lambda request:{'role':role}))
            async with TestClient(TestServer(app)) as client:
                response=await client.post('/oauth/authorize',data={'client_id':CHATGPT_CLIENT,
                    'redirect_uri':CHATGPT_REDIRECT,'code_challenge':'a'*43});assert response.status==403
    asyncio.run(check())


def test_code_exchange_still_rejects_wrong_verifier_and_replay(db,monkeypatch):
    _setup(monkeypatch,db);monkeypatch.setattr(mcp,'_authorize',lambda cur,profile:(A,11))
    verifier='v'*43;challenge=mcp._b64(hashlib.sha256(verifier.encode()).digest())
    async def check():
        app=web.Application()
        for method,path,handler in mcp.routes(lambda request:{'role':'owner'}):
            getattr(app.router,'add_'+method.lower())(path,handler)
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/oauth/authorize',data={'client_id':CHATGPT_CLIENT,
                'redirect_uri':CHATGPT_REDIRECT,'code_challenge':challenge},allow_redirects=False)
            code=parse_qs(urlparse(response.headers['Location']).query)['code'][0]
            payload={'grant_type':'authorization_code','client_id':CHATGPT_CLIENT,
                     'redirect_uri':CHATGPT_REDIRECT,'code':code,'code_verifier':verifier}
            bad=await client.post('/oauth/token',data={**payload,'code_verifier':'x'*43});assert bad.status==400
            monkeypatch.setenv('AI_LEARNING_MCP_CHANNEL_ID',str(uuid.UUID(int=202)))
            bad=await client.post('/oauth/token',data=payload);assert bad.status==400
            monkeypatch.setenv('AI_LEARNING_MCP_CHANNEL_ID',CA)
            good=await client.post('/oauth/token',data=payload);assert good.status==200
            replay=await client.post('/oauth/token',data=payload);assert replay.status==400
    asyncio.run(check())
