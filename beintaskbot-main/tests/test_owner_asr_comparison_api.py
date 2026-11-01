"""Authenticated owner-action HTTP fences; all execution is mocked."""
import asyncio
import logging
from unittest.mock import Mock
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import tenant_owner_asr_comparison as flow
from tenant_owner_asr_comparison_api import handler

PROFILE={'tenant_id':'synthetic-tenant','telegram_id':11}
SOURCE={'channel_id':'synthetic-channel','conversation_id':'synthetic-chat','message_id':'synthetic-message'}


def test_comparison_origin_identity_strict_payload_and_no_credentials_in_response(monkeypatch):
    preview=Mock(return_value={'can_execute':True,'version':'v1'})
    execute=Mock(return_value={'admitted':True,'can_execute':False,'variants':[]})
    monkeypatch.setattr(flow,'preview',preview);monkeypatch.setattr(flow,'execute',execute)
    async def scenario():
        current={'profile':PROFILE};app=web.Application()
        app.router.add_route('*','/comparison',handler(lambda _:current['profile'],'https://crm.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            headers={'X-Tenant-Id':PROFILE['tenant_id'],'X-User-Id':'11'}
            response=await client.get('/comparison',params=SOURCE);assert response.status==409
            response=await client.get('/comparison',params=SOURCE,headers={**headers,'X-Tenant-Id':'foreign'});assert response.status==409
            preview.assert_not_called()
            response=await client.get('/comparison',params=SOURCE,headers=headers);assert response.status==200
            assert response.headers['Cache-Control']=='no-store'
            preview.assert_called_once_with(PROFILE,SOURCE)
            payload={**SOURCE,'version':'v1','confirmed':True,'max_calls':2,'max_cost_microusd':60000,
                'expected_tenant_id':PROFILE['tenant_id'],'expected_user_id':11}
            assert (await client.post('/comparison',json=payload)).status==403
            assert (await client.post('/comparison',json={**payload,'expected_user_id':99},headers={'Origin':'https://crm.test'})).status==409
            execute.assert_not_called()
            response=await client.post('/comparison',json=payload,headers={'Origin':'https://crm.test'})
            assert response.status==200
            execute.assert_called_once_with(PROFILE,{k:v for k,v in payload.items() if not k.startswith('expected_')})
            current['profile']=None
            assert (await client.get('/comparison',params=SOURCE,headers=headers)).status==403
    asyncio.run(scenario())


def test_unexpected_error_is_not_echoed_or_logged(monkeypatch,caplog):
    monkeypatch.setattr(flow,'execute',Mock(side_effect=RuntimeError('SYNTHETIC_SECRET_KEY_OR_PROVIDER_ERROR')))
    async def scenario():
        app=web.Application();app.router.add_post('/comparison',handler(lambda _:PROFILE,'https://crm.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/comparison',json={'expected_tenant_id':PROFILE['tenant_id'],'expected_user_id':11},headers={'Origin':'https://crm.test'})
            assert response.status==503
            assert 'SYNTHETIC_SECRET' not in await response.text()
    asyncio.run(scenario())
    assert 'SYNTHETIC_SECRET' not in caplog.text
