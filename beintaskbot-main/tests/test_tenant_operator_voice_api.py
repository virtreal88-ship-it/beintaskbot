import json
import unittest
from unittest.mock import Mock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import tenant_operator_voice_api as api
from tenant_linear_policy import TenantLinearError


class VoiceHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.profile={'tenant_id':'tenant-synthetic','telegram_id':7}
        self.check=Mock()
        self.patcher=patch.object(api,'check',self.check);self.patcher.start()
        def member(request):return self.profile if request.headers.get('X-Synthetic-Login')=='yes' else None
        app=web.Application();app.router.add_post('/preflight',api.handler(member,'https://crm.pro.az',Mock(),'preflight'))
        app.router.add_get('/ws',api.handler(member,'https://crm.pro.az',Mock(),'ws'))
        self.client=TestClient(TestServer(app));await self.client.start_server()
        self.headers={'Origin':'https://crm.pro.az','X-Synthetic-Login':'yes'}
        self.body={'expected_tenant_id':'tenant-synthetic','expected_user_id':'7',
                   'provider':'connect','lead_id':41,'channel_id':'synthetic','conversation_id':'synthetic'}

    async def asyncTearDown(self):
        await self.client.close();self.patcher.stop()

    async def test_preflight_checks_existing_profile_then_reports_disabled(self):
        response=await self.client.post('/preflight',headers=self.headers,json=self.body)
        self.assertEqual(response.status,200);self.assertEqual(await response.json(),{'success':True,'result':{'available':False}})
        self.check.assert_called_once_with(self.profile,{'provider':'connect','lead_id':41,'channel_id':'synthetic','conversation_id':'synthetic'})

    async def test_missing_or_changed_profile_and_origin_never_reach_authority(self):
        for headers,body,status in [({'Origin':'https://crm.pro.az'},self.body,403),
            ({'Origin':'https://evil.example','X-Synthetic-Login':'yes'},self.body,403),
            (self.headers,{**self.body,'expected_tenant_id':'another'},409)]:
            response=await self.client.post('/preflight',headers=headers,json=body);self.assertEqual(response.status,status)
        self.check.assert_not_called()

    async def test_authority_denial_is_returned_and_does_not_open_voice(self):
        self.check.side_effect=TenantLinearError('Нет доступа.',403)
        response=await self.client.post('/preflight',headers=self.headers,json=self.body)
        self.assertEqual(response.status,403)

    async def test_websocket_never_upgrades_and_has_no_provider(self):
        response=await self.client.get('/ws?expected_tenant_id=tenant-synthetic&expected_user_id=7',headers={**self.headers,
            'Connection':'Upgrade','Upgrade':'websocket','Sec-WebSocket-Version':'13','Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ=='})
        self.assertEqual(response.status,503);self.assertNotEqual(response.headers.get('Upgrade'),'websocket')
        self.assertEqual((await response.json())['success'],False);self.check.assert_not_called()

    async def test_websocket_rejects_cross_origin_and_mismatched_identity(self):
        for query,origin,status in [('expected_tenant_id=tenant-synthetic&expected_user_id=7','https://evil.example',403),
                                    ('expected_tenant_id=other&expected_user_id=7','https://crm.pro.az',409)]:
            response=await self.client.get('/ws?'+query,headers={'Origin':origin,'X-Synthetic-Login':'yes'});self.assertEqual(response.status,status)
        self.check.assert_not_called()


if __name__=='__main__':unittest.main()
