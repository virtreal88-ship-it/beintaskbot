import json
import asyncio
import unittest
from cryptography.fernet import Fernet
from urllib.error import HTTPError
from unittest.mock import AsyncMock, MagicMock, patch

import tenant_beinsystems_operator as operator
import tenant_beinsystems_operator_api as operator_api
import tenant_beinsystems_operator_access as access
import tenant_beinsystems_operator_ai_tools as ai_tools
import tenant_beinsystems_operator_company_import as company_import
from tenant_beinsystems_operator_schema import migrate_operator_identity_links
from tenant_member_store import _EDITOR_PERMISSION_KEYS
from tenant_platform_common import _permissions
from tenant_policy import TenantPolicy


TENANT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.payload[:size]

    def close(self):
        pass


class FakeOpener:
    def __init__(self, payload):
        self.response = FakeResponse(payload)
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return self.response


class FakeHTTPErrorOpener:
    def __init__(self, status, payload, headers=None):
        self.status = status
        self.response = FakeResponse(payload)
        self.headers = headers
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        raise HTTPError(
            request.full_url, self.status, "Unauthorized", self.headers, self.response)


class FakeCursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))


class BeinSystemsOperatorTests(unittest.TestCase):
    def test_link_connection_scope_rejects_unconfigured_and_malformed_metadata(self):
        instance = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        for row in (None, {}, {"metadata": None}, {"metadata": []},
                    {"metadata": {"generation": "g"}},
                    {"metadata": {"generation": "g", "connection_instance_id": ""}},
                    {"metadata": {"generation": "g", "connection_instance_id": "not-a-uuid"}},
                    {"metadata": {"generation": "", "connection_instance_id": instance}},
                    {"metadata": {"generation": "  ", "connection_instance_id": instance}},
                    {"metadata": {"generation": 7, "connection_instance_id": instance}}):
            with self.subTest(row=row):
                self.assertEqual(operator._link_connection_scope(row), (None, None))
        self.assertEqual(operator._link_connection_scope({"metadata": {
            "generation": "exact-generation", "connection_instance_id": instance.upper()}}),
            ("exact-generation", instance))

    def test_operator_login_401_is_marked_as_provider_auth_failure(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(lambda _request: profile, "https://crm.example", MagicMock())
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "login", "login": "operator", "password": "bad-password",
             })), \
             patch.object(operator, "connect_with_password", side_effect=operator.OperatorError(
                 "Operator не принял логин и пароль.", 401)):
            response = asyncio.run(handler(request))
        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers["X-Tenant-Auth-Source"], "operator")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_integration_handler_reports_upstream_http_status_without_provider_body(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        provider_error = operator.OperatorError(
            "Operator сообщил Bearer invalid_token (upstream HTTP 401).",
            502, upstream_http_status=401, upstream_error_code="invalid_token")
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "test", "search": "synthetic-account",
             })), \
             patch.object(operator, "verify_accounts_access", side_effect=provider_error) as verify:
            response = asyncio.run(handler(request))
        body = json.loads(response.text)
        self.assertEqual(response.status, 502)
        self.assertEqual(body["upstream_http_status"], 401)
        self.assertEqual(body["upstream_error_code"], "invalid_token")
        self.assertIn("invalid_token", body["error"])
        self.assertNotIn("synthetic-jwt", response.text)
        self.assertNotIn("private provider detail", response.text)
        self.assertEqual(response.headers["X-Tenant-Auth-Source"], "operator")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        verify.assert_called_once_with(profile, "synthetic-account")
    def test_integration_handler_test_action_checks_accounts_with_pending_token_contract(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        verification = {"source": "pending", "request_accepted": True,
                        "access_confirmed": False, "account_read": False,
                        "result_count": 0, "match_found": False}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "test", "search": "synthetic-account",
             })), \
             patch.object(operator, "verify_accounts_access", return_value=verification) as verify, \
             patch.object(operator, "status", return_value={
                 "status": "not_connected", "pending_auth_received": True,
                 "pending_auth_access_confirmed": True,
             }):
            response = asyncio.run(handler(request))
        body = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["verification"], verification)
        self.assertEqual(body["integration"]["status"], "not_connected")
        self.assertNotIn("synthetic-account", response.text)
        self.assertNotIn("synthetic-jwt", response.text)
        verify.assert_called_once_with(profile, "synthetic-account")

    def test_integration_handler_login_check_returns_only_bounded_accounts_result(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        verification = {
            "source": "fresh_login", "request_accepted": True,
            "access_confirmed": True, "account_read": True, "result_count": 1,
            "match_found": True, "accounts": ["beinsystems"], "token_stored": False,
        }
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "login_check", "login": "synthetic-user@account",
                 "password": "synthetic-password", "search": "beinsystems",
             })), \
             patch.object(operator, "login_and_read_accounts",
                          return_value=verification) as check, \
             patch.object(operator, "status", return_value={
                 "status": "not_connected", "pending_auth_received": True,
             }):
            response = asyncio.run(handler(request))
        body = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["verification"], verification)
        self.assertEqual(body["integration"]["status"], "not_connected")
        self.assertNotIn("synthetic-password", response.text)
        self.assertNotIn("fresh-sso-jwt", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        check.assert_called_once_with(
            profile, "synthetic-user@account", "synthetic-password", "beinsystems")


    def test_integration_handler_test_action_requires_bounded_search(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={"action": "test"})), \
             patch.object(operator, "verify_accounts_access") as verify:
            response = asyncio.run(handler(request))
        self.assertEqual(response.status, 400)
        verify.assert_not_called()

    def test_account_catalog_get_and_import_are_explicit_same_origin_actions(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        query = {"expected_tenant_id": TENANT, "expected_user_id": "7"}
        handler = operator_api.accounts_handler(lambda _request: profile, "https://crm.example", MagicMock())
        get_request = MagicMock(method="GET", query=query)
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "imported_accounts", return_value=[{"account":"local-1"}]) as catalog, \
             patch.object(operator, "read") as provider_read:
            response = asyncio.run(handler(get_request))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["accounts"], [{"account":"local-1"}])
        catalog.assert_called_once_with(profile, search="")
        provider_read.assert_not_called()

        post_request = MagicMock(method="POST")
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={"action":"import"})), \
             patch.object(operator, "import_accounts", return_value={
                 "imported":137,"activated":False,"identity":{"identifier":"synthetic","role":"admin"},
                 "status":{"status":"connected"},
             }) as importer:
            response = asyncio.run(handler(post_request))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["imported"], 137)
        importer.assert_called_once_with(profile)

    def test_dialog_company_handler_checks_current_context_and_stays_local(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        query = {"channel_id":"bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
                 "conversation_id":"synthetic-dialog","expected_tenant_id":TENANT,
                 "expected_user_id":"7"}
        handler = operator_api.dialog_company_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        request = MagicMock(method="GET", query=query)
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "dialog_company_binding",
                          return_value={"company_id":"company-id","company_name":"Synthetic"}) as read_local, \
             patch.object(operator, "_request_operator") as provider:
            response = asyncio.run(handler(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["binding"]["company_name"], "Synthetic")
        read_local.assert_called_once_with(profile, channel_id=query["channel_id"],
            conversation_id=query["conversation_id"])
        provider.assert_not_called()

    def test_unverified_manual_token_action_is_rejected(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "connect", "token": "synthetic-unverified-jwt",
             })), \
             patch.object(operator, "save_token") as save:
            response = asyncio.run(handler(request))
        self.assertEqual(response.status, 400)
        save.assert_not_called()

    def test_public_connection_status_does_not_expose_link_generation_or_credentials(self):
        with patch.dict("os.environ", {access.API_BASE_URL_ENV: "https://operator.example/api"}):
            result = operator._public({"status": "connected", "secrets": b"secret",
                "metadata": {"generation": "private-generation", "connection_instance_id": "private-instance"}})
        self.assertTrue(result["connected"])
        self.assertNotIn("generation", result)
        self.assertNotIn("connection_instance_id", result)
        self.assertNotIn("secrets", result)

    def test_private_workspace_gate_uses_uuid_not_name(self):
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}):
            self.assertTrue(access.is_operator_workspace(TENANT))
            self.assertFalse(access.is_operator_workspace("BeinSystems"))
            self.assertFalse(access.is_operator_workspace("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"))
        with patch.dict("os.environ", {access.WORKSPACE_ENV: "invalid"}):
            self.assertFalse(access.is_operator_workspace(TENANT))

    def test_operator_api_base_is_pinned_to_vendor_origin(self):
        approved = "https://operator-api.beinsystems.az/v1"
        self.assertEqual(access.api_base_url(), approved)
        for value in ("https://operator-api.beinsystems.az/v1",
                      "https://attacker.example/v1",
                      "http://operator-api.beinsystems.az/v1",
                      "https://operator-api.beinsystems.az/v2"):
            with patch.dict("os.environ", {access.API_BASE_URL_ENV: value}):
                self.assertEqual(access.api_base_url(), approved)

    def test_read_adapter_uses_approved_host_headers_and_bearer(self):
        fake = FakeOpener({"status": "success", "data": [{"account": "client-1"}]})
        base = "https://operator-api.beinsystems.az/v1"
        result = operator._request_operator(
            "accounts", {"search": "client", "limit": 1}, token="secret-jwt",
            base_url=base, opener=fake)
        self.assertEqual(result["data"][0]["account"], "client-1")
        self.assertEqual(fake.request.full_url, base + "/user/accounts")
        self.assertEqual(fake.request.get_method(), "POST")
        self.assertEqual(fake.request.get_header("Authorization"), "Bearer secret-jwt")
        self.assertIsNone(fake.request.get_header("Origin"))
        self.assertIsNone(fake.request.get_header("Referer"))
        self.assertEqual(fake.request.get_header("Content-type"), "application/json")
        self.assertIsNone(fake.request.get_header("User-agent"))
        self.assertEqual(json.loads(fake.request.data), {"search": "client", "limit": 1})
        self.assertEqual(fake.timeout, operator.REQUEST_TIMEOUT_SECONDS)

    def test_me_read_uses_confirmed_get_host_path_and_bearer_only(self):
        fake = FakeOpener({"status":"success","data":{
            "identifier":"synthetic.operator","role":"superadmin","partnerPin":None,
        }})
        base = "https://operator-api.beinsystems.az/v1"
        result = operator._request_operator("me", {}, token="synthetic-jwt",
            base_url=base, opener=fake)
        self.assertEqual(result["data"]["identifier"], "synthetic.operator")
        self.assertEqual(fake.request.full_url, base + "/user/me")
        self.assertEqual(fake.request.get_method(), "GET")
        self.assertIsNone(fake.request.data)
        self.assertEqual(fake.request.get_header("Authorization"), "Bearer synthetic-jwt")
        self.assertIsNone(fake.request.get_header("Origin"))
        self.assertIsNone(fake.request.get_header("Referer"))
        self.assertEqual(fake.request.get_header("Accept"), "application/json")

    def test_operator_read_auth_http_errors_keep_upstream_code_without_body(self):
        base = "https://operator-api.beinsystems.az/v1"
        for upstream_status in (401, 403):
            fake = FakeHTTPErrorOpener(upstream_status, {
                "message": "private provider detail synthetic-login synthetic-password",
                "token": "synthetic-jwt",
            })
            with self.subTest(upstream_status=upstream_status), \
                 patch.dict("os.environ", {access.API_BASE_URL_ENV: base}):
                with self.assertRaises(operator.OperatorError) as raised:
                    operator._request_operator(
                        "me", {}, token="synthetic-jwt", base_url=base, opener=fake)
            self.assertEqual(raised.exception.status, 502)
            self.assertEqual(raised.exception.upstream_http_status, upstream_status)
            self.assertIn(f"upstream HTTP {upstream_status}", str(raised.exception))
            for secret in ("private provider detail", "synthetic-login",
                           "synthetic-password", "synthetic-jwt"):
                self.assertNotIn(secret, str(raised.exception))
            self.assertEqual(fake.request.full_url, base + "/user/me")
            self.assertEqual(fake.request.get_method(), "GET")

    def test_operator_read_classifies_only_explicit_bearer_challenges(self):
        base = "https://operator-api.beinsystems.az/v1"
        cases = (
            (401, {"WWW-Authenticate": 'Bearer realm="operator", error="invalid_token"'},
             "invalid_token"),
            (403, {"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
             "insufficient_scope"),
            (403, {"WWW-Authenticate": 'Bearer error="invalid_token"'}, None),
            (403, {"WWW-Authenticate":
                   'Bearer error_description="copy, error=\\\"insufficient_scope\\\""'},
             None),
            (403, {"WWW-Authenticate": 'Basic realm="private-detail"'}, None),
        )
        for status, headers, expected_code in cases:
            fake = FakeHTTPErrorOpener(status, {
                "detail": "private provider body synthetic-jwt",
            }, headers=headers)
            with self.subTest(status=status, expected_code=expected_code), \
                 patch.dict("os.environ", {access.API_BASE_URL_ENV: base}):
                with self.assertRaises(operator.OperatorError) as raised:
                    operator._request_operator(
                        "accounts", {"search": "synthetic-account", "limit": 1},
                        token="synthetic-jwt", base_url=base, opener=fake)
            self.assertEqual(raised.exception.upstream_error_code, expected_code)
            self.assertEqual(raised.exception.upstream_http_status, status)
            self.assertNotIn("private provider body", str(raised.exception))
            self.assertNotIn("synthetic-jwt", str(raised.exception))
            self.assertNotIn("private-detail", str(raised.exception))
            self.assertNotIn("error_description", str(raised.exception))

    def test_operator_read_rejects_unapproved_api_host(self):
        fake = FakeOpener({"status": "success"})
        with self.assertRaises(operator.OperatorError):
            operator._request_operator("accounts", {"search": "client"}, token="jwt",
                base_url="https://attacker.example/v1", opener=fake)
        self.assertIsNone(fake.request)

    def test_operator_login_uses_confirmed_request_and_response_contract(self):
        fake = FakeOpener({"success": True, "data": {"token": "synthetic-jwt"}})
        with patch.object(operator, "build_opener", return_value=fake):
            actual = operator._authenticate_operator(
                "beinsystems-user@account", "synthetic-password")
        self.assertEqual(actual, "synthetic-jwt")
        self.assertEqual(fake.request.full_url, "https://auth.beinsystems.az/auth/login")
        self.assertEqual(fake.request.get_method(), "POST")
        self.assertEqual(json.loads(fake.request.data), {
            "login": "beinsystems-user@account", "password": "synthetic-password",
            "uniqDeviceId": "beintaskbot-operator",
        })
        self.assertEqual(fake.request.get_header("Content-type"), "application/json")
        self.assertEqual(fake.request.get_header("Accept"), "application/json, text/plain, */*")
        self.assertTrue(fake.request.get_header("User-agent").startswith("Beintaskbot-Operator Python-urllib/"))
        self.assertIsNone(fake.request.get_header("Origin"))
        self.assertIsNone(fake.request.get_header("Referer"))
        self.assertIsNone(fake.request.get_header("Authorization"))
        self.assertEqual(fake.timeout, operator.REQUEST_TIMEOUT_SECONDS)
    def test_operator_login_requires_user_at_account_before_network(self):
        for login in ("operator-user", "@account", "user@", "user@@account"):
            with self.subTest(login=login), patch.object(operator, "build_opener") as build:
                with self.assertRaises(operator.OperatorError) as raised:
                    operator._authenticate_operator(login, "synthetic-password")
                self.assertEqual(raised.exception.status, 400)
                build.assert_not_called()
    def test_operator_login_transport_disables_redirects(self):
        fake = FakeOpener({"success": True, "data": {"token": "synthetic-jwt"}})
        with patch.object(operator, "build_opener", return_value=fake) as build:
            operator._authenticate_operator("operator-user@account", "synthetic-password")
        self.assertIsInstance(build.call_args.args[0], operator._NoRedirect)
    def test_operator_login_passes_the_supplied_login_without_rewriting_it(self):
        fake = FakeOpener({"success": True, "data": {"token": "synthetic-jwt"}})
        with patch.object(operator, "build_opener", return_value=fake):
            operator._authenticate_operator("  operator-user@account  ", "synthetic-password")
        self.assertEqual(json.loads(fake.request.data), {
            "login": "operator-user@account", "password": "synthetic-password",
            "uniqDeviceId": operator.AUTH_UNIQ_DEVICE_ID,
        })
    def test_operator_login_ignores_provider_messages_and_legacy_token_shapes(self):
        responses = [
            ({"success": False, "message": "arbitrary server text synthetic-password synthetic-jwt"}, 401),
            ({"success": True, "token": "synthetic-jwt"}, 502),
            ({"success": True, "data": {"access_token": "synthetic-jwt"}}, 502),
            ({"success": 1, "data": {"token": "synthetic-jwt"}}, 502),
        ]
        for payload, expected_status in responses:
            with self.subTest(payload=payload):
                fake = FakeOpener(payload)
                with patch.object(operator, "build_opener", return_value=fake):
                    with self.assertRaises(operator.OperatorError) as raised:
                        operator._authenticate_operator(
                            "user@example.com", "synthetic-password")
                self.assertEqual(raised.exception.status, expected_status)
                self.assertNotIn("synthetic-password", str(raised.exception))
                self.assertNotIn("synthetic-jwt", str(raised.exception))
                self.assertNotIn("arbitrary server text", str(raised.exception))
    def test_operator_http_auth_error_does_not_return_provider_body(self):
        fake = FakeHTTPErrorOpener(403, {
            "message": "arbitrary server text user@example.com synthetic-password synthetic-jwt",
        })
        with patch.object(operator, "build_opener", return_value=fake):
            with self.assertRaises(operator.OperatorError) as raised:
                operator._authenticate_operator("user@example.com", "synthetic-password")
        message = str(raised.exception)
        self.assertIn("HTTP 403", message)
        self.assertNotIn("arbitrary server text", message)
        self.assertNotIn("synthetic-password", message)
        self.assertNotIn("synthetic-jwt", message)
        self.assertEqual(raised.exception.status, 401)
    def test_connect_with_password_stages_token_without_using_or_replacing_active_token(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.return_value = {"request_count": 1}
        connection.cursor.return_value = cursor
        pending = {"status": "connected", "connected": True,
                   "pending_auth_received": True, "pending_auth_access_confirmed": False}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_authenticate_operator", return_value="sso-jwt") as authenticate, \
             patch.object(operator, "stage_confirmed_auth_token", return_value=pending) as stage, \
             patch.object(operator, "_request_operator") as request, \
             patch.object(operator, "save_token") as save:
            result = operator.connect_with_password(profile, "operator", "raw-password")
        self.assertEqual(result, pending)
        authenticate.assert_called_once_with("operator", "raw-password")
        stage.assert_called_once_with(profile, "sso-jwt")
        request.assert_not_called()
        save.assert_not_called()
        self.assertFalse(any("raw-password" in repr(call) for call in cursor.execute.call_args_list))

    def test_login_and_read_accounts_uses_fresh_jwt_without_staging_tokens(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.return_value = {"request_count": 1}
        connection.cursor.return_value = cursor
        response = {"ResponseStatus": 0, "data": [
            {"account": "beinsystems", "phone": "private-synthetic-phone"},
        ], "metadata": {"private": "ignored"}}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_authenticate_operator", return_value="fresh-sso-jwt") as authenticate, \
             patch.object(operator, "_assert_current_operator_owner") as owner_check, \
             patch.object(operator, "_record_operator_read") as audit, \
             patch.object(operator, "_request_operator", return_value=response) as request, \
             patch.object(operator, "stage_confirmed_auth_token") as stage, \
             patch.object(operator, "_activate_candidate_if_unconfigured") as activate:
            result = operator.login_and_read_accounts(
                profile, "synthetic-user@synthetic-account", "synthetic-password",
                "beinsystems")
        self.assertEqual(result, {
            "source": "fresh_login", "request_accepted": True,
            "access_confirmed": True, "account_read": True,
            "result_count": 1, "match_found": True,
            "accounts": ["beinsystems"], "token_stored": False,
        })
        authenticate.assert_called_once_with(
            "synthetic-user@synthetic-account", "synthetic-password")
        request.assert_called_once_with(
            "accounts", {"search": "beinsystems", "limit": 10},
            token="fresh-sso-jwt", base_url=operator.api_base_url())
        audit.assert_called_once_with(
            profile, "accounts", {"search": "beinsystems"})
        self.assertEqual(owner_check.call_count, 3)
        stage.assert_not_called()
        activate.assert_not_called()
        self.assertNotIn("private-synthetic-phone", repr(result))
        self.assertFalse(any(
            "synthetic-password" in repr(call) or "fresh-sso-jwt" in repr(call)
            for call in cursor.execute.call_args_list))

    def test_login_and_read_accounts_403_keeps_existing_pending_and_active_untouched(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.return_value = {"request_count": 1}
        connection.cursor.return_value = cursor
        provider_error = operator.OperatorError(
            "Upstream Bearer challenge insufficient_scope (HTTP 403).",
            502, upstream_http_status=403, upstream_error_code="insufficient_scope")
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_authenticate_operator", return_value="fresh-sso-jwt"), \
             patch.object(operator, "_assert_current_operator_owner"), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator", side_effect=provider_error) as request, \
             patch.object(operator, "_pending_auth_candidate") as pending, \
             patch.object(operator, "_credentials") as credentials, \
             patch.object(operator, "stage_confirmed_auth_token") as stage, \
             patch.object(operator, "save_token") as save:
            with self.assertRaises(operator.OperatorError) as raised:
                operator.login_and_read_accounts(
                    profile, "synthetic-user@synthetic-account", "synthetic-password",
                    "beinsystems")
        self.assertEqual(raised.exception.upstream_http_status, 403)
        self.assertEqual(raised.exception.upstream_error_code, "insufficient_scope")
        request.assert_called_once()
        pending.assert_not_called()
        credentials.assert_not_called()
        stage.assert_not_called()
        save.assert_not_called()
        self.assertFalse(any(
            "synthetic-password" in repr(call) for call in cursor.execute.call_args_list))

    def test_login_and_read_accounts_requires_current_owner_before_authentication(self):
        profile = {"tenant_id": TENANT, "telegram_id": 9, "role": "operator", "active": True}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_authenticate_operator") as authenticate, \
             patch.object(operator, "_request_operator") as request:
            with self.assertRaises(operator.OperatorError):
                operator.login_and_read_accounts(
                    profile, "synthetic-user@account", "synthetic-password",
                    "beinsystems")
        authenticate.assert_not_called()
        request.assert_not_called()

    def test_login_search_parser_accepts_account_data_with_unknown_envelope_fields(self):
        self.assertEqual(operator._accounts_login_search_payload({
            "status": "success", "ResponseStatus": 0,
            "data": [{"account": "beinsystems", "phone": "private-synthetic-phone"}],
            "extra": {"ignored": True},
        }, limit=10), ["beinsystems"])
        self.assertEqual(operator._accounts_login_search_payload({"data": []}, limit=10), [])
        for response in (
            {"success": False, "data": [{"account": "beinsystems"}]},
            {"status": "error", "data": [{"account": "beinsystems"}]},
            {"data": [{"account": "beinsystems"}] * 11},
            {"data": [{"name": "malformed"}]},
        ):
            with self.subTest(response=response), self.assertRaises(operator.OperatorError):
                operator._accounts_login_search_payload(response, limit=10)

    def test_contract_pending_token_is_encrypted_separately_from_legacy_pending(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        current_at = "2026-11-18T22:00:00+00:00"
        legacy_at = "2026-11-17T22:00:00+00:00"
        cursor.fetchone.side_effect = [
            {"received_at": current_at}, {"received_at": current_at}, {"received_at": legacy_at},
        ]
        connection.cursor.return_value = cursor
        cipher = Fernet(Fernet.generate_key())
        active = {"status": "connected", "connected_at": None, "updated_at": None}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=active), \
             patch.object(operator, "_fernet", return_value=cipher):
            result = operator.stage_confirmed_auth_token(profile, "synthetic-jwt-token")
        insert_call = next(call for call in cursor.execute.call_args_list
                           if "INSERT INTO saas_beinsystems_operator_pending_auth_v2" in call.args[0])
        ciphertext = bytes(insert_call.args[1][2])
        self.assertNotIn(b"synthetic-jwt-token", ciphertext)
        stored = json.loads(cipher.decrypt(ciphertext).decode("utf-8"))
        self.assertEqual(stored, {
            "tenant_id": TENANT, "provider": operator.PROVIDER, "owner_telegram_id": 7,
            "purpose": "pending_auth_verification",
            "auth_contract": operator.AUTH_CONTRACT_VERSION,
            "token": "synthetic-jwt-token",
        })
        legacy_writes = [call.args[0] for call in cursor.execute.call_args_list
                         if "saas_beinsystems_operator_pending_auth " in call.args[0]
                         and any(word in call.args[0] for word in ("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(legacy_writes, [])
        self.assertFalse(any("saas_tenant_integrations" in call.args[0]
                             and ("INSERT" in call.args[0] or "UPDATE" in call.args[0])
                             for call in cursor.execute.call_args_list))
        self.assertEqual(result["status"], "connected")
        self.assertTrue(result["pending_auth_received"])
        self.assertIsNone(result["pending_auth_contract"],
                          "status does not infer a pending token’s login provenance")
        self.assertFalse(result["pending_auth_access_confirmed"])
        self.assertTrue(result["legacy_pending_auth_received"])
        self.assertEqual(result["pending_auth_received_at"], current_at)
        connection.commit.assert_called_once()

    def test_pending_auth_candidate_accepts_previous_login_contract(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        cipher = Fernet(Fernet.generate_key())
        for contract in (
            operator.AUTH_CONTRACT_VERSION,
            "auth_login_runtime_metadata_v1",
        ):
            with self.subTest(contract=contract):
                envelope = {
                    "tenant_id": TENANT, "provider": operator.PROVIDER,
                    "owner_telegram_id": 7, "purpose": "pending_auth_verification",
                    "auth_contract": contract, "token": "synthetic-jwt",
                }
                ciphertext = cipher.encrypt(json.dumps(envelope).encode("utf-8"))
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.__exit__.return_value = False
                cursor = MagicMock()
                cursor.__enter__.return_value = cursor
                cursor.__exit__.return_value = False
                cursor.fetchone.return_value = {
                    "token_ciphertext": ciphertext, "received_at": "2026-11-19T00:00:00Z",
                }
                connection.cursor.return_value = cursor
                with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
                     patch.object(operator, "_connect", return_value=connection), \
                     patch.object(operator, "_ensure_schema"), \
                     patch.object(operator, "_current_profile", return_value=profile), \
                     patch.object(operator, "_fernet", return_value=cipher), \
                     patch.object(operator, "api_base_url",
                                  return_value="https://operator-api.beinsystems.az/v1"):
                    candidate = operator._pending_auth_candidate(profile)
                self.assertEqual(candidate["token"], "synthetic-jwt")
                self.assertEqual(candidate["received_at"], "2026-11-19T00:00:00Z")

    def test_legacy_pending_token_storage_remains_separate(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.return_value = {"received_at": "2026-11-17T22:00:00+00:00"}
        connection.cursor.return_value = cursor
        cipher = Fernet(Fernet.generate_key())
        active = {"status": "connected", "connected_at": None, "updated_at": None}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=active), \
             patch.object(operator, "_fernet", return_value=cipher):
            result = operator.stage_auth_token(profile, "legacy-synthetic-jwt")
        insert_call = next(call for call in cursor.execute.call_args_list
                           if "INSERT INTO saas_beinsystems_operator_pending_auth" in call.args[0])
        self.assertNotIn("me_verified_at", insert_call.args[0])
        self.assertNotIn("activated_at", insert_call.args[0])
        self.assertNotIn(b"legacy-synthetic-jwt", bytes(insert_call.args[1][2]))
        self.assertFalse(result["pending_auth_received"])
        self.assertTrue(result["legacy_pending_auth_received"])
        self.assertIsNone(result["pending_auth_contract"])
        connection.commit.assert_called_once()

    def test_pending_auth_status_is_visible_only_to_workspace_owner(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.side_effect = [
            {"received_at": "2026-11-18T22:00:00+00:00"},
            {"received_at": "2026-11-17T22:00:00+00:00"},
        ]
        connection.cursor.return_value = cursor
        active = {"status": "not_connected", "connected_at": None, "updated_at": None}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_authorize", return_value=(TENANT, 7, {"role": "owner"})), \
             patch.object(operator, "_operator_row", return_value=active):
            owner_status = operator.status(profile)
        self.assertTrue(owner_status["pending_auth_received"])
        self.assertFalse(owner_status["pending_auth_access_confirmed"])
        self.assertIsNone(owner_status["pending_auth_contract"],
                          "pending candidates can predate the current login contract")
        self.assertTrue(owner_status["legacy_pending_auth_received"])

        cursor.execute.reset_mock()
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_authorize", return_value=(TENANT, 7, {"role": "manager"})), \
             patch.object(operator, "_operator_row", return_value=active):
            non_owner_status = operator.status(profile)
        self.assertFalse(non_owner_status["pending_auth_received"])
        self.assertIsNone(non_owner_status["pending_auth_received_at"])
        self.assertFalse(non_owner_status["legacy_pending_auth_received"])
        cursor.execute.assert_not_called()

    def test_verify_accounts_access_reads_one_pending_result_without_activation(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.rowcount = 1
        connection.cursor.return_value = cursor
        candidate = {"token": "synthetic-jwt", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        response = {"status": "success", "data": [{"account": "synthetic-account"}]}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=None), \
             patch.object(operator, "_pending_auth_candidate", return_value=candidate), \
             patch.object(operator, "_assert_current_operator_owner") as owner_check, \
             patch.object(operator, "_pending_timestamp_is_current", return_value=True), \
             patch.object(operator, "_record_operator_read") as audit, \
             patch.object(operator, "_request_operator", return_value=response) as request, \
             patch.object(operator, "_upsert_imported_accounts") as upsert, \
             patch.object(operator, "_activate_candidate_if_unconfigured") as activate, \
             patch.object(operator, "save_token") as save:
            result = operator.verify_accounts_access(profile, " synthetic-account ")
        self.assertEqual(result, {
            "source": "pending", "request_accepted": True,
            "access_confirmed": True, "account_read": True,
            "result_count": 1, "match_found": True,
        })
        self.assertEqual(owner_check.call_count, 2,
                         "the live workspace owner is rechecked before the request and before return")
        request.assert_called_once_with(
            "accounts", {"search": "synthetic-account", "limit": 1},
            token="synthetic-jwt", base_url=candidate["base_url"])
        audit.assert_called_once_with(
            profile, "accounts", {"search": "synthetic-account"})
        sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("SET accounts_access_verified_at=now()", sql)
        self.assertNotIn("me_verified_at=now()", sql)
        self.assertNotIn("verified_identifier", sql)
        self.assertNotIn("saas_tenant_integrations SET", sql)
        self.assertFalse(any("synthetic-jwt" in repr(call) for call in cursor.execute.call_args_list))
        upsert.assert_not_called()
        activate.assert_not_called()
        save.assert_not_called()
        connection.commit.assert_called_once()

    def test_verify_accounts_access_accepts_empty_result_without_claiming_account_read(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor.return_value = cursor
        candidate = {"token": "synthetic-jwt", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=None), \
             patch.object(operator, "_pending_auth_candidate", return_value=candidate), \
             patch.object(operator, "_assert_current_operator_owner"), \
             patch.object(operator, "_pending_timestamp_is_current", return_value=True), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator", return_value={
                 "status": "success", "data": [],
             }) as request:
            result = operator.verify_accounts_access(profile, "synthetic-account")
        self.assertEqual(result, {
            "source": "pending", "request_accepted": True,
            "access_confirmed": False, "account_read": False,
            "result_count": 0, "match_found": False,
        })
        request.assert_called_once_with(
            "accounts", {"search": "synthetic-account", "limit": 1},
            token="synthetic-jwt", base_url=candidate["base_url"])
        self.assertFalse(any(
            "SET accounts_access_verified_at=now()" in call.args[0]
            for call in cursor.execute.call_args_list))
        connection.commit.assert_not_called()

    def test_verify_accounts_access_rejects_unknown_or_malformed_probe_responses(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        candidate = {"token": "synthetic-jwt", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        malformed = [
            [{"account": "synthetic-account"}],
            {"success": True, "data": [{"account": "synthetic-account"}]},
            {"status": "error", "data": []},
            {"status": "success", "data": "not-a-list"},
            {"status": "success", "data": [{"account": "first"}, {"account": "second"}]},
            {"status": "success", "data": [None]},
            {"status": "success", "data": [{}]},
            {"status": "success", "data": [{"account": 17}]},
            {"status": "success", "data": [{"account": "   "}]},
            {"status": "success", "data": [{"account": "invalid\\naccount"}]},
            {"status": "success", "data": [], "extra": "unknown"},
        ]
        for response in malformed:
            with self.subTest(response=response):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.__exit__.return_value = False
                cursor = MagicMock()
                cursor.__enter__.return_value = cursor
                cursor.__exit__.return_value = False
                connection.cursor.return_value = cursor
                with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
                     patch.object(operator, "_connect", return_value=connection), \
                     patch.object(operator, "_ensure_schema"), \
                     patch.object(operator, "_current_profile", return_value=profile), \
                     patch.object(operator, "_operator_row", return_value=None), \
                     patch.object(operator, "_pending_auth_candidate", return_value=candidate), \
                     patch.object(operator, "_assert_current_operator_owner"), \
                     patch.object(operator, "_record_operator_read"), \
                     patch.object(operator, "_request_operator", return_value=response):
                    with self.assertRaises(operator.OperatorError):
                        operator.verify_accounts_access(profile, "synthetic-account")
                self.assertFalse(any(
                    "SET accounts_access_verified_at=now()" in call.args[0]
                    for call in cursor.execute.call_args_list))
                connection.commit.assert_not_called()

    def test_verify_accounts_access_rechecks_owner_before_network_and_before_active_return(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor.return_value = cursor
        candidate = {"token": "synthetic-jwt", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        owner = {"role": "owner", "active": True, "tenant_status": "active"}
        manager = {"role": "manager", "active": True, "tenant_status": "active"}

        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", side_effect=[owner, manager]), \
             patch.object(operator, "_operator_row", return_value=None), \
             patch.object(operator, "_pending_auth_candidate", return_value=candidate), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator") as request:
            with self.assertRaises(operator.OperatorError):
                operator.verify_accounts_access(profile, "synthetic-account")
        request.assert_not_called()

        active_row = {"status": "connected", "secrets": b"encrypted-active"}
        credentials = {
            "token": "synthetic-active-jwt",
            "base_url": "https://operator-api.beinsystems.az/v1",
            "generation": "generation-1", "connection_instance_id": "connection-1",
        }
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", side_effect=[owner, owner, manager]), \
             patch.object(operator, "_operator_row", return_value=active_row), \
             patch.object(operator, "_pending_auth_candidate", return_value=None), \
             patch.object(operator, "_credentials", side_effect=[credentials, credentials, credentials]), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator", return_value={
                 "status": "success", "data": [{"account": "synthetic-account"}],
             }) as request:
            with self.assertRaises(operator.OperatorError) as raised:
                operator.verify_accounts_access(profile, "synthetic-account")
        request.assert_called_once()
        self.assertNotIn("synthetic-active-jwt", str(raised.exception))

    def test_verify_accounts_access_rejects_provider_failure_even_with_list_data(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor.return_value = cursor
        candidate = {"token": "synthetic-jwt", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=None), \
             patch.object(operator, "_pending_auth_candidate", return_value=candidate), \
             patch.object(operator, "_assert_current_operator_owner"), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator", return_value={
                 "success": False, "message": "private provider detail", "data": [],
             }):
            with self.assertRaises(operator.OperatorError) as raised:
                operator.verify_accounts_access(profile, "synthetic-account")
        self.assertNotIn("private provider detail", str(raised.exception))
        self.assertEqual(cursor.execute.call_count, 0)
        connection.commit.assert_not_called()

    def test_verify_accounts_access_uses_active_connection_without_changing_it(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor.return_value = cursor
        active_row = {"status": "connected", "secrets": b"encrypted-active-token"}
        credentials = {
            "token": "synthetic-active-jwt", "base_url": "https://operator-api.beinsystems.az/v1",
            "generation": "generation-1", "connection_instance_id": "connection-1",
        }
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=active_row), \
             patch.object(operator, "_pending_auth_candidate", return_value=None), \
             patch.object(operator, "_credentials", side_effect=[credentials, credentials, credentials]), \
             patch.object(operator, "_record_operator_read") as audit, \
             patch.object(operator, "_request_operator", return_value={
                 "status": "success", "data": [{"account": "synthetic-account"}],
             }) as request:
            result = operator.verify_accounts_access(profile, "synthetic-account")
        self.assertEqual(result, {
            "source": "active", "request_accepted": True,
            "access_confirmed": True, "account_read": True,
            "result_count": 1, "match_found": True,
        })
        request.assert_called_once_with(
            "accounts", {"search": "synthetic-account", "limit": 1},
            token="synthetic-active-jwt", base_url=credentials["base_url"])
        audit.assert_called_once_with(profile, "accounts", {"search": "synthetic-account"})
        self.assertEqual(cursor.execute.call_count, 0)
        connection.commit.assert_not_called()
        self.assertNotIn("synthetic-active-jwt", json.dumps(result))

    def test_verify_accounts_access_requires_owner_and_search_before_provider_call(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "manager", "active": True}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_request_operator") as request:
            with self.assertRaises(operator.OperatorError):
                operator.verify_accounts_access(profile, "synthetic-account")
            request.assert_not_called()
            profile["role"] = "owner"
            with self.assertRaises(operator.OperatorError):
                operator.verify_accounts_access(profile, "abc")
            request.assert_not_called()

    def test_verify_accounts_access_rejects_stale_pending_candidate(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.rowcount = 0
        connection.cursor.return_value = cursor
        candidate = {"token": "synthetic-jwt", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=None), \
             patch.object(operator, "_pending_auth_candidate", return_value=candidate), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator", return_value={
                 "status": "success", "data": [],
             }) as request:
            with self.assertRaises(operator.OperatorError) as raised:
                operator.verify_accounts_access(profile, "synthetic-account")
        request.assert_not_called()
        self.assertEqual(raised.exception.status, 409)
        sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertNotIn("saas_tenant_integrations SET", sql)
        connection.commit.assert_not_called()

    def test_account_import_reads_pending_candidate_and_preserves_existing_connection(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        candidate = {"token":"synthetic-candidate-token","received_at":"2026-11-19T00:00:00Z",
                     "base_url":"https://operator-api.beinsystems.az/v1"}
        active = {"status":"connected","secrets":b"existing-encrypted-token"}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.rowcount = 1
        connection.cursor.return_value = cursor
        responses = [
            {"status":"success","data":{"identifier":"synthetic.operator","role":"superadmin"}},
            {"status":"success","data":[{"account":"synthetic-account"}]},
        ]
        with patch.dict("os.environ", {access.WORKSPACE_ENV:TENANT}), \
             patch.object(operator,"_connect",return_value=connection), \
             patch.object(operator,"_ensure_schema"), \
             patch.object(operator,"_current_profile",return_value=profile), \
             patch.object(operator,"_operator_row",return_value=active), \
             patch.object(operator,"_pending_auth_candidate",return_value=candidate), \
             patch.object(operator,"_record_operator_read") as audit, \
             patch.object(operator,"_request_operator",side_effect=responses) as request, \
             patch.object(operator,"_pending_timestamp_is_current",return_value=True), \
             patch.object(operator,"_upsert_imported_accounts") as upsert, \
             patch.object(operator,"_activate_candidate_if_unconfigured",return_value=False) as activate, \
             patch.object(operator,"status",return_value={"status":"connected"}), \
             patch.object(operator,"save_token") as save:
            result=operator.import_accounts(profile)
        self.assertEqual(result["imported"],1)
        self.assertFalse(result["activated"])
        self.assertEqual([call.args[0] for call in request.call_args_list],["me","accounts"])
        self.assertTrue(all(call.kwargs["token"]=="synthetic-candidate-token"
                            for call in request.call_args_list))
        self.assertEqual(audit.call_count,2)
        upsert.assert_called_once()
        activate.assert_called_once_with(cursor,TENANT,7,"synthetic-candidate-token")
        save.assert_not_called()

    def test_activation_guard_leaves_an_existing_connected_token_untouched(self):
        cursor=MagicMock()
        active={"status":"connected","secrets":b"existing-encrypted-token"}
        with patch.dict("os.environ",{access.WORKSPACE_ENV:TENANT}), \
             patch.object(operator,"_operator_row",return_value=active) as row:
            activated=operator._activate_candidate_if_unconfigured(
                cursor,TENANT,7,"synthetic-candidate-token")
        self.assertFalse(activated)
        row.assert_called_once_with(cursor,TENANT,lock=True)
        cursor.execute.assert_not_called()

    def test_unverified_token_cannot_replace_active_connection(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect") as connect:
            with self.assertRaises(operator.OperatorError) as raised:
                operator.save_token(profile, "synthetic-jwt")
        self.assertEqual(raised.exception.status, 409)
        connect.assert_not_called()


    def test_adapter_rejects_undocumented_mutations_and_short_search(self):
        with self.assertRaises(operator.OperatorError):
            operator._request_operator("task_create", {"title": "x"}, token="jwt",
                                       base_url="https://operator.example",
                                       opener=FakeOpener({}))
        with self.assertRaises(operator.OperatorError):
            operator._request_operator("accounts", {"search": "abc"}, token="jwt",
                                       base_url="https://operator.example",
                                       opener=FakeOpener({}))
        for invalid_limit in (0, 101, True, 1.0, "1"):
            with self.subTest(limit=invalid_limit), self.assertRaises(operator.OperatorError):
                operator._request_operator("accounts", {
                    "search": "known-account", "limit": invalid_limit,
                }, token="jwt", base_url="https://operator.example",
                   opener=FakeOpener({}))
    def test_account_search_and_user_login_projection_are_bounded(self):
        self.assertEqual(operator.accounts_list_payload({"status": "success", "data": [
            {"account": "acct-7", "phone": "+1 555 0100", "balance": 999},
            {"phone": "+1 555 0101"},
        ]}), [{"account": "acct-7"}])
        self.assertEqual(operator.profile_users_payload({"status": "success", "data": [
            {"login": "admin", "last_login": "private", "publicmode": "online"},
        ]}), [{"login": "admin", "bindable": True}])
        with self.assertRaises(operator.OperatorError):
            operator.accounts_list_payload({"status": "success", "data": {"account": "acct-7"}})
        with self.assertRaises(operator.OperatorError):
            operator.profile_users_payload({"status": "error", "data": [{"login": "admin"}]})

    def test_operator_me_payload_uses_actual_provider_identity_and_not_app_permissions(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner",
                   "permissions": ["operator_data:view"]}
        response = {"status": "success", "data": {
            "identifier": "synthetic.operator", "role": "superadmin", "partnerPin": None,
        }}
        self.assertEqual(operator.operator_me_payload(response), {
            "identifier": "synthetic.operator", "role": "superadmin",
        })
        self.assertEqual(profile["permissions"], ["operator_data:view"])
        for invalid in (
            {"status": "error", "data": {"identifier": "x", "role": "admin"}},
            {"status": "success", "data": {"identifier": " ", "role": "admin"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(operator.OperatorError):
                operator.operator_me_payload(invalid)

    def test_full_account_import_keeps_more_than_100_and_rejects_partial_data(self):
        values = [{"account": f"synthetic-{index}"} for index in range(137)]
        values.extend([{"account": "synthetic-4"}])
        result = operator.accounts_import_payload({"status": "success", "data": values})
        self.assertEqual(len(result), 137)
        self.assertEqual(result[-1], {"account": "synthetic-136"})
        self.assertEqual(operator.accounts_list_payload(
            {"status": "success", "data": values}), result[:100])
        for invalid in (
            {"status": "error", "data": values},
            {"status": "success", "data": values + [None]},
            {"status": "success", "data": [{"account": ""}]},
            {"status": "success", "data": [{"account": "bad\naccount"}]},
        ):
            with self.subTest(invalid=type(invalid.get("data")).__name__), \
                 self.assertRaises(operator.OperatorError):
                operator.accounts_import_payload(invalid)

    def test_account_summary_drops_financial_and_administrative_fields_and_checks_actual_identity(self):
        response = {"status": "success", "data": {
            "account": "acct-7", "balance": 500, "role": "superadmin",
            "last_login": "private", "last_contact": "2026-11-01",
            "partner_name": "Partner", "token": "secret",
        }}
        self.assertEqual(operator.account_info_payload(response, "acct-7"), {
            "account": "acct-7", "last_contact": "2026-11-01", "partner_name": "Partner",
        })
        for response_data in (
            {"status": "success", "data": {"account": "different-account"}},
            {"status": "success", "data": {"partner_name": "Partner"}},
            {"status": "success", "data": {"account": "acct-7\\n"}},
        ):
            with self.subTest(response_data=response_data), self.assertRaises(operator.OperatorError):
                operator.account_info_payload(response_data, "acct-7")

    def test_task_draft_is_disabled_and_linear_match_is_exact(self):
        draft = operator.build_task_draft("Issue title", "Details", "acct-7", "high")
        self.assertFalse(draft["enabled"])
        self.assertEqual(draft["payload"], {
            "title": "Issue title", "description": "Details", "account": "acct-7", "priority": "high",
        })
        self.assertEqual(operator.task_creation_contract()["enabled"], False)
        self.assertEqual(operator.exact_linear_identifier({"linear_id": "APP-123"}), "APP-123")
        self.assertEqual(operator.exact_linear_identifier({"title": "APP-123"}), "")

    def test_operator_ai_tools_are_read_only_and_case_scoped(self):
        tools = ai_tools.registry()
        self.assertTrue(all(item["read_only"] and not item["customer_visible"] for item in tools))
        self.assertFalse(any("create" in item["name"] for item in tools))
        self.assertFalse(next(item for item in tools if item["name"] == "operator_related_tasks")["enabled"])
        with patch.object(ai_tools.operator, "links_for_local", return_value=[]):
            result = ai_tools.invoke({"tenant_id": TENANT, "telegram_id": 7},
                                     "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                                     "operator_case_context")
        self.assertEqual(result["status"], "unlinked")
        with patch.object(ai_tools.operator, "links_for_local", return_value=[]):
            with self.assertRaises(operator.OperatorError):
                ai_tools.invoke({"tenant_id": TENANT, "telegram_id": 7},
                                "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                                "operator_account_summary", {"account": "guessed"})

    def test_operator_permissions_are_granular_and_owner_managed(self):
        values = _permissions(["operator_data:view", "operator_data:link"], "manager")
        policy = TenantPolicy({"role": "manager", "active": True, "permissions": values})
        self.assertTrue(policy.allows_action("operator_data", "view"))
        self.assertTrue(policy.allows_action("operator_data", "link"))
        self.assertIn("operator_data:view", _EDITOR_PERMISSION_KEYS)
        self.assertIn("operator_data:link", _EDITOR_PERMISSION_KEYS)
        self.assertFalse(TenantPolicy({"role": "operator", "active": True,
                                      "permissions": ["operator_data:view"]}).allows_action("operator_data", "link"))

    def test_schema_migration_is_additive_and_tenant_scoped(self):
        cur = FakeCursor()
        migrate_operator_identity_links(cur)
        sql = "\n".join(statement for statement, _ in cur.sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_links", sql)
        self.assertIn("connection_instance_id UUID NOT NULL", sql)
        self.assertIn("saas_operator_active_profile_user_idx", sql)
        self.assertIn("saas_operator_active_company_account_idx", sql)
        self.assertIn("saas_operator_active_case_account_idx", sql)
        self.assertIn("external_entity_type='profile_user' ", sql)
        self.assertIn("local_entity_type='company' AND external_entity_type='account' ", sql)
        self.assertIn("local_entity_type='support_ticket' AND external_entity_type='account' ", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_rate_limits", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_pending_auth_v2", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS accounts_access_verified_at", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_account_catalog", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_company_imports", sql)
        self.assertIn("PRIMARY KEY(tenant_id,external_account)", sql)
        self.assertIn("FOREIGN KEY(tenant_id,company_id)", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_dialog_company_bindings", sql)
        self.assertIn("saas_beinsystems_operator_rate_limits_endpoint_check", sql)
        self.assertIn("CHECK(endpoint IN ('me','accounts','account_info','profile_users','task_list','auth_login'))", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_employee_links", sql)
        self.assertIn("FOREIGN KEY(tenant_id,member_telegram_id)", sql)
        self.assertIn("saas_operator_active_member_link_idx", sql)
        self.assertIn("saas_operator_active_staff_identity_idx", sql)



class OperatorCompanyImportTests(unittest.TestCase):
    def test_active_snapshot_uses_only_active_token_and_documented_read_endpoints(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner"}
        instance = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        row = {"status": "connected", "secrets": b"synthetic-ciphertext",
               "metadata": {"generation": "active-generation",
                            "connection_instance_id": instance}}
        credentials = {"token": "synthetic-active-jwt",
                       "base_url": "https://operator-api.beinsystems.az/v1",
                       "generation": "active-generation",
                       "connection_instance_id": instance, "status": "connected"}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor.return_value = cursor
        responses = [
            {"status": "success", "data": {"identifier": "synthetic.operator", "role": "operator"}},
            {"status": "success", "data": [{"account": "fresh-account"}]},
        ]
        with (
            patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}),
            patch.object(operator, "_connect", return_value=connection),
            patch.object(operator, "_ensure_schema"),
            patch.object(operator, "_current_profile", return_value=profile),
            patch.object(operator, "_operator_row", return_value=row),
            patch.object(operator, "_credentials", side_effect=[credentials, credentials]) as active,
            patch.object(operator, "_pending_auth_candidate") as pending,
            patch.object(operator, "_record_operator_read") as audit,
            patch.object(operator, "_request_operator", side_effect=responses) as request,
        ):
            accounts, used = company_import._fresh_active_accounts(profile, TENANT, 7)
        self.assertEqual(accounts, [{"account": "fresh-account"}])
        self.assertEqual(used, credentials)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["me", "accounts"])
        self.assertTrue(all(call.kwargs["token"] == "synthetic-active-jwt"
                            for call in request.call_args_list))
        self.assertEqual(audit.call_count, 2)
        self.assertEqual(active.call_args_list[0].kwargs, {"allow_configured": True})
        pending.assert_not_called()

    def test_active_connection_rotation_during_read_rejects_before_import(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner"}
        instance = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        row = {"status": "connected", "secrets": b"synthetic-ciphertext",
               "metadata": {"generation": "old", "connection_instance_id": instance}}
        first = {"token": "synthetic-old-token", "base_url": "https://operator-api.beinsystems.az/v1",
                 "generation": "old", "connection_instance_id": instance, "status": "connected"}
        second = {**first, "token": "synthetic-new-token", "generation": "new"}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        connection.cursor.return_value = cursor
        responses = [
            {"status": "success", "data": {"identifier": "synthetic.operator", "role": "operator"}},
            {"status": "success", "data": [{"account": "fresh-account"}]},
        ]
        with (
            patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}),
            patch.object(operator, "_connect", return_value=connection),
            patch.object(operator, "_ensure_schema"),
            patch.object(operator, "_current_profile", return_value=profile),
            patch.object(operator, "_operator_row", return_value=row),
            patch.object(operator, "_credentials", side_effect=[first, second]),
            patch.object(operator, "_pending_auth_candidate") as pending,
            patch.object(operator, "_record_operator_read"),
            patch.object(operator, "_request_operator", side_effect=responses),
        ):
            with self.assertRaises(operator.OperatorError) as raised:
                company_import._fresh_active_accounts(profile, TENANT, 7)
        self.assertEqual(raised.exception.status, 409)
        pending.assert_not_called()

    def test_fresh_lists_drive_creation_across_rotation_without_removing_old_mapping(self):
        existing = {"legacy-account": "company-legacy"}
        visible = {"company-legacy"}
        first = company_import._plan_company_import(
            [{"account": "new-account"}], existing, {}, visible, set()
        )
        self.assertEqual(first, [{"account": "new-account", "action": "create"}])
        second = company_import._plan_company_import(
            [{"account": "legacy-account"}], existing, {}, visible, set()
        )
        self.assertEqual(second, [{
            "account": "legacy-account", "action": "reuse", "company_id": "company-legacy"
        }])
        self.assertEqual(existing, {"legacy-account": "company-legacy"})

    def test_manual_links_are_reused_but_name_collisions_require_review(self):
        plan = company_import._plan_company_import(
            [{"account": "bound-account"}, {"account": "same-name"},
             {"account": "new-account"}, {"account": "ambiguous-account"}],
            {},
            {"bound-account": ["company-bound"],
             "ambiguous-account": ["company-one", "company-two"]},
            {"company-bound"},
            {"same-name"},
        )
        self.assertEqual([item["action"] for item in plan],
                         ["bind", "manual_review", "create", "manual_review"])
        self.assertEqual(plan[0]["company_id"], "company-bound")
        self.assertNotIn("company_id", plan[1],
                         "name similarity never creates an automatic CRM link")


if __name__ == "__main__":
    unittest.main()
