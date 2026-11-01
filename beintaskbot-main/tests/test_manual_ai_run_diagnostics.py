"""Read-only exact-route manual run metadata; no customer/provider data."""
import asyncio
import json
import uuid
from unittest.mock import Mock
import pytest
from aiohttp.test_utils import TestClient,TestServer
from aiohttp import web
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database,db,A,B,CA,PROFILE
import tenant_operator_copilot as copilot
import tenant_manual_ai_run_diagnostics as diag
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def fixture(ready,monkeypatch):
    db,other,generated,data=ready
    copilot.ensure_schema(db)
    request=str(uuid.uuid4());source='connect:'+str(uuid.uuid4())
    result={'text':'PRIVATE CUSTOMER BODY sk-secret https://private.invalid 994000@s.whatsapp.net',
      'source_versions':[{'external_id':source,'media_version':'a'*64,'body_hash':'b'*64,'media_kind':'image','url':'PRIVATE URL'}],
      'media_failure':{'source_id':source,'source_hash':'c'*64,'kind':'image','status':'error','code':'media_unavailable','failure_stage':'visual_analysis'}}
    db.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id,result) VALUES(%s::uuid,%s::uuid,11,41,'hash','v1','unknown',1,'connect',%s,'client',%s::jsonb)",(A,request,CA,json.dumps(result)));db.commit()
    monkeypatch.setattr(diag,'_connect',lambda:db)
    return db,request,source,generated


def args(**kw):return {'channel_id':CA,'lead_id':'41',**kw}


def test_exact_owner_metadata_no_text_no_writes_and_optional_request(fixture,monkeypatch):
    db,request,source,generated=fixture;sql=[];original=db.query
    def record(statement,params=()):sql.append(statement);return original(statement,params)
    monkeypatch.setattr(db,'query',record)
    result=diag.read(PROFILE,args(request_id=request))
    assert len(result['items'])==1 and result['items'][0]['request_id']==request
    assert result['items'][0]['source_versions'][0]['source_id']==source
    assert result['items'][0]['media_failure']['code']=='media_unavailable'
    assert result['items'][0]['dispatch_proof'] is None
    assert not any(x.lstrip().upper().startswith(('INSERT ','UPDATE ','DELETE ','CREATE ','ALTER ')) for x in sql)
    for private in ['PRIVATE','sk-secret','https://','@s.whatsapp.net']:assert private not in json.dumps(result)
    generated.assert_not_called()
    assert diag.read(PROFILE,args(request_id=str(uuid.uuid4())))['items']==[]


@pytest.mark.parametrize('mutation',["UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid", "UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid", "UPDATE saas_tenants SET owner_telegram_id=22 WHERE id=%s::uuid"])
def test_current_canonical_owner_only(fixture,mutation):
    db,*_=fixture;db.execute(mutation,(A,));db.commit()
    with pytest.raises(TenantLinearError) as caught:diag.read(PROFILE,args())
    assert caught.value.status==403


@pytest.mark.parametrize('bad',[{'lead_id':'0'},{'lead_id':str(2**63)},{'lead_id':'41 OR 1=1'},{'limit':'11'},{'limit':'0'},{'request_id':'not-uuid'},{'conversation_id':'private-jid'},{'channel_id':str(uuid.uuid4())}])
def test_invalid_inputs_and_other_channel_fail_closed(fixture,bad):
    with pytest.raises(TenantLinearError):diag.read(PROFILE,args(**bad))


def test_foreign_tenant_and_bounded_page(fixture):
    db,request,*_=fixture
    with pytest.raises(TenantLinearError):diag.read({'tenant_id':B,'telegram_id':22},args())
    for _ in range(12):
        db.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id) VALUES(%s::uuid,%s::uuid,11,41,'hash','v1','unknown',1,'connect',%s,'client')",(A,str(uuid.uuid4()),CA))
    db.commit();result=diag.read(PROFILE,args())
    assert len(result['items'])==10 and result['limited'] is True


def test_persisted_arbitrary_metadata_is_not_returned(fixture):
    db,request,*_=fixture
    raw={'media_failure':dict(source_id='994000@s.whatsapp.net',source_hash='PRIVATE',kind='PRIVATE',status='PRIVATE',code='sk-secret',failure_stage='https://private'),'source_versions':[{'external_id':'994000@s.whatsapp.net','media_version':'PRIVATE'}]}
    db.execute('UPDATE saas_chat_ai_runs SET result=%s::jsonb WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(json.dumps(raw),A,request));db.commit()
    result=diag.read(PROFILE,args())
    assert not any(x in json.dumps(result) for x in ['PRIVATE','sk-secret','https://','@s.whatsapp.net'])
    assert result['items'][0]['source_versions']==[]


def test_existing_technical_get_selects_metadata_mode_and_rejects_wrong_identity(fixture,monkeypatch):
    import tenant_ai_state_api as api
    async def scenario():
        app=web.Application();app.router.add_get('/technical',api.handler(lambda _:PROFILE,Mock(),'technical'))
        async with TestClient(TestServer(app)) as client:
            params=args(expected_tenant_id=A,expected_user_id='11')
            response=await client.get('/technical',params=params)
            assert response.status==200 and (await response.json())['result']['coverage']=='manual_runs_metadata_only'
            response=await client.get('/technical',params={**params,'expected_user_id':'22'})
            assert response.status==409
    asyncio.run(scenario())
