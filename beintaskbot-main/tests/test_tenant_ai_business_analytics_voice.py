import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_ai_business_analytics_voice as voice_api
from tenant_linear_policy import TenantLinearError


class BusinessAnalystVoiceHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.profile = {'tenant_id': 'tenant-synthetic', 'telegram_id': 7, 'role': 'owner'}
        self.logger = Mock()

        def member(request):
            return self.profile if request.headers.get('X-Synthetic-Login') == 'yes' else None

        app = web.Application()
        app.router.add_post('/preflight', voice_api.handler(member, 'https://crm.example', self.logger, 'preflight'))
        app.router.add_get('/ws', voice_api.handler(member, 'https://crm.example', self.logger, 'ws'))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {'Origin': 'https://crm.example', 'X-Synthetic-Login': 'yes'}
        self.body = {'expected_tenant_id': 'tenant-synthetic', 'expected_user_id': '7'}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_preflight_checks_exact_owner_session_without_opening_provider(self):
        authority = Mock()
        with patch.object(voice_api.AnalyticsVoiceAuthority, 'create', new=AsyncMock(return_value=authority)) as create:
            response = await self.client.post('/preflight', headers=self.headers, json=self.body)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {'success': True, 'result': {'available': True}})
        create.assert_awaited_once()
        self.assertEqual(create.await_args.args[0], self.profile)
        self.assertTrue(callable(create.await_args.args[1]))
        authority.assert_not_called()

    async def test_preflight_rejects_wrong_origin_identity_and_body_fields(self):
        with patch.object(voice_api.AnalyticsVoiceAuthority, 'create', new=AsyncMock()) as create:
            for headers, body, status in [
                ({**self.headers, 'Origin': 'https://evil.example'}, self.body, 403),
                (self.headers, {**self.body, 'expected_user_id': '8'}, 409),
                (self.headers, {**self.body, 'route': 'customer'}, 400),
            ]:
                response = await self.client.post('/preflight', headers=headers, json=body)
                self.assertEqual(response.status, status)
            create.assert_not_awaited()

    async def test_owner_pilot_or_consent_failure_stays_closed(self):
        with patch.object(voice_api.AnalyticsVoiceAuthority, 'create',
                          new=AsyncMock(side_effect=TenantLinearError('voice unavailable', 403))):
            response = await self.client.post('/preflight', headers=self.headers, json=self.body)
        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())['success'], False)

    async def test_websocket_requires_exact_identity_query_and_delegates_to_session_gateway(self):
        with patch.object(voice_api, 'open_server_session', new=AsyncMock(return_value=web.Response(status=503))) as opened:
            response = await self.client.get('/ws?expected_tenant_id=tenant-synthetic&expected_user_id=7', headers=self.headers)
        self.assertEqual(response.status, 503)
        opened.assert_awaited_once()
        bad = await self.client.get('/ws?expected_tenant_id=tenant-synthetic&expected_user_id=7&route=customer',
                                    headers=self.headers)
        self.assertEqual(bad.status, 403)
        self.assertEqual(opened.await_count, 1)


if __name__ == '__main__':
    unittest.main()
