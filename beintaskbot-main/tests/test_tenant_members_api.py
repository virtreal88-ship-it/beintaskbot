import json
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tenant_members_api import MemberApiContext, handlers


class StoreError(Exception):
    pass


class MembersApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"tenant_id": "company", "telegram_id": 1, "role": "owner"}
        self.member = Mock(return_value=self.profile)
        self.identity = Mock(return_value=22)
        self.name = Mock(return_value="Employee")
        self.send = Mock()
        self.listing = Mock(return_value=[{"telegram_id": 22}])
        self.upsert = Mock(); self.deactivate = Mock()
        self.create = Mock(return_value={"token": "once", "role": "worker"})
        self.accept = Mock(return_value={"status": "pending", "owner_id": 1,
                                         "employee_name": "E", "card_name": "Card"})
        self.requests = Mock(return_value=[])
        self.decide = Mock(return_value={"telegram_id": 22, "approved": True})
        self.accept_email = Mock(return_value={'tenant_id':'company-b','tenant_name':'Other company'})
        self.add_whatsapp = Mock(return_value={'phone':'+994501234567','recognition_enabled':True})
        self.api = handlers(MemberApiContext(self.member, self.identity, self.name, self.send,
            "https://crm.pro.az", self.listing, self.upsert, self.deactivate, self.create,
            self.accept, self.requests, self.decide, StoreError,
            accept_email_invite=self.accept_email,add_member_whatsapp_number=self.add_whatsapp,
            issue_session=lambda tenant_id,actor_id:f'{tenant_id}:{actor_id}'))

    def request(self, method="POST", data=None):
        request = Mock(method=method)
        request.json = AsyncMock(return_value={} if data is None else data)
        request.headers = {'Origin':'https://crm.pro.az'}
        return request

    @staticmethod
    def payload(response):
        return json.loads(response.text)

    async def test_all_owner_routes_guard_before_storage(self):
        self.member.return_value = None
        self.assertEqual((await self.api["members"](self.request("GET"))).status, 403)
        self.assertEqual((await self.api["create_invite"](self.request())).status, 403)
        self.assertEqual((await self.api["invite_requests"](self.request("GET"))).status, 403)
        self.listing.assert_not_called(); self.create.assert_not_called(); self.requests.assert_not_called()

    async def test_member_list_upsert_and_deactivate(self):
        result = self.payload(await self.api["members"](self.request("GET")))
        self.assertEqual(result["members"], [{"telegram_id": 22}])
        await self.api["members"](self.request(data={"telegram_id": "22", "display_name": "A",
            "role": "worker", "permissions": ["tasks"]}))
        self.upsert.assert_called_once_with(tenant_id="company", owner_id=1, telegram_id=22,
            display_name="A", role="worker", permissions=["tasks"])
        await self.api["members"](self.request(data={"action": "deactivate", "telegram_id": 22}))
        self.deactivate.assert_called_once_with(tenant_id="company", owner_id=1, telegram_id=22)
        self.assertEqual(self.listing.call_count, 3)

    async def test_quick_phone_link_requires_same_origin_and_expected_tenant(self):
        result=await self.api['members'](self.request(data={'action':'add_whatsapp_number','telegram_id':22,
            'phone':'+994501234567','expected_tenant_id':'company','expected_user_id':'1'}))
        self.assertEqual(result.status,200)
        self.add_whatsapp.assert_called_once_with(tenant_id='company',owner_id=1,telegram_id=22,phone='+994501234567')
        bad=self.request(data={'action':'add_whatsapp_number','telegram_id':22,'phone':'+994501234567',
            'expected_tenant_id':'other','expected_user_id':'1'})
        self.assertEqual((await self.api['members'](bad)).status,409)
        self.add_whatsapp.assert_called_once()

    async def test_invalid_member_identity_does_not_write(self):
        response = await self.api["members"](self.request(data={"telegram_id": "not-number"}))
        self.assertEqual(response.status, 400)
        self.upsert.assert_not_called()

    async def test_invite_url_and_telegram_link_are_server_derived(self):
        result = self.payload(await self.api["create_invite"](self.request(data={"display_name": "A"})))
        self.assertEqual(result["invite"]["url"], "https://crm.pro.az/invite/once")
        self.create.assert_called_once_with(tenant_id="company", owner_id=1, display_name="A",
                                            role="worker", permissions=[])

    async def test_accept_requires_verified_telegram_and_notifies_owner(self):
        self.identity.return_value = None
        request = self.request(data={"telegram": {"id": 22}, "token": "once"})
        self.assertEqual((await self.api["accept_invite"](request)).status, 401)
        self.accept.assert_not_called()
        self.identity.return_value = 22
        result = self.payload(await self.api["accept_invite"](request))
        self.assertTrue(result["pending"])
        self.accept.assert_called_once_with(token="once", telegram_id=22, display_name="Employee")
        self.assertIn("https://crm.pro.az/app", self.send.call_args.args[1])

    async def test_email_invitation_is_bound_and_switches_to_invited_tenant(self):
        self.profile['email']='worker@example.com'
        request=self.request(data={'token':'one-use-token'});request.headers={'Origin':'https://crm.pro.az'}
        response=await self.api['accept_email_invite'](request)
        self.assertEqual(response.status,200)
        self.accept_email.assert_called_once_with(token='one-use-token',actor_id=1,email='worker@example.com',display_name='')
        self.assertIn('csa_tenant_session',response.cookies)

    async def test_access_decision_notifies_exact_request_identity(self):
        result = self.payload(await self.api["invite_requests"](
            self.request(data={"request_id": "request-1", "action": "approve"})))
        self.assertEqual(result["requests"], [])
        self.decide.assert_called_once_with(tenant_id="company", owner_id=1,
                                            request_id="request-1", approve=True)
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[0], 22)

    def test_bot_wires_all_member_routes_to_one_context(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "member_api" for target in node.targets)]
        self.assertEqual(len(assignments), 1)
        call = assignments[0].value.args[0]
        fields = {item.arg: ast.unparse(item.value) for item in call.keywords}
        self.assertEqual(fields["member"], "_tenant_member_from_request")
        self.assertEqual(fields["platform_error"], "TenantPlatformError")
        rendered = ast.unparse(tree)
        for key in ("members", "create_invite", "accept_invite", "invite_requests"):
            self.assertIn(f"member_api['{key}']", rendered)
