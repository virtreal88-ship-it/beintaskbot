"""Real local HTTP, synthetic source credentials; no provider or production DB."""
import asyncio
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import tenant_whatsapp_native_api as api
from tenant_linear_policy import TenantLinearError


def test_private_delivery_gate_and_post_commit_response(monkeypatch):
    async def run():
        logger,member=Mock(),Mock()
        authenticate=Mock(return_value={'managed':True})
        store=Mock(return_value={'state':'stored','sequence':7})
        monkeypatch.setattr(api.tenant_browser_calls,'gateway_auth',authenticate)
        monkeypatch.setattr(api.inbox,'store_delivery',store)
        monkeypatch.setattr(api,'SLOTS',asyncio.Semaphore(2))
        app=web.Application(client_max_size=api.inbox.MAX_DELIVERY)
        app.router.add_post('/messages',api.handler(member,'https://example.invalid',logger))
        async with TestClient(TestServer(app)) as client:
            monkeypatch.delenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX',raising=False)
            response=await client.post('/messages',json={})
            assert response.status==404
            authenticate.assert_not_called();store.assert_not_called()
            monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX','1')
            for headers in ({'Origin':'https://example.invalid'},{'Cookie':'session=test'}):
                response=await client.post('/messages',json={},headers=headers)
                assert response.status==403
            authenticate.assert_not_called()
            for managed in (False,None):
                authenticate.return_value={'managed':managed}
                assert (await client.post('/messages',json={})).status==403
            authenticate.return_value={'managed':True}
            assert (await client.post('/messages',data='{}')).status==400
            assert (await client.post('/messages',data=b'x'*(api.inbox.MAX_DELIVERY+1),headers={'Content-Type':'application/json'})).status==413
            store.assert_not_called()
            response=await client.post('/messages',json={},headers={'Authorization':'Bearer synthetic'})
            assert response.status==200 and await response.json()=={'state':'stored','sequence':7}
            assert response.headers['Cache-Control']=='no-store'
            assert store.call_args.args==('Bearer synthetic',b'{}')
            store.side_effect=TenantLinearError('native_source_changed',409)
            assert (await client.post('/messages',json={})).status==409
            store.side_effect=RuntimeError('SYNTHETIC_PRIVATE_PAYLOAD')
            response=await client.post('/messages',json={})
            assert response.status==503 and 'SYNTHETIC_PRIVATE_PAYLOAD' not in await response.text()
            assert logger.warning.call_args.args==('native_whatsapp_delivery_failed stage=store code=internal',)
            member.assert_not_called()
    asyncio.run(run())


def test_inbox_saturation_does_not_use_call_slots(monkeypatch):
    async def run():
        monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX','1')
        monkeypatch.setattr(api,'SLOTS',asyncio.Semaphore(0))
        auth=Mock();monkeypatch.setattr(api.tenant_browser_calls,'gateway_auth',auth)
        app=web.Application();app.router.add_post('/messages',api.handler(Mock(),'https://example.invalid',Mock()))
        async with TestClient(TestServer(app)) as client:
            assert (await client.post('/messages',json={})).status==429
            auth.assert_not_called()
    asyncio.run(run())
