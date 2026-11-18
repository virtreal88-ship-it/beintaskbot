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


@pytest.mark.asyncio
async def test_unified_pairing_generation_is_immutable_and_keeps_keys_private(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN};endpoint='/v1/sources/'+SOURCE
    try:
        payload={**BINDING,'messaging_generation':'test-g1'}
        response=await client.post(endpoint,json=payload,headers=headers)
        assert response.status==200
        assert set(await response.json())=={'public_key'}
        original=json.loads((tmp_path/SOURCE/'source.json').read_text())
        assert original['messaging']['generation']=='test-g1'
        assert len(original['messaging']['key'])==64
        assert (await client.post(endpoint,json=payload,headers=headers)).status==200
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==original
        assert (await client.post(endpoint,json={**payload,'messaging_generation':'test-g2'},headers=headers)).status==409
        assert json.loads((tmp_path/SOURCE/'source.json').read_text())==original
    finally:await client.close()


@pytest.mark.asyncio
async def test_native_send_requires_explicit_binding_and_exact_scope(tmp_path):
    supervisor,client=await client_for(tmp_path)
    headers={'Authorization':'Bearer '+TOKEN}
    try:
        await client.post('/v1/sources/'+SOURCE,json=BINDING,headers=headers)
        endpoint='/v1/sources/'+SOURCE+'/messages'
        assert (await client.post(endpoint,json={},headers=headers)).status==409
        supervisor.sources[SOURCE]['data']['messaging']={'generation':'g1','key':'a'*64}
        expected={'tenant':BINDING['tenant_id'],'source':BINDING['channel_id'],'account':BINDING['account_jid'],'generation':'g1'}
        for suffix in ('','/group'):
            for key in expected:
                value={**expected,key:'other'}
                assert (await client.post(endpoint+suffix,json={'scope':value},headers=headers)).status==403
        for extra in ({'Origin':'https://crm.pro.az'},{'Cookie':'session=synthetic'}):
            assert (await client.post(endpoint,json={'scope':expected},headers={**headers,**extra})).status==403
        supervisor.configure(SOURCE)
        config=json.loads((tmp_path/SOURCE/'gateway.json').read_text())
        assert config['messaging']['scope']==expected
        assert config['messaging']['key']
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_audio_proxy_diagnosis_distinguishes_stage_without_error_text():
    import asyncio
    from types import SimpleNamespace
    from aiohttp import WSMsgType
    from supervisor import copy_audio
    async def frames(data=b'\x00\x01'):
        yield SimpleNamespace(type=WSMsgType.BINARY,data=data)
    target=SimpleNamespace(send_bytes=AsyncMock())
    assert await copy_audio(frames(),target,'engine')=='engine_closed'
    target.send_bytes.assert_awaited_once_with(b'\x00\x01')
    target.send_bytes.reset_mock()
    assert await copy_audio(frames(b'\x00'),target,'worker')=='worker_invalid_pcm'
    target.send_bytes.assert_not_awaited()
    target.send_bytes.side_effect=asyncio.TimeoutError('private payload')
    assert await copy_audio(frames(),target,'engine')=='engine_send_timeout'
    target.send_bytes.side_effect=RuntimeError('private credentials')
    assert await copy_audio(frames(),target,'worker')=='worker_transport_error'
