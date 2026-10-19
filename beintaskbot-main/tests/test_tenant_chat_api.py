import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tenant_chat_api import ChatApiContext, handlers


class PlatformError(RuntimeError):
    pass


class Policy:
    def __init__(self, profile):
        self.profile = profile

    def allows(self, permission):
        return permission in self.profile.get("permissions", [])

    def can_access_deal(self, deal):
        return bool(deal and deal.get("tenant_id") == self.profile.get("tenant_id")
                    and deal.get("allowed", True))


class Request:
    def __init__(self, **query):
        self.rel_url = SimpleNamespace(query=query)


class ChatApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"tenant_id": "company-a", "telegram_id": 20,
                        "permissions": ["customers"]}
        self.member = Mock(return_value=self.profile)
        self.deal = Mock(return_value={"tenant_id": "company-a", "allowed": True})
        self.messages = Mock(return_value=[{"external_id": "m1", "body": "Salam"}])
        self.sync = AsyncMock(return_value=[])
        self.logger = Mock()
        self.api = handlers(ChatApiContext(
            member=self.member, policy=Policy, get_deal=self.deal,
            list_messages=self.messages, sync_chat=self.sync,
            platform_error=PlatformError, logger=self.logger,
        ))

    @staticmethod
    def body(response):
        return json.loads(response.body)

    async def test_session_and_module_are_required(self):
        self.member.return_value = None
        self.assertEqual((await self.api["chat"](Request(lead_id="1"))).status, 403)
        self.member.return_value = {**self.profile, "permissions": []}
        self.assertEqual((await self.api["chat"](Request(lead_id="1"))).status, 403)
        self.deal.assert_not_called()

    async def test_lead_id_is_bounded_before_storage(self):
        for value in (None, "bad", "0", "-1"):
            with self.subTest(value=value):
                self.assertEqual((await self.api["chat"](Request(lead_id=value))).status, 400)
        self.deal.assert_not_called()

    async def test_cached_chat_cannot_bypass_deal_scope(self):
        self.deal.return_value = {"tenant_id": "company-a", "allowed": False}
        response = await self.api["chat"](Request(lead_id="7"))
        self.assertEqual(response.status, 404)
        self.messages.assert_not_called()

    async def test_cached_history_keeps_response_contract(self):
        response = await self.api["chat"](Request(lead_id="7"))
        self.assertEqual(response.status, 200)
        self.assertEqual(self.body(response), {
            "success": True, "messages": [{"external_id": "m1", "body": "Salam"}],
            "stale": False, "warning": "",
        })
        self.deal.assert_called_once_with(tenant_id="company-a", kommo_lead_id=7)
        self.messages.assert_called_once_with(tenant_id="company-a", kommo_lead_id=7)
        self.sync.assert_not_awaited()

    async def test_refresh_failure_returns_saved_history_as_stale(self):
        self.sync.side_effect = PlatformError("Kommo müvəqqəti əlçatan deyil.")
        response = await self.api["chat"](Request(lead_id="7", refresh="true"))
        body = self.body(response)
        self.assertEqual(response.status, 200)
        self.assertTrue(body["stale"])
        self.assertEqual(body["warning"], "Kommo müvəqqəti əlçatan deyil.")
        self.messages.assert_called_once()

    async def test_connect_native_history_refresh_does_not_call_kommo(self):
        self.deal.return_value = {"tenant_id": "company-a", "allowed": True,
                                  "raw": {"source": "connect", "conversation_id": "15550000000@s.whatsapp.net"}}
        response = await self.api["chat"](Request(lead_id="7", refresh="true"))
        body = self.body(response)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["messages"], [{"external_id": "m1", "body": "Salam"}])
        self.assertFalse(body["stale"])
        self.sync.assert_not_awaited()
        self.messages.assert_called_once_with(tenant_id="company-a", kommo_lead_id=7)

    async def test_unexpected_refresh_error_does_not_leak_details(self):
        self.sync.side_effect = RuntimeError("PRIVATE_TOKEN")
        response = await self.api["chat"](Request(lead_id="7", refresh="1"))
        body = self.body(response)
        self.assertEqual(response.status, 200)
        self.assertTrue(body["stale"])
        self.assertNotIn("PRIVATE_TOKEN", body["warning"])
        self.logger.exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
