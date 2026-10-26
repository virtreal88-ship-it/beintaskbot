"""Owner-only opt-in catalogue: synthetic key, mocked GET, no generation."""
import asyncio
import hashlib
import json
import threading
import uuid
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from test_tenant_support_inbox import A,B,CA,PROFILE,database,db
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
import tenant_ai_administrator_read as admin
import tenant_chat_ai_settings as settings
import tenant_chat_ai_provider as provider
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def catalogue(db,monkeypatch):
    token=_setup(monkeypatch,db)
    connection=str(uuid.uuid4())
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET oauth_connection_id=%s::uuid',(connection,));db.commit()
    monkeypatch.setattr(settings,'_connect',lambda:db)
    response=Mock(status_code=200,json=Mock(return_value={'data':[{'id':'gpt-6-luna'},{'id':'gpt-4o-mini'},{'id':'sk-not-a-model'}]}))
    get=Mock(return_value=response);post=Mock(side_effect=AssertionError('No generation permitted'))
    monkeypatch.setattr(provider.requests,'get',get);monkeypatch.setattr(provider.requests,'post',post)
    def company_key(profile):
        assert profile=={'tenant_id':A,'telegram_id':11}
        return A,{},'synthetic-company-key'
    monkeypatch.setattr(settings,'_company_key',company_key)
    cur=db.execute('SELECT id::text AS id FROM saas_ai_learning_mcp_tokens')[0]
    cfg={**mcp._require_cfg(),'granted_scopes':[admin.READ_SCOPE],'actor_id':11,
         'oauth_token_id':cur['id'],'oauth_connection_id':connection,'oauth_token_hash':hashlib.sha256(token.encode()).hexdigest()}
    db.commit()
    return token,cfg,get,post


def read(db,cfg,args):
    with db.cursor() as cur:return admin.read(cur,cfg,'company_ai_settings_read',args)


def test_catalogue_opt_in_only_uses_company_get_and_never_returns_key(db,catalogue):
    _,cfg,get,post=catalogue
    ordinary=read(db,cfg,{})
    assert 'provider_models' not in ordinary;get.assert_not_called()
    result=read(db,cfg,{'list_provider_models':True})['provider_models']
    assert result=={'provider':'OpenAI','credential_source':'company','generation_access_verified':False,'model_ids':['gpt-4o-mini','gpt-6-luna'],'status':'listed'}
    assert 'synthetic-company-key' not in json.dumps(result)
    assert get.call_args.args==('https://api.openai.com/v1/models',)
    assert get.call_args.kwargs['headers']['Authorization']=='Bearer synthetic-company-key'
    assert get.call_args.kwargs['allow_redirects'] is False
    assert get.call_args.kwargs['timeout']==(3,10)
    post.assert_not_called()

@pytest.mark.parametrize('violation',['read_scope','owner','revoked','expired','tenant','connection','token_hash'])
def test_current_owner_tenant_and_connection_are_checked_before_network(db,catalogue,violation):
    _,cfg,get,post=catalogue
    if violation=='read_scope':cfg['granted_scopes']=[]
    elif violation=='owner':db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
    elif violation=='revoked':db.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now()')
    elif violation=='expired':db.execute("UPDATE saas_ai_learning_mcp_tokens SET expires_at=now()-interval '1 second'")
    elif violation=='tenant':cfg['AI_LEARNING_MCP_TENANT_ID']=B
    elif violation=='connection':cfg['oauth_connection_id']=str(uuid.uuid4())
    elif violation=='token_hash':cfg['oauth_token_hash']='wrong'
    db.commit()
    with pytest.raises(TenantLinearError):read(db,cfg,{'list_provider_models':True})
    get.assert_not_called();post.assert_not_called()

@pytest.mark.parametrize('args',[{'list_provider_models':1},{'list_provider_models':True,'tenant_id':B},{'list_provider_models':True,'base_url':'https://other.invalid'}])
def test_invalid_arguments_cannot_change_tenant_or_provider(db,catalogue,args):
    _,cfg,get,post=catalogue
    with pytest.raises(TenantLinearError):read(db,cfg,args)
    get.assert_not_called();post.assert_not_called()


def test_provider_errors_are_sanitized_and_not_retried(db,catalogue):
    _,cfg,get,post=catalogue
    get.return_value=Mock(status_code=403,json=Mock(return_value={'error':{'code':'restricted_api_key','message':'PRIVATE provider body'}}))
    result=read(db,cfg,{'list_provider_models':True})['provider_models']
    assert result['reason_code']=='provider_project_access' and result['status']=='unavailable'
    assert 'PRIVATE' not in json.dumps(result)
    get.assert_called_once();post.assert_not_called()


def test_existing_company_connection_missing_does_not_fallback_to_platform(db,catalogue,monkeypatch):
    _,cfg,get,post=catalogue
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-platform-key')
    monkeypatch.setattr(settings,'_company_key',Mock(side_effect=TenantLinearError('missing',409)))
    result=read(db,cfg,{'list_provider_models':True})['provider_models']
    assert result['reason_code']=='company_connection_unavailable'
    get.assert_not_called();post.assert_not_called()


def test_only_company_settings_tool_advertises_opt_in_network_read():
    definitions={v['name']:v for v in admin.definitions()}
    tool=definitions['company_ai_settings_read']
    assert tool['inputSchema']['properties']['list_provider_models']['type']=='boolean'
    assert tool['annotations']=={'readOnlyHint':True,'destructiveHint':False,'openWorldHint':True}
    for name,item in definitions.items():
        if name!='company_ai_settings_read':assert 'list_provider_models' not in item['inputSchema'].get('properties',{})
    with pytest.raises(TenantLinearError):admin.validate({'list_provider_models':True},'agents_read')


def test_authenticated_mcp_opt_in_get_runs_off_event_loop(db,catalogue):
    token,_,get,post=catalogue
    main_thread=threading.get_ident();response=get.return_value
    def guarded_get(*args,**kwargs):
        assert threading.get_ident()!=main_thread
        return response
    get.side_effect=guarded_get
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            result=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'company_ai_settings_read','arguments':{'list_provider_models':True}}},headers={'Authorization':'Bearer '+token})
            assert result.status==200
            payload=json.loads((await result.json())['result']['content'][0]['text'])
            assert payload['provider_models']['model_ids']==['gpt-4o-mini','gpt-6-luna']
    asyncio.run(check());get.assert_called_once();post.assert_not_called()


def test_explicit_false_remains_network_free(db,catalogue):
    _,cfg,get,post=catalogue
    assert 'provider_models' not in read(db,cfg,{'list_provider_models':False})
    get.assert_not_called();post.assert_not_called()


def test_timeout_is_sanitized_without_retry(db,catalogue):
    _,cfg,get,post=catalogue
    get.side_effect=TimeoutError('PRIVATE key echo must not escape')
    result=read(db,cfg,{'list_provider_models':True})['provider_models']
    assert result['reason_code']=='model_list_unavailable' and result['model_ids']==[]
    assert 'PRIVATE' not in json.dumps(result)
    get.assert_called_once();post.assert_not_called()
