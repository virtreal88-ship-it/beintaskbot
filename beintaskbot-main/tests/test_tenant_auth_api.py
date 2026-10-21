import json
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tenant_auth_api import AuthApiContext, handlers, telegram_display_name


class PlatformError(Exception):
    pass


class Request:
    def __init__(self, body=None, *, origin="https://crm.pro.az", cookies=None, query_string=""):
        self.body = body
        self.headers = {"Origin": origin} if origin is not None else {}
        self.cookies = cookies or {}
        self.query_string = query_string
        self.remote = "127.0.0.1"

    async def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def payload(response):
    return json.loads(response.body)


class TenantAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"tenant_id": "company-a", "telegram_id": 7, "role": "owner", "workflow": {"private": True}}
        self.member = Mock(return_value=self.profile)
        self.identity = Mock(return_value=7)
        self.tenants = Mock(return_value=[{"id": "company-a", "name": "Acme", "role": "owner", "status": "active"}])
        self.create = Mock(return_value={"id": "company-new", "name": "New", "status": "onboarding"})
        self.integrations = Mock(return_value=[{"provider": "kommo"}])
        self.members = Mock(return_value=[{"telegram_id": 7}])
        self.cleanup = Mock()
        self.log = Mock()
        self.temp = tempfile.TemporaryDirectory()
        self.context = AuthApiContext(
            member=self.member, telegram_identity=self.identity,
            issue_session=lambda tenant_id, chat_id: f"{tenant_id}:{chat_id}",
            tenants_for_user=self.tenants, create_tenant=self.create,
            list_integrations=self.integrations, list_members=self.members,
            capabilities=lambda profile: {"role": profile["role"]},
            disable_browser_push=self.cleanup, role_permissions={"owner": ["all"]},
            platform_error=PlatformError, origin="https://crm.pro.az",
            cookie_name="tenant_session", cookie_ttl=3600,
            base_dir=self.temp.name, logger=self.log,
        )
        self.api = handlers(self.context)

    def tearDown(self):
        self.temp.cleanup()

    async def test_register_reuses_draft_and_preserves_cookie_contract(self):
        self.tenants.return_value = [{"id": "draft", "name": "Draft", "role": "owner", "status": "onboarding"}]
        response = await self.api["register"](Request({"telegram": {"first_name": "Rüfət"}}))
        self.assertEqual(response.status, 200)
        self.create.assert_not_called()
        self.assertEqual(payload(response)["tenant"]["id"], "draft")
        cookie = response.cookies["tenant_session"]
        self.assertEqual(cookie.value, "draft:7")
        self.assertEqual(cookie["max-age"], "3600")
        self.assertEqual(cookie["secure"], True)
        self.assertEqual(cookie["httponly"], True)
        self.assertEqual(cookie["samesite"], "Lax")

    async def test_register_creates_tenant_and_rejects_unverified_identity(self):
        self.tenants.return_value = []
        response = await self.api["register"](Request({"company_name": "A", "telegram": {"first_name": "Nizami", "last_name": "Qasımov"}}))
        self.assertEqual(response.status, 200)
        self.create.assert_called_once_with(name="A", industry="", owner_telegram_id=7, owner_name="Nizami Qasımov")
        self.identity.return_value = None
        response = await self.api["register"](Request({"telegram": {}}))
        self.assertEqual(response.status, 401)

    async def test_register_does_not_leak_unexpected_storage_error(self):
        self.tenants.side_effect = RuntimeError("database-password")
        response = await self.api["register"](Request({"telegram": {}}))
        self.assertEqual(response.status, 500)
        self.assertNotIn("database-password", response.text)
        self.log.exception.assert_called_once()

    async def test_login_selects_company_and_checks_requested_membership(self):
        self.tenants.return_value = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        response = await self.api["login"](Request({"telegram": {}}))
        self.assertTrue(payload(response)["select_company"])
        self.assertNotIn("tenant_session", response.cookies)
        response = await self.api["login"](Request({"telegram": {}, "tenant_id": "missing"}))
        self.assertEqual(response.status, 403)
        response = await self.api["login"](Request({"telegram": {}, "tenant_id": "b"}))
        self.assertEqual(payload(response)["redirect"], "/setup")

    async def test_login_active_and_onboarding_destinations(self):
        response = await self.api["login"](Request({"telegram": {}}))
        self.assertEqual(payload(response)["redirect"], "/app")
        self.tenants.return_value[0]["status"] = "onboarding"
        response = await self.api["login"](Request({"telegram": {}}))
        self.assertEqual(payload(response)["redirect"], "/setup")

    async def test_existing_email_user_accepts_invite_during_login(self):
        email_auth = SimpleNamespace(login=Mock(return_value={"actor_id": 123, "tenant_id": "company-a"}))
        accept_invite = Mock(return_value={"tenant_id": "company-b", "tenant_name": "Other"})
        account = Mock(return_value={"email": "person@example.com", "display_name": "Person"})
        companies = Mock(side_effect=[
            [{"id": "company-a", "name": "A"}],
            [{"id": "company-b", "name": "B"}],
        ])
        record_login = Mock()
        context = replace(self.context, email_auth=email_auth, accept_email_invite=accept_invite,
                          account_profile=account, tenants_for_user=companies, record_login=record_login)
        api = handlers(context)
        response = await api["email_login"](Request({"email": "person@example.com", "password": "valid", "invite_token": "once"}))
        self.assertEqual(response.status, 200)
        self.assertTrue(payload(response)["invite_accepted"])
        self.assertIn("company-b:123", response.cookies["tenant_session"].value)
        accept_invite.assert_called_once_with(token="once", actor_id=123, email="person@example.com", display_name="Person")
        record_login.assert_called_once_with(tenant_id="company-b", actor_id=123)

    async def test_login_denies_unknown_user_and_reports_platform_outage(self):
        self.tenants.return_value = []
        response = await self.api["login"](Request({"telegram": {}}))
        self.assertEqual(response.status, 403)
        self.tenants.side_effect = PlatformError("PostgreSQL müvəqqəti əlçatan deyil.")
        response = await self.api["login"](Request({"telegram": {}}))
        self.assertEqual(response.status, 503)

    async def test_me_hides_workflow_and_lists_members_only_for_owner(self):
        response = await self.api["me"](Request())
        data = payload(response)
        self.assertNotIn("workflow", data["member"])
        self.assertEqual(data["capabilities"], {"role": "owner"})
        self.assertEqual(data["members"], [{"telegram_id": 7}])
        self.profile["role"] = "worker"
        response = await self.api["me"](Request())
        self.assertEqual(payload(response)["members"], [])

    async def test_logout_requires_origin_and_cleans_only_tenant_cookies(self):
        response = await self.api["logout"](Request(origin=None, cookies={"tenant_push_device": "device"}))
        self.assertEqual(response.status, 403)
        self.cleanup.assert_not_called()
        response = await self.api["logout"](Request(cookies={"tenant_push_device": "device"}))
        self.cleanup.assert_called_once_with(self.profile, "device")
        self.assertIn("tenant_session", response.cookies)
        self.assertIn("tenant_push_device", response.cookies)
        self.assertNotIn("web_session", response.cookies)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_logout_cleanup_failure_does_not_destroy_session(self):
        self.cleanup.side_effect = RuntimeError("secret")
        response = await self.api["logout"](Request(cookies={"tenant_push_device": "device"}))
        self.assertEqual(response.status, 503)
        self.assertNotIn("tenant_session", response.cookies)
        self.assertNotIn("secret", response.text)

    async def test_auth_pages_use_fixed_files_and_no_cache(self):
        docs = Path(self.temp.name) / "docs"
        docs.mkdir()
        (docs / "onboarding.html").write_text("onboarding", encoding="utf-8")
        response = self.api["onboarding_page"](Request())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-cache, max-age=0, must-revalidate")
        response = self.api["app_page"](Request())
        self.assertEqual(response.status, 404)

    async def test_unauthenticated_connect_sso_redirects_to_product_login_and_preserves_request(self):
        self.api = handlers(replace(self.context,sso=object()))
        self.member.return_value = None
        handler = self.api["sso_authorize"]
        with self.assertRaises(Exception) as raised:
            await handler(Request(query_string="client_id=beintaskbot-connect&state=abc123"))
        self.assertEqual(raised.exception.status, 302)
        self.assertEqual(raised.exception.location,
                         "/connector/login?next=%2Fapi%2Fplatform%2Fsso%2Fauthorize%3Fclient_id%3Dbeintaskbot-connect%26state%3Dabc123")

    def test_display_name_is_bounded_and_has_fallback(self):
        self.assertEqual(telegram_display_name({}), "İstifadəçi")
        self.assertEqual(telegram_display_name({"first_name": " A ", "last_name": " B "}), "A B")
        self.assertEqual(len(telegram_display_name({"first_name": "x" * 200})), 120)

    def test_bot_routes_use_extracted_handlers_only(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn('tenant_auth_handlers(AuthApiContext(', source)
        for route, handler in (("/api/platform/register", "register"), ("/api/platform/login", "login"),
                               ("/api/platform/logout", "logout"), ("/api/platform/me", "me"),
                               ("/connector/", "connector_page"),
                               ("/connector/login", "auth_page"),
                               ("/app", "app_page")):
            self.assertTrue(any(f'"{route}", auth_api[{quote}{handler}{quote}]' in source for quote in ('"', "'")))
        for legacy in ("handle_platform_register", "handle_platform_login", "handle_platform_logout",
                       "handle_platform_me", "serve_platform_onboarding", "redirect_platform_app"):
            self.assertNotIn(f"def {legacy}", source)


if __name__ == "__main__":
    unittest.main()
