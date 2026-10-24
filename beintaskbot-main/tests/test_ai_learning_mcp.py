"""Regression coverage for the externally consumable, read-only Learning MCP."""
import asyncio
import hashlib
import logging
import os
import uuid
import pytest
from urllib.parse import parse_qs, urlparse

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_tenant_support_inbox import A, CA, PROFILE, database, db, seed_job, seed_simulation_run
import tenant_ai_learning_mcp as mcp
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
            assert mcp.OFFLINE_SCOPE in data['scopes_supported']
            assert mcp.KNOWLEDGE_DEDUP_SCOPE in data['scopes_supported']
            protected=await client.get('/.well-known/oauth-protected-resource/mcp/ai-learning')
            assert (await protected.json())['resource']=='https://crm.test/mcp/ai-learning'
    asyncio.run(check())


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
