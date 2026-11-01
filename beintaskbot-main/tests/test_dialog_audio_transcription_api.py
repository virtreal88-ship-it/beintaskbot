"""Authenticated ASR-only HTTP contract, all execution mocked."""
import asyncio
import logging
from unittest.mock import Mock
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import tenant_dialog_audio_transcription as flow
from tenant_dialog_audio_transcription_api import handler

PROFILE={'tenant_id':'synthetic-tenant','telegram_id':11}
SOURCE={'channel_id':'synthetic','conversation_id':'synthetic','message_id':'connect:synthetic','message_row_id':'synthetic-row'}


def test_origin_identity_readonly_get_and_post_contract(monkeypatch):
    preview=Mock(return_value={'state':'ready','cost_token':'opaque'})
    execute=Mock(return_value={'state':'completed','requested_attempt':{'id':'same-operation'}})
    monkeypatch.setattr(flow,'preview',preview);monkeypatch.setattr(flow,'execute',execute)
    async def scenario():
        app=web.Application();current={'profile':PROFILE}
        app.router.add_route('*','/audio',handler(lambda _:current['profile'],'https://fixture.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/audio',params=SOURCE)).status==409
            preview.assert_not_called()
            response=await client.get('/audio',params=SOURCE,headers={'X-Tenant-Id':PROFILE['tenant_id'],'X-User-Id':'11'})
            assert response.status==200 and response.headers['Cache-Control']=='no-store'
            preview.assert_called_once_with(PROFILE,SOURCE);execute.assert_not_called()
            body={**SOURCE,'action':'transcribe','cost_token':'opaque','source_version':'v','idempotency_key':'uuid','expected_tenant_id':PROFILE['tenant_id'],'expected_user_id':11}
            assert (await client.post('/audio',json=body)).status==403
            assert (await client.post('/audio',json={**body,'expected_user_id':99},headers={'Origin':'https://fixture.test'})).status==409
            execute.assert_not_called()
            response=await client.post('/audio',json=body,headers={'Origin':'https://fixture.test'})
            assert response.status==200 and (await response.json())['result']['requested_attempt']['id']=='same-operation'
            current['profile']=None
            assert (await client.get('/audio',params=SOURCE,headers={'X-Tenant-Id':PROFILE['tenant_id'],'X-User-Id':'11'})).status==403
    asyncio.run(scenario())


def test_unexpected_provider_error_not_returned_or_logged(monkeypatch,caplog):
    monkeypatch.setattr(flow,'execute',Mock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_PROVIDER_KEY')))
    async def scenario():
        app=web.Application();app.router.add_post('/audio',handler(lambda _:PROFILE,'https://fixture.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/audio',json={'expected_tenant_id':PROFILE['tenant_id'],'expected_user_id':11},headers={'Origin':'https://fixture.test'})
            assert response.status==503 and 'SYNTHETIC_PRIVATE' not in await response.text()
    asyncio.run(scenario());assert 'SYNTHETIC_PRIVATE' not in caplog.text


def test_resource_409_preserves_verified_scope_but_wrong_identity_does_not(monkeypatch):
    monkeypatch.setattr(flow,'preview',Mock(side_effect=flow.AudioError('source_changed','Обновите источник.',409)))
    async def scenario():
        app=web.Application();app.router.add_get('/audio',handler(lambda _:PROFILE,'https://fixture.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            good=await client.get('/audio',params=SOURCE,headers={'X-Tenant-Id':PROFILE['tenant_id'],'X-User-Id':'11'})
            assert good.status==409 and (await good.json())['scope_verified'] is True
            wrong=await client.get('/audio',params=SOURCE,headers={'X-Tenant-Id':'another-company','X-User-Id':'11'})
            assert wrong.status==409 and not (await wrong.json()).get('scope_verified')
    asyncio.run(scenario())
