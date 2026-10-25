import json
import unittest
from unittest.mock import AsyncMock, Mock
import pytest

from tenant_telegram_binding_api import TelegramBindingContext, handler
from tenant_platform_core import TenantPlatformError
from tenant_telegram_binding import complete_binding


class Request:
    def __init__(self, method='GET', origin='https://crm.pro.az'):
        self.method=method; self.headers={'Origin':origin}


class BindingApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.member=Mock(return_value={'tenant_id':'tenant-a','telegram_id':1000000000001})
        self.request=Mock(return_value={'deep_link':'https://t.me/bot?start=bind_once','bound':False})
        self.status=Mock(return_value={'bound':True,'verified_at':'2026-10-25T00:00:00+00:00'})
        self.api=handler(TelegramBindingContext(self.member,self.request,self.status,'https://crm.pro.az',lambda:'bot',ValueError))

    async def test_binding_request_uses_signed_in_employee_not_a_member_telegram_id(self):
        result=await self.api(Request('POST'))
        body=json.loads(result.body)
        self.assertTrue(body['success']); self.assertIn('bind_once',body['deep_link'])
        self.request.assert_called_once_with(tenant_id='tenant-a',actor_id=1000000000001,bot_username='bot')

    async def test_binding_read_and_origin_isolation(self):
        self.assertEqual((await self.api(Request())).status,200)
        self.assertEqual((await self.api(Request('POST','https://other.example'))).status,403)
        self.request.assert_not_called()

    async def test_binding_requires_session(self):
        self.member.return_value=None
        self.assertEqual((await self.api(Request())).status,401)


def test_binding_rejects_groups_and_spoofed_sender_before_database_access():
    with pytest.raises(TenantPlatformError):
        complete_binding(chat_id=-100123, user_id=42, token='one-time')
    with pytest.raises(TenantPlatformError):
        complete_binding(chat_id=42, user_id=43, token='one-time')
