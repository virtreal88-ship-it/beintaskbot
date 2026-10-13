import json
import ast
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from tenant_kommo import (KommoContext, TenantKommo, callback_domain, oauth_settings,
                          token_is_expiring)
from tenant_kommo_api import handlers as kommo_handlers


class StoreError(Exception):
    pass


class Response:
    def __init__(self, status=200, payload=None, content=b"x"):
        self.status_code = status
        self._payload = payload or {}
        self.content = content
    def json(self): return self._payload


class KommoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"tenant_id": "company", "telegram_id": 1, "role": "owner",
                        "workflow": {"pipelines": [], "stages": []}}
        self.member = Mock(return_value=self.profile)
        self.logger = Mock()
        self.begin = Mock(return_value="state"); self.consume = Mock()
        self.disconnect = Mock(); self.credentials = Mock(); self.replace = Mock()
        self.connection = Mock(); self.save_tokens = Mock(); self.save_workflow = Mock()
        self.update = Mock(); self.audit = Mock()
        self.service = TenantKommo(KommoContext(
            self.member, "https://crm.pro.az", self.logger, StoreError, self.begin,
            self.consume, self.disconnect, self.credentials, self.replace, self.connection,
            self.save_tokens, self.save_workflow, self.update, self.audit))
        self.service.settings = Mock(return_value=("client", "secret", "https://crm.pro.az/callback"))

    def request(self, method="GET", data=None, query=None):
        request = Mock(method=method)
        request.json = AsyncMock(return_value={} if data is None else data)
        request.rel_url.query = query or {}
        return request

    @staticmethod
    def payload(response): return json.loads(response.text)

    def test_settings_aliases_redirect_and_missing_names(self):
        self.assertEqual(oauth_settings("https://crm.pro.az", {
            "KOMMO_INTEGRATION_ID": "id", "KOMMO_SECRET_KEY": "secret"}),
            ("id", "secret", "https://crm.pro.az/api/platform/integrations/kommo/callback"))
        with self.assertRaisesRegex(StoreError, "KOMMO_OAUTH_CLIENT_SECRET"):
            oauth_settings("https://crm.pro.az", {"KOMMO_CLIENT_ID": "id"}, StoreError)

    def test_domain_and_expiry_are_strict(self):
        self.assertEqual(callback_domain("HTTPS://Demo.Kommo.com/path", StoreError), "demo.kommo.com")
        for value in ("", "evil.example", "kommo.com.evil.example"):
            with self.assertRaises(StoreError): callback_domain(value, StoreError)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(token_is_expiring("bad", now=now))
        self.assertTrue(token_is_expiring((now + timedelta(minutes=2)).isoformat(), now=now))
        self.assertFalse(token_is_expiring((now + timedelta(minutes=4)).isoformat(), now=now))

    async def test_request_refreshes_once_and_retries_unauthorized(self):
        credentials = {"account_domain": "demo.kommo.com", "access_token": "old",
                       "refresh_token": "refresh", "token_expires_at": "2999-01-01T00:00:00+00:00"}
        refreshed = {**credentials, "access_token": "new"}
        self.credentials.side_effect = [credentials, refreshed]
        with patch("tenant_kommo.wait_slot"), \
             patch("tenant_kommo.requests.request", side_effect=[Response(401), Response(200, {"id": 1})]), \
             patch("tenant_kommo.requests.post", return_value=Response(200, {"access_token": "new"})):
            self.assertEqual(await self.service.request("company", "GET", "account"), {"id": 1})
            self.assertEqual(self.credentials.call_count, 2)
            self.replace.assert_called_once()

    async def test_provider_errors_do_not_log_token_response_body(self):
        credentials = {"account_domain": "demo.kommo.com", "access_token": "old",
                       "refresh_token": "refresh", "token_expires_at": "2000-01-01T00:00:00+00:00"}
        response = Response(400)
        response.text = "access_token=must-not-log"
        self.credentials.return_value = credentials
        with patch("tenant_kommo.requests.post", return_value=response):
            with self.assertRaises(StoreError):
                await self.service.request("company", "GET", "account")
        self.assertNotIn("must-not-log", str(self.logger.mock_calls))

    async def test_handlers_require_owner_before_provider_or_store(self):
        self.member.return_value = None
        api = kommo_handlers(self.service.context, self.service)
        self.service.request = AsyncMock()
        for name in ("request", "disconnect", "start", "pipelines", "test", "save_pipelines"):
            response = await api[name](self.request("POST"))
            self.assertEqual(response.status, 403, name)
        self.service.request.assert_not_called()

    async def test_start_and_connection_test_preserve_contract(self):
        api = kommo_handlers(self.service.context, self.service)
        result = self.payload(await api["start"](self.request("POST", {"account_domain": "demo.kommo.com"})))
        self.assertIn("client_id=client", result["authorize_url"])
        self.assertIn("state=state", result["authorize_url"])
        self.begin.assert_called_once_with(tenant_id="company", owner_id=1,
                                           account_domain="demo.kommo.com")
        self.service.request = AsyncMock(return_value={"id": 7, "name": "Demo", "subdomain": "demo"})
        result = self.payload(await api["test"](self.request()))
        self.assertEqual(result["account"], {"id": 7, "name": "Demo", "subdomain": "demo"})

    async def test_pipeline_output_is_bounded_and_normalized(self):
        api = kommo_handlers(self.service.context, self.service)
        remote = {"_embedded": {"pipelines": [{"id": 10, "name": "Sales", "_embedded": {
            "statuses": [{"id": 20, "name": "New"}]}}]}}
        self.service.request = AsyncMock(return_value=remote)
        result = self.payload(await api["pipelines"](self.request()))
        self.assertEqual(result["pipelines"], [{"id": 10, "name": "Sales",
                                               "stages": [{"id": 20, "name": "New"}]}])

    async def test_callback_missing_state_does_not_touch_storage(self):
        api = kommo_handlers(self.service.context, self.service)
        with self.assertRaises(web.HTTPFound) as redirect:
            await api["callback"](self.request(query={}))
        self.assertEqual(redirect.exception.location, "/setup?kommo=invalid")
        self.consume.assert_not_called()

    async def test_callback_uses_tenant_bound_saved_domain_and_saves_tokens(self):
        api = kommo_handlers(self.service.context, self.service)
        self.consume.return_value = {"tenant_id": "company", "account_domain": "demo.kommo.com"}
        response = Response(200, {"access_token": "access", "refresh_token": "refresh"})
        with patch("tenant_kommo_api.requests.post", return_value=response) as exchange:
            with self.assertRaises(web.HTTPFound) as redirect:
                await api["callback"](self.request(query={"state": "once", "code": "code"}))
        self.assertEqual(redirect.exception.location, "/setup?kommo=connected")
        self.consume.assert_called_once_with("once")
        self.save_tokens.assert_called_once_with(tenant_id="company", account_domain="demo.kommo.com",
                                                 token_payload=response._payload)
        self.assertEqual(exchange.call_args.args[0], "https://demo.kommo.com/oauth2/access_token")

    async def test_callback_does_not_show_unexpected_internal_exception(self):
        api = kommo_handlers(self.service.context, self.service)
        self.consume.side_effect = RuntimeError("private implementation detail")
        with self.assertRaises(web.HTTPFound) as redirect:
            await api["callback"](self.request(query={"state": "once", "code": "code"}))
        self.assertNotIn("private", redirect.exception.location)
        self.assertIn("Kommo%20ba%C4%9Flant%C4%B1s%C4%B1", redirect.exception.location)

    async def test_pipeline_save_rejects_foreign_pipeline_before_write(self):
        api = kommo_handlers(self.service.context, self.service)
        self.service.request = AsyncMock(return_value={"_embedded": {"pipelines": [{"id": 10}]}})
        response = await api["save_pipelines"](self.request("POST", {"pipelines": [{"id": 99}]}))
        self.assertEqual(response.status, 400)
        self.save_workflow.assert_not_called()
        self.update.assert_not_called()

    def test_bot_wires_gateway_and_all_oauth_routes(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        rendered = ast.unparse(tree)
        self.assertEqual(rendered.count("TenantKommo(KommoContext("), 1)
        self.assertEqual(rendered.count("tenant_kommo_handlers("), 1)
        self.assertIn("platform_error=TenantPlatformError", rendered)
        for key in ("request", "start", "disconnect", "callback", "pipelines", "save_pipelines", "test"):
            self.assertIn(f"kommo_api['{key}']", rendered)
        gateway = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == "_tenant_kommo_request")
        self.assertIn("_tenant_kommo_service.request", ast.unparse(gateway))


class StagingOAuthSafetyTests(unittest.TestCase):
    def test_staging_oauth_callback_must_stay_on_staging_origin(self):
        origin = "https://beintaskbot-staging.up.railway.app"
        with self.assertRaisesRegex(ValueError, "staging HTTPS host"):
            oauth_settings(origin, {
                "STAGING_MODE": "true",
                "KOMMO_OAUTH_CLIENT_ID": "stage-client",
                "KOMMO_OAUTH_CLIENT_SECRET": "stage-secret",
                "KOMMO_OAUTH_REDIRECT_URI": "https://crm.pro.az/api/platform/integrations/kommo/callback",
            })
        self.assertEqual(oauth_settings(origin, {
                "STAGING_MODE": "true",
                "KOMMO_OAUTH_CLIENT_ID": "stage-client",
                "KOMMO_OAUTH_CLIENT_SECRET": "stage-secret",
            }), ("stage-client", "stage-secret", origin + "/api/platform/integrations/kommo/callback"))
