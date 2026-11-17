import json
import uuid
from unittest.mock import AsyncMock,Mock
import pytest
from aiohttp.test_utils import TestServer,TestClient
from supervisor import Supervisor,make_app,binding

SOURCE=str(uuid.UUID(int=1));TOKEN='s'*48
BINDING={'tenant_id':str(uuid.UUID(int=2)),'channel_id':str(uuid.UUID(int=3)),
         'account_jid':'123456789@s.whatsapp.net','gateway_token':'x'*48}


async def client_for(tmp_path):
    supervisor=Supervisor(tmp_path,TOKEN,'https://worker.invalid','never-executed')
    supervisor.start=AsyncMock();supervisor.stop=AsyncMock();supervisor.launch=Mock();supervisor.engine=AsyncMock(return_value={'connected':False,'account_jid':'','qr':''})
    client=TestClient(TestServer(make_app(supervisor)));await client.start_server()
    return supervisor,client


@pytest.mark.asyncio
async def test_private_control_rejects_browser_tokens_and_legacy_urls(tmp_path):
    supervisor,client=await client_for(tmp_path)
    try:
        for extra in ({},{'Authorization':'Bearer wrong'},{'Authorization':'Bearer '+TOKEN,'Origin':'https://crm.pro.az'},
                      {'Authorization':'Bearer '+TOKEN,'Cookie':'auth=1'}):
            response=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=extra)
            assert response.status==403
        assert not supervisor.sources
        response=await client.get('/api/sessions',headers={'Authorization':'Bearer '+TOKEN})
        assert response.status==404
    finally:await client.close()


@pytest.mark.asyncio
async def test_source_provision_is_durable_immutable_and_never_returns_secrets(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN}
    try:
        first=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=headers)
        result=await first.json();assert first.status==200 and set(result)=={'public_key'} and len(result['public_key'])==64
        saved=json.loads((tmp_path/SOURCE/'source.json').read_text());supervisor.validate_saved(SOURCE,saved)
        repeated=await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=headers)
        assert await repeated.json()==result
        for field in ('tenant_id','channel_id','gateway_token','account_jid'):
            changed={**BINDING,field:str(uuid.UUID(int=9)) if field.endswith('_id') else 'z'*48 if field=='gateway_token' else '987654321@s.whatsapp.net'}
            response=await client.post('/v1/sources/'+SOURCE,json=changed,headers=headers)
            assert response.status==409
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==saved
        other=await client.post('/v1/sources/'+str(uuid.UUID(int=10)),json={**BINDING,'channel_id':str(uuid.UUID(int=10))},headers=headers)
        assert other.status==409
        assert len(supervisor.sources)==1
    finally:await client.close()


def test_only_direct_number_and_uuid_binding_are_accepted():
    from aiohttp import web
    for changed in ({'account_jid':'123456@g.us'},{'account_jid':'123456@lid'},{'tenant_id':'../other'},
                    {'gateway_token':'short'},{'arbitrary_url':'https://external.invalid'}):
        with pytest.raises(web.HTTPBadRequest):binding({**BINDING,**changed})
