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


class FakeRequestsResponse:
    def __init__(self, payload=None, *, status_code=200, headers=None, chunks=None):
        if isinstance(payload, bytes):
            body = payload
        elif payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = list(chunks) if chunks is not None else ([body] if body else [])
        self.iterated = False
        self.chunk_size = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def iter_content(self, chunk_size):
        self.iterated = True
        self.chunk_size = chunk_size
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeRequestsSession:
    def __init__(self, response):
        self.response = response
        self.trust_env = True
        self.url = None
        self.kwargs = None
        self.post_calls = 0
        self.get_calls = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def post(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        self.post_calls += 1
        return self.response

    def get(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        self.get_calls += 1
        return self.response


class CountingResponseBody:
    def __init__(self, body):
        self.body = body
        self.stream_calls = 0
        self.bytes_yielded = 0
        self.read_calls = []
        self.closed = False

    def stream(self, chunk_size, decode_content=False):
        self.stream_calls += 1
        if self.body:
            self.bytes_yielded += len(self.body)
            yield self.body

    def read(self, amount=None, decode_content=False):
        self.read_calls.append(amount)
        body, self.body = self.body, b""
        return body

    def close(self):
        self.closed = True


class SyntheticResponseAdapter(operator.requests.adapters.BaseAdapter):
    def __init__(self, status_code, headers, raw):
        self.status_code = status_code
        self.headers = headers
        self.raw = raw
        self.calls = 0
        self.request = None
        self.kwargs = None

    def send(self, request, **kwargs):
        self.calls += 1
        self.request = request
        self.kwargs = kwargs
        response = operator.requests.Response()
        response.status_code = self.status_code
        response.headers = operator.requests.structures.CaseInsensitiveDict(self.headers)
        response.url = request.url
        response.reason = "Synthetic response"
        response.request = request
        response.raw = self.raw
        return response

    def close(self):
        pass


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

    def test_integration_handler_returns_only_authenticated_directory_id(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.integration_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        directory = {"source":"active", "account_id":"synthetic-account", "result_count":1}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action":"directory_search", "search":"synthetic-account"})), \
             patch.object(operator, "search_authenticated_operator_account",
                          return_value=directory) as search, \
             patch.object(operator, "status", return_value={"accounts_only_ready":False}):
            response = asyncio.run(handler(request))
        body = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(body, {"success":True, "directory":{
            **directory, "accounts_only_ready":False}})
        self.assertNotIn("identity", body)
        self.assertNotIn("token", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        search.assert_called_once_with(profile, "synthetic-account")

    def test_account_list_plan_and_confirm_are_distinct_exact_actions(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.accounts_handler(lambda _: profile, "https://crm.example", MagicMock())
        actions = (
            ({"action": "preview_first_company_batch"}, {}, "company_batch_preview"),
            ({"action": "preview_first_company_batch_plan"}, {"plan": True}, "company_batch_preview"),
            ({"action": "confirm_first_company_batch", "receipt": "opaque"}, {"receipt": "opaque"}, "company_import"),
            ({"action": "preview_company_import_plan"}, {"plan": True,"catalogue": True}, "company_batch_preview"),
            ({"action": "confirm_company_import_batch", "receipt": "opaque"}, {"receipt": "opaque","catalogue": True}, "company_import"),
        )
        for body, arguments, key in actions:
            with self.subTest(action=body["action"]), patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
                 patch.object(operator_api, "_body", new=AsyncMock(return_value=body)), \
                 patch.object(company_import, "first_company_batch", return_value={"checked": 1}) as call:
                result = asyncio.run(handler(request))
                self.assertEqual(result.status, 200)
                self.assertEqual(json.loads(result.text)[key], {"checked": 1})
                self.assertEqual(result.headers["Cache-Control"], "no-store")
                call.assert_called_once_with(profile, **arguments)
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                "action": "preview_first_company_batch_plan", "accounts": ["untrusted"]})), \
             patch.object(company_import, "first_company_batch") as call:
            result = asyncio.run(handler(request))
            self.assertEqual(result.status, 400)
            call.assert_not_called()

    def test_background_import_requires_explicit_plan_receipt_not_caller_account_ids(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.accounts_handler(lambda _: profile, "https://crm.example", MagicMock())
        actions = (
            ({"action": "preview_company_import_all"}, 200,
             {"plan": True, "catalogue": True, "background": True}, "company_batch_preview"),
            ({"action": "confirm_company_import_all", "receipt": "opaque"}, 202,
             {"receipt": "opaque", "catalogue": True, "background": True}, "company_import_job"),
            ({"action": "confirm_company_import_all", "receipt": "opaque", "accounts": ["a"]},
             400, None, None),
        )
        for body, expected_status, arguments, key in actions:
            with self.subTest(action=body["action"]), patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
                 patch.object(operator_api, "_body", new=AsyncMock(return_value=body)), \
                 patch.object(company_import, "first_company_batch", return_value={"id": "job"}) as call:
                result = asyncio.run(handler(request))
                self.assertEqual(result.status, expected_status)
                if key:
                    call.assert_called_once_with(profile, **arguments)
                    self.assertEqual(json.loads(result.text)[key], {"id": "job"})
                else:
                    call.assert_not_called()

    def test_add_account_company_requires_explicit_single_account_action(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        request = MagicMock(method="POST")
        handler = operator_api.accounts_handler(
            lambda _request: profile, "https://crm.example", MagicMock())
        result = {"status": "created", "source": "accounts_only",
                  "account_id": "exact-account", "company": {"id": "local-id", "name": "exact-account"}}
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "add_account_company", "account_id": "exact-account"})), \
             patch.object(company_import, "add_selected_account_company",
                          return_value=result) as add:
            response = asyncio.run(handler(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), {"success": True, "account_company": result})
        add.assert_called_once_with(profile, "exact-account")

        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={
                 "action": "add_account_company", "account_id": "exact-account",
                 "limit": 100})), \
             patch.object(company_import, "add_selected_account_company") as add:
            rejected = asyncio.run(handler(request))
        self.assertEqual(rejected.status, 400)
        add.assert_not_called()

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

    def test_dialog_picker_rejects_local_company_search_and_id(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "operator", "active": True}
        origin = "https://crm.example"
        handler = operator_api.dialog_company_handler(lambda _request: profile, origin, MagicMock())
        fields = {"provider": "connect", "lead_id": "41", "conversation_id": "test-dialog",
                  "channel_id": "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb", "company_search": "Demo"}
        body = dict(fields, expected_tenant_id=TENANT, expected_user_id="7")

        def post(payload, source=origin):
            data = json.dumps(payload).encode()
            request = MagicMock(method="POST", headers={"Origin": source}, content_type="application/json",
                                content_length=len(data), read=AsyncMock(return_value=data))
            return asyncio.run(handler(request))

        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch("tenant_dialog_company_binding.binding",
                   return_value=[{"id": "company", "name": "Demo"}]) as lookup, \
             patch.object(operator, "_request_operator") as upstream:
            self.assertEqual(post(body, "https://outside.example").status, 403)
            self.assertEqual(post(dict(body, expected_user_id="8")).status, 409)
            self.assertEqual(post(dict(body, company_id="ignored")).status, 400)
            self.assertEqual(post(dict(body, company_search=None)).status, 400)
            lookup.assert_not_called()
            self.assertEqual(post(body).status, 400)
            selection = dict(body)
            del selection["company_search"]
            self.assertEqual(post(dict(selection, company_id="local", expected_company_id=None)).status, 400)
            lookup.assert_not_called()
            upstream.assert_not_called()

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

    def test_verified_activation_seals_login_without_exposing_metadata(self):
        from cryptography.fernet import Fernet
        fernet = Fernet(Fernet.generate_key())
        cursor = MagicMock()
        cursor.fetchone.return_value = {"status": "connected"}
        with patch.object(operator, "_operator_row", return_value=None), \
             patch.object(operator, "_fernet", return_value=fernet):
            self.assertTrue(operator._activate_candidate_if_unconfigured(
                cursor, TENANT, 7, "synthetic-jwt",
                verified_identifier="Verified.Operator@example.test"))
            _, arguments = cursor.execute.call_args_list[0].args
            metadata = json.loads(arguments[3])
            self.assertEqual(set(metadata), {"generation", "connection_instance_id"})
            secret = json.loads(fernet.decrypt(arguments[4]))
            self.assertEqual(secret["verified_connected_identity"], {
                "identifier": "Verified.Operator@example.test", "generation": metadata["generation"]})
            self.assertNotIn("Verified.Operator", repr(arguments[3:]))
            row = {"status": "connected", "metadata": metadata, "secrets": arguments[4]}
            self.assertEqual(operator._connected_login(row, TENANT), "Verified.Operator@example.test")
            for rejected in [
                {**row, "status": "configured"},
                {**row, "status": "not_connected"},
                {**row, "secrets": b"not-a-credential"},
                {**row, "secrets": fernet.encrypt(b'{}')},
                {**row, "secrets": fernet.encrypt(json.dumps({
                    key: value for key, value in secret.items() if key != "verified_connected_identity"
                }).encode())},
                {**row, "metadata": {**metadata, "generation": "replaced"}},
                {**row, "metadata": {**metadata, "connection_instance_id": "replaced"}},
            ]:
                self.assertIsNone(operator._connected_login(rejected, TENANT))
            self.assertIsNone(operator._connected_login(row, "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"))
            self.assertNotIn("connected_login", operator._public(row))

    def test_login_projection_ignores_unsuitable_provider_identifier_without_blocking_activation(self):
        from cryptography.fernet import Fernet
        fernet = Fernet(Fernet.generate_key())
        for identifier in ("synthetic-jwt", "<script>login</script>", "not a login",
                           "unsafe\u202evalue", "x" * 241, None):
            with self.subTest(identifier=identifier):
                cursor = MagicMock()
                cursor.fetchone.return_value = {"status": "connected"}
                with patch.object(operator, "_operator_row", return_value=None), \
                     patch.object(operator, "_fernet", return_value=fernet):
                    self.assertTrue(operator._activate_candidate_if_unconfigured(
                        cursor, TENANT, 7, "synthetic-jwt", verified_identifier=identifier))
                    secret = json.loads(fernet.decrypt(cursor.execute.call_args_list[0].args[1][4]))
                    self.assertNotIn("verified_connected_identity", secret)

    def test_login_projection_requires_owner_and_explicit_live_status_read(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=conn), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_authorize", return_value=(TENANT, 7, profile)) as authorize, \
             patch.object(operator, "_operator_row", return_value={"status": "connected"}), \
             patch.object(operator, "_pending_auth_state", return_value=(None, None)), \
             patch.object(operator, "_connected_login", return_value="verified.test") as read_login:
            self.assertNotIn("connected_login", operator.status(profile))
            read_login.assert_not_called()
            self.assertEqual(operator.status(profile, include_connected_login=True)["connected_login"],
                             "verified.test")
            read_login.assert_called_once()
            authorize.return_value = (TENANT, 7, {**profile, "role": "manager"})
            with self.assertRaises(operator.OperatorError) as revoked:
                operator.status(profile, include_connected_login=True)
            self.assertEqual(revoked.exception.status, 403)
            read_login.assert_called_once()
            authorize.return_value = (TENANT, 7, profile)
            with self.assertRaises(operator.OperatorError):
                operator.status({**profile, "role": "manager"}, include_connected_login=True)
            read_login.assert_called_once()

    def test_login_projection_http_requires_current_context_and_opt_in_get(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        query = {"expected_tenant_id": TENANT, "expected_user_id": "7", "include_connected_login": "1"}
        logger = MagicMock()
        handler = operator_api.integration_handler(lambda _request: profile, "https://crm.example", logger)
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "status", side_effect=lambda _p, *, include_connected_login=False: {
                 "status": "connected", **({"connected_login": "verified.test"} if include_connected_login else {})
             }) as status:
            ordinary = asyncio.run(handler(MagicMock(method="GET", query={})))
            self.assertNotIn("connected_login", json.loads(ordinary.text)["integration"])
            proof = asyncio.run(handler(MagicMock(method="GET", query=query)))
            self.assertEqual(proof.status, 200)
            self.assertEqual(proof.headers["Cache-Control"], "no-store")
            self.assertEqual(proof.headers["Vary"], "Cookie")
            self.assertEqual(json.loads(proof.text)["integration"]["connected_login"], "verified.test")
            status.assert_called_with(profile, include_connected_login=True)
            for stale_query in ({**query, "expected_user_id": "8"},
                                {**query, "expected_tenant_id": "wrong"},
                                {"include_connected_login": "1"}):
                denied = asyncio.run(handler(MagicMock(method="GET", query=stale_query)))
                self.assertEqual(denied.status, 409)
                self.assertNotIn("connected_login", denied.text)
            self.assertEqual(status.call_count, 2)
        logger.exception.assert_not_called()

    def test_staff_selectors_require_pinned_account_and_exact_active_connection(self):
        account = "synthetic-staff"
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        row = {"status": "connected", "secrets": b"synthetic-credential",
               "metadata": {"generation": "active-gen",
                            "connection_instance_id": "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"}}
        scope = {"tenant": TENANT, "actor": 7, "account": account, "generation": "active-gen",
                 "instance": row["metadata"]["connection_instance_id"]}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        fernet = Fernet(Fernet.generate_key())
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT, operator._STAFF_ACCOUNT_ENV: account}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value={**row, "status": "configured"}) as active, \
             patch.object(operator, "_credentials", return_value={**row["metadata"], "token": "synthetic-token",
                          "base_url": "https://operator-api.beinsystems.az/v1"}) as credentials, \
             patch.object(operator, "_fernet", return_value=fernet), \
             patch.object(operator, "_record_operator_read") as audit, \
             patch.object(operator, "_request_operator", return_value={"status": "success", "data": [
                 {"login": "operator_b", "account": account, "private": "not projected"},
                 {"login": "operator_a", "account": account}
             ]}) as request:
            with self.assertRaises(operator.OperatorError) as inactive:
                operator.staff_employee_users(profile)
            self.assertEqual(inactive.exception.status, 409)
            credentials.assert_not_called()
            request.assert_not_called()
            active.return_value = row
            result = operator.staff_employee_users(profile)
            request.assert_called_once_with("profile_users", {"account": account}, token="synthetic-token",
                                            base_url="https://operator-api.beinsystems.az/v1",
                                            requests_staff_directory=True)
            audit.assert_called_once_with(profile, "profile_users", {"account": account})
            self.assertEqual(set(result), {"users", "selection_revision"})
            self.assertEqual([x["login"] for x in result["users"]], ["operator_a", "operator_b"])
            claims = operator._staff_selection_decode(result["selection_revision"])
            self.assertEqual(claims["scope"], scope)
            stale_token = fernet.encrypt_at_time(json.dumps(claims).encode(), current_time=1).decode()
            with self.assertRaises(operator.OperatorError) as expired:
                operator._staff_selection_decode(stale_token)
            self.assertEqual(expired.exception.status, 409)
            self.assertNotIn("private", repr(result))
            for unauthorized in ({**profile, "role": "operator"}, {**profile, "tenant_id": "wrong"}):
                with self.assertRaises(operator.OperatorError):
                    operator.staff_employee_users(unauthorized)
            with patch.dict("os.environ", {operator._STAFF_ACCOUNT_ENV: ""}):
                with self.assertRaises(operator.OperatorError):
                    operator.staff_employee_users(profile)
            request.assert_called_once()

    def test_staff_selector_accepts_compatible_envelopes_but_rejects_explicit_failure(self):
        account = "synthetic-staff"
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner"}
        scope = {"tenant": TENANT, "actor": 7, "account": account, "generation": "gen1", "instance": "inst1"}
        values = [{"login": "known", "account": account}]
        with patch.object(operator, "_staff_connection_scope", return_value=scope), \
             patch.object(operator, "_credentials", return_value={"generation": "gen1",
                          "connection_instance_id": "inst1", "token": "synthetic-token", "base_url": "base"}), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator") as request:
            request.return_value = values
            baseline = operator._staff_directory(profile, account)
            self.assertEqual(baseline[1], [{"login": "known", "bindable": True}])
            for accepted in (
                    values, {"data": values}, {"success": True, "data": values},
                    {"status": " SUCCESS ", "data": values},
                    {"status": "success", "success": True, "data": values, "account": account}):
                with self.subTest(accepted=accepted):
                    request.return_value = accepted
                    self.assertEqual(operator._staff_directory(profile, account), baseline)
            for rejected in (
                    {"status": "failed", "success": True, "data": values},
                    {"status": "success", "success": False, "data": values},
                    {"status": None, "data": values}, {"status": "", "data": values},
                    {"success": "true", "data": values}, {"success": 1, "data": values},
                    {"success": False, "data": values}, {"error": "unavailable", "data": values},
                    {"errors": ["unavailable"], "data": values},
                    {"success": True, "data": values, "account": "other"},
                    {"data": [{"login": "known", "account": "other"}]}, {"data": [{"login": 19}]},
                    {"data": {"login": "known"}}, {"data": values * 101}, {"unknown": values}):
                with self.subTest(rejected=rejected):
                    request.return_value = rejected
                    with self.assertRaises(operator.OperatorError) as invalid:
                        operator._staff_directory(profile, account)
                    self.assertEqual(invalid.exception.status, 502)

    def test_staff_selector_rejects_mixed_provider_scope_and_changed_generation(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner"}
        scope = {"tenant": TENANT, "actor": 7, "account": "synthetic-staff",
                 "generation": "gen1", "instance": "inst1"}
        with patch.object(operator, "_staff_connection_scope", return_value=scope) as read_scope, \
             patch.object(operator, "_credentials", return_value={"generation": "gen1",
                          "connection_instance_id": "inst1", "token": "synthetic-token", "base_url": "base"}), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_request_operator", return_value={"status": "success", "data": [
                 {"login": "known", "account": "unexpected"}
             ]}) as request:
            with self.assertRaises(operator.OperatorError) as mixed:
                operator._staff_directory(profile, scope["account"])
            self.assertEqual(mixed.exception.status, 502)
            request.return_value = {"status": "success", "data": [{"login": "duplicate"}] * 2}
            with self.assertRaises(operator.OperatorError) as duplicated:
                operator._staff_directory(profile, scope["account"])
            self.assertEqual(duplicated.exception.status, 502)
            request.reset_mock()
            with self.assertRaises(operator.OperatorError) as outdated:
                operator._staff_directory(profile, scope["account"], expected_scope={**scope, "generation": "old"})
            self.assertEqual(outdated.exception.status, 409)
            request.assert_not_called()
            read_scope.side_effect = [scope, scope, {**scope, "generation": "gen2"}]
            with self.assertRaises(operator.OperatorError) as late:
                operator._staff_directory(profile, scope["account"])
            self.assertEqual(late.exception.status, 409)
            request.assert_called_once()

    def test_staff_bind_requires_fresh_emitted_choice_then_rechecks_transactional_scope(self):
        from datetime import datetime, timezone
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner"}
        instance = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        scope = {"tenant": TENANT, "actor": 7, "account": "synthetic-staff",
                 "generation": "gen1", "instance": instance}
        integration = {"status": "connected", "secrets": b"synthetic",
                       "metadata": {"generation": "gen1", "connection_instance_id": instance}}
        row = {"id": instance, "operator_account": scope["account"], "operator_login": "known",
               "version": 1, "verified_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        conn, cursor = MagicMock(), MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = [{"active": True}, None, None, row]
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT, operator._STAFF_ACCOUNT_ENV: scope["account"]}), \
             patch.object(operator, "_connect", return_value=conn), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value={**integration, "metadata": {
                 **integration["metadata"], "generation": "gen2"}}) as locked, \
             patch.object(operator, "_staff_selection_decode", return_value={"scope": scope, "members": "known-digest"}), \
             patch.object(operator, "_staff_directory", return_value=(scope, [{"login": "known"}], "new-digest")) as directory:
            def bind(login="known"):
                return operator.confirm_employee_link(profile, member_telegram_id=19, account=None,
                    login=login, evidence_summary=None, selection_revision="synthetic-revision")
            with self.assertRaises(operator.OperatorError):
                bind()
            cursor.execute.assert_not_called()
            directory.return_value = (scope, [{"login": "known"}], "known-digest")
            with self.assertRaises(operator.OperatorError):
                bind("unknown")
            cursor.execute.assert_not_called()
            with self.assertRaises(operator.OperatorError):
                bind()
            cursor.execute.assert_not_called()
            locked.return_value = integration
            self.assertEqual(bind()["login"], "known")
            directory.assert_called_with(profile, scope["account"], expected_scope=scope)
            locked.assert_called_with(cursor, TENANT, lock=True)
            calls = cursor.execute.call_args_list
            membership = next(index for index, call in enumerate(calls) if "SELECT active FROM" in call.args[0])
            insert = next(index for index, call in enumerate(calls) if "INSERT INTO" in call.args[0] and
                          "saas_beinsystems_operator_employee_links" in call.args[0])
            self.assertLess(membership, insert)
            self.assertIn("manual_operator_user_selection", calls[insert].args[0])
            self.assertIn(operator._STAFF_SELECTION_EVIDENCE, calls[insert].args[1])
            self.assertIn("operator_employee_link_confirmed", calls[-1].args[0])
            conn.commit.assert_called_once()

    def test_staff_roster_and_bind_recheck_fail_closed_on_provider_denial(self):
        base = "https://operator-api.beinsystems.az/v1"
        account = "synthetic-staff"
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        instance = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
        metadata = {"generation": "gen1", "connection_instance_id": instance}
        credentials = {**metadata, "token": "synthetic-jwt", "base_url": base}
        row = {"status": "connected", "secrets": b"synthetic", "metadata": metadata}
        scope = {"tenant": TENANT, "actor": 7, "account": account,
                 "generation": metadata["generation"], "instance": instance}
        logger = MagicMock()
        cases = (
            (401, 'invalid_token', {}, operator_api.employee_users_handler),
            (403, 'insufficient_scope', {}, operator_api.employee_users_handler),
            (401, 'invalid_token', {"member_telegram_id": 19, "login": "known",
                                    "selection_revision": "opaque"}, operator_api.employee_links_handler),
            (403, 'insufficient_scope', {"member_telegram_id": 19, "login": "known",
                                         "selection_revision": "opaque"}, operator_api.employee_links_handler),
        )
        for status, challenge, payload, handler in cases:
            response = FakeRequestsResponse(
                {"detail": "private upstream detail synthetic-jwt"},
                status_code=status, headers={"WWW-Authenticate": f'Bearer error="{challenge}"'})
            transport = FakeRequestsSession(response)
            conn, cursor = MagicMock(), MagicMock()
            conn.__enter__.return_value = conn
            conn.cursor.return_value.__enter__.return_value = cursor
            with (
                self.subTest(status=status, handler=handler.__name__),
                patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT, operator._STAFF_ACCOUNT_ENV: account}),
                patch.object(operator_api, "_body", new=AsyncMock(return_value=payload)),
                patch.object(operator, "_connect", return_value=conn),
                patch.object(operator, "_ensure_schema"),
                patch.object(operator, "_current_profile", return_value=profile),
                patch.object(operator, "_operator_row", return_value=row) as integration,
                patch.object(operator, "_credentials", return_value=credentials),
                patch.object(operator, "_record_operator_read") as audit,
                patch.object(operator, "_staff_selection_decode", return_value={"scope": scope, "members": "digest"}),
                patch.object(operator, "_NoRedirectRequestsSession", return_value=transport),
                patch.object(operator, "build_opener") as urllib_opener,
            ):
                endpoint = handler(lambda _: profile, "https://crm.example", logger)
                result = asyncio.run(endpoint(MagicMock(method="POST")))
                self.assertEqual(result.status, 502)
                self.assertEqual(result.headers["Cache-Control"], "no-store")
                self.assertEqual(set(json.loads(result.text)), {"success", "error"})
                self.assertIn(challenge, json.loads(result.text)["error"])
                for secret in ("private upstream detail", "synthetic-jwt", account, "opaque"):
                    self.assertNotIn(secret, result.text)
                self.assertEqual(transport.url, base + "/user/profileUsers")
                self.assertEqual(transport.post_calls, 1)
                self.assertEqual(transport.get_calls, 0)
                self.assertEqual(transport.kwargs["json"], {"account": account})
                self.assertEqual(transport.kwargs["headers"], {
                    "Authorization": "Bearer synthetic-jwt", "Accept": "application/json"})
                self.assertFalse(transport.kwargs["allow_redirects"])
                self.assertTrue(transport.kwargs["verify"])
                self.assertTrue(transport.kwargs["stream"])
                self.assertFalse(transport.trust_env)
                self.assertFalse(response.iterated)
                self.assertTrue(response.closed)
                self.assertTrue(transport.closed)
                urllib_opener.assert_not_called()
                audit.assert_called_once_with(profile, "profile_users", {"account": account})
                self.assertEqual(integration.call_count, 2)
                cursor.execute.assert_not_called()
                conn.commit.assert_not_called()

    def test_requests_staff_option_is_exact_route_only_and_preserves_redirect_and_size_limits(self):
        base = "https://operator-api.beinsystems.az/v1"
        account = {"account": "synthetic-staff"}
        for bad_endpoint, bad_payload, bad_base in (
                ("me", account, base), ("accounts", account, base),
                ("profile_users", {}, base), ("profile_users", {**account, "search": "foo"}, base),
                ("profile_users", account, "https://example.invalid/v1")):
            with self.subTest(endpoint=bad_endpoint, payload=bad_payload, base=bad_base), \
                 patch.object(operator, "_NoRedirectRequestsSession") as transport:
                with self.assertRaises(operator.OperatorError):
                    operator._request_operator(bad_endpoint, bad_payload, token="synthetic",
                                               base_url=bad_base, requests_staff_directory=True)
                transport.assert_not_called()
        for response in (
                FakeRequestsResponse({"detail": "private redirect"}, status_code=307,
                                     headers={"Location": "https://example.invalid"}),
                FakeRequestsResponse(chunks=[b"x" * 5])):
            fake = FakeRequestsSession(response)
            with self.subTest(status=response.status_code), \
                 patch.object(operator, "MAX_RESPONSE_BYTES", 4), \
                 patch.object(operator, "_NoRedirectRequestsSession", return_value=fake):
                with self.assertRaises(operator.OperatorError) as raised:
                    operator._request_operator("profile_users", account, token="synthetic",
                                               base_url=base, requests_staff_directory=True)
                self.assertEqual(fake.post_calls, 1)
                self.assertEqual(response.iterated, response.status_code == 200)
                self.assertTrue(response.closed)
                self.assertNotIn("private redirect", str(raised.exception))

    def test_staff_http_accepts_only_exact_new_selector_shape_or_legacy(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner"}
        logger = MagicMock()
        users = operator_api.employee_users_handler(lambda _: profile, "https://crm.example", logger)
        links = operator_api.employee_links_handler(lambda _: profile, "https://crm.example", logger)
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator_api, "_body", new=AsyncMock(return_value={})) as body, \
             patch.object(operator, "staff_employee_users", return_value={
                 "users": [{"login": "known", "bindable": True}], "selection_revision": "opaque"}) as staff, \
             patch.object(operator, "operator_employee_users", return_value=[]) as legacy, \
             patch.object(operator, "confirm_employee_link", return_value={"status": "confirmed"}) as bind:
            request = MagicMock(method="POST")
            result = asyncio.run(users(request))
            self.assertEqual(result.status, 200)
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertEqual(json.loads(result.text)["selection_revision"], "opaque")
            staff.assert_called_once_with(profile)
            body.return_value = {"account": "legacy"}
            self.assertEqual(asyncio.run(users(request)).status, 200)
            legacy.assert_called_once_with(profile, "legacy")
            body.return_value = {"member_telegram_id": 19, "login": "known"}
            self.assertEqual(asyncio.run(links(request)).status, 400)
            body.return_value["selection_revision"] = "opaque"
            self.assertEqual(asyncio.run(links(request)).status, 201)
            bind.assert_called_once_with(profile, member_telegram_id=19, account=None, login="known",
                                         evidence_summary=None, selection_revision="opaque")
            body.return_value["account"] = "forged"
            self.assertEqual(asyncio.run(links(request)).status, 400)
            bind.assert_called_once()

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

    def test_read_adapter_uses_approved_host_requests_json_and_bearer(self):
        response = FakeRequestsResponse({"status": "success", "data": [{"account": "client-1"}]})
        fake = FakeRequestsSession(response)
        base = "https://operator-api.beinsystems.az/v1"
        with patch.object(operator, "_NoRedirectRequestsSession", return_value=fake) as session, \
             patch.object(operator, "build_opener") as urllib_opener:
            result = operator._request_operator(
                "accounts", {"search": "client", "limit": 1}, token="synthetic-jwt",
                base_url=base)
        self.assertEqual(result["data"][0]["account"], "client-1")
        session.assert_called_once_with()
        urllib_opener.assert_not_called()
        self.assertEqual(fake.url, base + "/user/accounts")
        self.assertEqual(fake.kwargs["json"], {"search": "client", "limit": 1})
        self.assertEqual(fake.kwargs["headers"], {"Authorization": "Bearer synthetic-jwt"})
        self.assertEqual(fake.kwargs["timeout"], operator.REQUEST_TIMEOUT_SECONDS)
        self.assertFalse(fake.kwargs["allow_redirects"])
        self.assertTrue(fake.kwargs["stream"])
        self.assertTrue(fake.kwargs["verify"])
        self.assertNotIn("User-Agent", fake.kwargs["headers"])
        self.assertFalse(fake.trust_env)
        self.assertTrue(fake.closed)
        self.assertTrue(response.closed)
        self.assertEqual(response.chunk_size, 64 * 1024)

    def test_accounts_request_always_sends_the_default_limit(self):
        with patch.object(operator, "api_base_url", return_value=operator.OPERATOR_API_BASE_URL), \
             patch.object(operator, "_request_accounts_with_requests", return_value=b'{"status":"success","data":[]}') as call:
            operator._request_operator("accounts", {}, token="synthetic-jwt")
        self.assertEqual(call.call_args.args[1], {"limit": 1000})

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

    def test_owner_directory_me_requests_transport_is_bounded_direct_and_has_no_body(self):
        response = FakeRequestsResponse({"status":"success", "data":{
            "identifier":"synthetic.operator", "role":"superadmin"}})
        fake = FakeRequestsSession(response)
        base = "https://operator-api.beinsystems.az/v1"
        with patch.object(operator, "_NoRedirectRequestsSession", return_value=fake), \
             patch.object(operator, "build_opener") as urllib_opener:
            result = operator._request_operator("me", {}, token="synthetic-jwt", base_url=base,
                                                requests_me=True)
        self.assertEqual(result["data"]["identifier"], "synthetic.operator")
        self.assertEqual(fake.url, base + "/user/me")
        self.assertEqual(fake.get_calls, 1)
        self.assertEqual(fake.post_calls, 0)
        self.assertEqual(fake.kwargs["headers"], {"Authorization":"Bearer synthetic-jwt"})
        self.assertFalse(fake.kwargs["allow_redirects"])
        self.assertTrue(fake.kwargs["stream"])
        self.assertTrue(fake.kwargs["verify"])
        self.assertFalse(fake.trust_env)
        self.assertEqual(response.chunk_size, 64 * 1024)
        self.assertTrue(response.closed)
        self.assertTrue(fake.closed)
        urllib_opener.assert_not_called()

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
            response = FakeRequestsResponse(
                {"detail": "private provider body synthetic-jwt"},
                status_code=status, headers=headers)
            fake = FakeRequestsSession(response)
            with self.subTest(status=status, expected_code=expected_code), \
                 patch.dict("os.environ", {access.API_BASE_URL_ENV: base}), \
                 patch.object(operator, "_NoRedirectRequestsSession", return_value=fake):
                with self.assertRaises(operator.OperatorError) as raised:
                    operator._request_operator(
                        "accounts", {"search": "synthetic-account", "limit": 1},
                        token="synthetic-jwt", base_url=base)
            self.assertEqual(raised.exception.upstream_error_code, expected_code)
            self.assertEqual(raised.exception.upstream_http_status, status)
            self.assertNotIn("private provider body", str(raised.exception))
            self.assertNotIn("synthetic-jwt", str(raised.exception))
            self.assertNotIn("private-detail", str(raised.exception))
            self.assertNotIn("error_description", str(raised.exception))
            self.assertFalse(response.iterated)
            self.assertTrue(response.closed)

    def test_accounts_request_does_not_follow_redirects(self):
        base = "https://operator-api.beinsystems.az/v1"
        response = FakeRequestsResponse(
            {"detail": "private redirect response"}, status_code=302,
            headers={"Location": "https://attacker.example/"})
        fake = FakeRequestsSession(response)
        with patch.dict("os.environ", {access.API_BASE_URL_ENV: base}), \
             patch.object(operator, "_NoRedirectRequestsSession", return_value=fake):
            with self.assertRaises(operator.OperatorError) as raised:
                operator._request_operator(
                    "accounts", {"search": "synthetic-account", "limit": 1},
                    token="synthetic-jwt", base_url=base)
        self.assertEqual(fake.post_calls, 1)
        self.assertFalse(fake.kwargs["allow_redirects"])
        self.assertFalse(response.iterated)
        self.assertNotIn("private redirect response", str(raised.exception))
        self.assertNotIn("attacker.example", str(raised.exception))

    def test_real_requests_session_does_not_prefetch_redirect_body(self):
        # The real requests Session.send/resolve_redirects path runs here; only
        # the adapter's upstream Response is synthetic, so this cannot reach Operator.
        base = "https://operator-api.beinsystems.az/v1"
        raw = CountingResponseBody(b"synthetic redirect body beyond the response limit")
        adapter = SyntheticResponseAdapter(
            302, {"Location": "/redirect"}, raw)
        session = operator._NoRedirectRequestsSession()
        session.mount("https://", adapter)
        with patch.dict("os.environ", {access.API_BASE_URL_ENV: base}), \
             patch.object(operator, "_NoRedirectRequestsSession", return_value=session), \
             patch.object(operator, "MAX_RESPONSE_BYTES", 8):
            with self.assertRaises(operator.OperatorError) as raised:
                operator._request_operator(
                    "accounts", {"search": "synthetic-account", "limit": 1},
                    token="synthetic-jwt", base_url=base)
        self.assertEqual(str(raised.exception), "Operator временно не ответил.")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(adapter.request.url, base + "/user/accounts")
        self.assertEqual(adapter.request.headers["Authorization"], "Bearer synthetic-jwt")
        self.assertEqual(json.loads(adapter.request.body), {
            "search": "synthetic-account", "limit": 1,
        })
        self.assertTrue(adapter.kwargs["stream"])
        self.assertTrue(adapter.kwargs["verify"])
        self.assertTrue(adapter.request.headers["User-Agent"].startswith("python-requests/"))
        self.assertEqual(raw.stream_calls, 0)
        self.assertEqual(raw.bytes_yielded, 0)
        self.assertEqual(raw.read_calls, [])
        self.assertTrue(raw.closed)

    def test_accounts_request_bounds_streamed_response_and_closes_it(self):
        base = "https://operator-api.beinsystems.az/v1"
        response = FakeRequestsResponse(
            chunks=[b"123456", b"78901"])
        fake = FakeRequestsSession(response)
        with patch.dict("os.environ", {access.API_BASE_URL_ENV: base}), \
             patch.object(operator, "MAX_RESPONSE_BYTES", 10), \
             patch.object(operator, "_NoRedirectRequestsSession", return_value=fake):
            with self.assertRaises(operator.OperatorError) as raised:
                operator._request_operator(
                    "accounts", {"search": "synthetic-account", "limit": 1},
                    token="synthetic-jwt", base_url=base)
        self.assertEqual(str(raised.exception), "Ответ Operator превышает допустимый размер.")
        self.assertTrue(response.closed)
        self.assertTrue(fake.closed)
        self.assertEqual(response.chunk_size, 64 * 1024)

    def test_accounts_transport_failure_is_generic_and_does_not_fallback(self):
        base = "https://operator-api.beinsystems.az/v1"
        fake = FakeRequestsSession(FakeRequestsResponse())
        fake.post = MagicMock(side_effect=operator.requests.ConnectionError(
            "private transport detail synthetic-jwt"))
        with patch.dict("os.environ", {access.API_BASE_URL_ENV: base}), \
             patch.object(operator, "_NoRedirectRequestsSession", return_value=fake), \
             patch.object(operator, "build_opener") as urllib_opener:
            with self.assertRaises(operator.OperatorError) as raised:
                operator._request_operator(
                    "accounts", {"search": "synthetic-account", "limit": 1},
                    token="synthetic-jwt", base_url=base)
        self.assertEqual(str(raised.exception), "Operator временно не ответил.")
        self.assertNotIn("private transport detail", str(raised.exception))
        self.assertNotIn("synthetic-jwt", str(raised.exception))
        urllib_opener.assert_not_called()

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
            None,
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

    def test_authenticated_directory_search_uses_active_first_and_returns_only_one_id(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        active_row = {"status": "connected", "secrets": b"encrypted"}
        credentials = {"token": "synthetic-active-jwt",
            "base_url": "https://operator-api.beinsystems.az/v1",
            "generation": "g1", "connection_instance_id": "c1"}
        db = MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        db.cursor.return_value = cursor
        responses = [
            {"status": "success", "data": {"identifier": "secret@example.test",
                "role": "superadmin", "partnerPin": "private"}},
            {"status": "success", "data": [{"account": "Synthetic Account",
                "name": "private name", "email": "private@example.test"}]},
        ]
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=db), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=active_row), \
             patch.object(operator, "_pending_auth_candidate") as pending, \
             patch.object(operator, "_credentials", return_value=credentials), \
             patch.object(operator, "_record_operator_read") as audit, \
             patch.object(operator, "_operator_directory_fence"), \
             patch.object(operator, "_request_operator", side_effect=responses) as request:
            result = operator.search_authenticated_operator_account(profile, " Synthetic ")
        self.assertEqual(result, {"source": "active", "account_id": "Synthetic Account",
                                  "result_count": 1, "exact_match": False})
        self.assertEqual([call.args[0] for call in request.call_args_list], ["me", "accounts"])
        self.assertEqual(request.call_args_list[0].args[1], {})
        self.assertEqual(request.call_args_list[1].args[1],
                         {"search": "Synthetic", "limit": 1})
        self.assertEqual([call.args[1] for call in audit.call_args_list], ["me", "accounts"])
        pending.assert_not_called()
        self.assertNotIn("secret@example.test", json.dumps(result))
        self.assertNotIn("superadmin", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))

    def test_authenticated_directory_search_empty_and_bad_identity_fail_closed(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        credentials = {"token": "synthetic-pending-jwt", "received_at": "r1",
            "base_url": "https://operator-api.beinsystems.az/v1"}
        db = MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        db.cursor.return_value = cursor
        base_patches = [
            patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}),
            patch.object(operator, "_connect", return_value=db),
            patch.object(operator, "_ensure_schema"),
            patch.object(operator, "_current_profile", return_value=profile),
            patch.object(operator, "_operator_row", return_value=None),
            patch.object(operator, "_pending_auth_candidate", return_value=credentials),
            patch.object(operator, "_record_operator_read"),
            patch.object(operator, "_operator_directory_fence"),
        ]
        for patcher in base_patches: patcher.start()
        try:
            with patch.object(operator, "_request_operator", side_effect=[
                    {"status":"success", "data":{"identifier":"hidden", "role":"owner"}},
                    {"status":"success", "data":[]}]) as request:
                self.assertEqual(operator.search_authenticated_operator_account(
                    profile, "empty query"), {"source":"pending", "account_id":None,
                                               "result_count":0, "exact_match":False})
            self.assertEqual(request.call_count, 2)
            with patch.object(operator, "_request_operator", return_value={
                    "status":"failure", "data":{"identifier":"hidden", "role":"owner"}}) as request:
                with self.assertRaises(operator.OperatorError):
                    operator.search_authenticated_operator_account(profile, "empty query")
                request.assert_called_once()
            with patch.object(operator, "_request_operator", return_value={
                    "status":"success", "data":{"identifier":"hidden"}}) as request:
                with self.assertRaises(operator.OperatorError):
                    operator.search_authenticated_operator_account(profile, "empty query")
                request.assert_called_once()
            with patch.object(operator, "_request_operator", side_effect=[
                    {"status":"success", "data":{"identifier":"hidden", "role":"owner"}},
                    {"status":"success", "data":[{"account":"one"},{"account":"two"}]}]) as request:
                with self.assertRaises(operator.OperatorError):
                    operator.search_authenticated_operator_account(profile, "empty query")
                self.assertEqual(request.call_count, 2)
        finally:
            for patcher in reversed(base_patches): patcher.stop()

    def test_authenticated_directory_search_rejects_invalid_input_before_network(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        for value in ("abc", "x" * 121, "okay\nsearch"):
            with self.subTest(value=value), patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
                 patch.object(operator, "_connect") as connect, \
                 patch.object(operator, "_request_operator") as request:
                with self.assertRaises(operator.OperatorError):
                    operator.search_authenticated_operator_account(profile, value)
                connect.assert_not_called()
                request.assert_not_called()
        profile["role"] = "manager"
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_request_operator") as request:
            with self.assertRaises(operator.OperatorError):
                operator.search_authenticated_operator_account(profile, "valid search")
            request.assert_not_called()

    def test_directory_fence_rejects_active_generation_change_and_active_appearing_for_pending(self):
        credentials = {"generation":"g1", "connection_instance_id":"c1",
            "token":"synthetic", "base_url":"https://operator-api.beinsystems.az/v1"}
        profile = {"tenant_id":TENANT, "telegram_id":7}
        fresh = {**credentials, "generation":"g2"}
        with patch.object(operator, "_assert_current_operator_owner"), \
             patch.object(operator, "_credentials", return_value=fresh):
            with self.assertRaises(operator.OperatorError):
                operator._operator_directory_fence(profile, TENANT, 7, "active", credentials)

        db = MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        db.cursor.return_value = cursor
        with patch.object(operator, "_assert_current_operator_owner"), \
             patch.object(operator, "_connect", return_value=db), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_operator_row", return_value={
                 "status":"connected", "secrets":b"active"}):
            with self.assertRaises(operator.OperatorError):
                operator._operator_directory_fence(profile, TENANT, 7, "pending",
                    {"received_at":"r1"})

    def test_authenticated_directory_search_stops_if_owner_or_active_generation_changes_after_me(self):
        profile = {"tenant_id":TENANT, "telegram_id":7, "role":"owner", "active":True}
        credentials = {"token":"synthetic-active-jwt", "base_url":"https://operator-api.beinsystems.az/v1",
            "generation":"g1", "connection_instance_id":"c1"}
        db = MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        db.cursor.return_value = cursor
        responses = [{"status":"success", "data":{"identifier":"hidden", "role":"owner"}}]
        with patch.dict("os.environ", {access.WORKSPACE_ENV:TENANT}), \
             patch.object(operator, "_connect", return_value=db), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value={"status":"connected", "secrets":b"x"}), \
             patch.object(operator, "_credentials", return_value=credentials), \
             patch.object(operator, "_record_operator_read"), \
             patch.object(operator, "_operator_directory_fence",
                          side_effect=[None, operator.OperatorError("owner changed", 409)]) as fence, \
             patch.object(operator, "_request_operator", side_effect=responses) as request:
            with self.assertRaises(operator.OperatorError):
                operator.search_authenticated_operator_account(profile, "valid query")
        self.assertEqual(fence.call_count, 2)
        request.assert_called_once()

    def test_authenticated_directory_search_requires_configured_tenant(self):
        profile = {"tenant_id":"bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb", "telegram_id":7,
                   "role":"owner", "active":True}
        with patch.dict("os.environ", {access.WORKSPACE_ENV:TENANT}), \
             patch.object(operator, "_request_operator") as request:
            with self.assertRaises(operator.OperatorError):
                operator.search_authenticated_operator_account(profile, "valid query")
            request.assert_not_called()

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
        self.assertEqual([call.args for call in request.call_args_list], [("me", {}), ("accounts", {})])
        self.assertIs(request.call_args_list[0].kwargs["requests_me"], True)
        self.assertNotIn("requests_me", request.call_args_list[1].kwargs)
        self.assertTrue(all(call.kwargs["token"]=="synthetic-candidate-token"
                            for call in request.call_args_list))
        self.assertEqual(audit.call_count,2)
        upsert.assert_called_once()
        activate.assert_called_once_with(cursor,TENANT,7,"synthetic-candidate-token",
                                         verified_identifier="synthetic.operator")
        save.assert_not_called()

    def test_full_import_never_writes_if_canonical_identity_or_accounts_proof_fails(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        candidate = {"token": "synthetic-token", "received_at": "2026-11-19T00:00:00Z",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        identity = {"status": "success", "data": {"identifier": "synthetic.operator", "role": "operator"}}
        accounts = {"status": "success", "data": [{"account": "synthetic-account"}]}
        denied = operator.OperatorError("synthetic denied", 502, upstream_http_status=403)
        for responses, current, calls in (
                ([denied], True, ["me"]),
                ([{"status": "failure"}], True, ["me"]),
                ([identity, denied], True, ["me", "accounts"]),
                ([identity, {"status": "success", "data": [{"not_account": "synthetic"}]}],
                 True, ["me", "accounts"]),
                ([identity, accounts], False, ["me", "accounts"])):
            with self.subTest(responses=responses, current=current):
                connection = MagicMock()
                with (
                    patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}),
                    patch.object(operator, "_connect", return_value=connection),
                    patch.object(operator, "_ensure_schema"),
                    patch.object(operator, "_current_profile", return_value=profile),
                    patch.object(operator, "_operator_row", return_value=None),
                    patch.object(operator, "_pending_auth_candidate", return_value=candidate),
                    patch.object(operator, "_record_operator_read"),
                    patch.object(operator, "_request_operator", side_effect=responses) as request,
                    patch.object(operator, "_pending_timestamp_is_current", return_value=current),
                    patch.object(operator, "_lock_accounts_capability_scope"),
                    patch.object(operator, "_upsert_imported_accounts") as upsert,
                    patch.object(operator, "_revoke_accounts_capability") as revoke,
                    patch.object(operator, "_activate_candidate_if_unconfigured") as activate,
                ):
                    with self.assertRaises(operator.OperatorError):
                        operator.import_accounts(profile)
                self.assertEqual([call.args[0] for call in request.call_args_list], calls)
                self.assertIs(request.call_args_list[0].kwargs["requests_me"], True)
                upsert.assert_not_called()
                revoke.assert_not_called()
                activate.assert_not_called()
                connection.__enter__.return_value.commit.assert_not_called()

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
        for invalid_limit in (0, 1001, True, 1.0, "1"):
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
        self.assertIn("CHECK(endpoint IN ('me','accounts','account_info','profile_users','task_list','auth_login','widget'))", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_employee_links", sql)
        self.assertIn("FOREIGN KEY(tenant_id,member_telegram_id)", sql)
        self.assertIn("saas_operator_active_member_link_idx", sql)
        self.assertIn("saas_operator_active_staff_identity_idx", sql)



class OperatorCompanyImportTests(unittest.TestCase):
    def test_pending_capability_preview_discloses_only_verified_identifier_and_revision(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        candidate = {"token": "synthetic-jwt", "received_at": "revision-1",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        identity = {"identifier": "owner@synthetic.test", "role": "superadmin"}
        with patch.object(company_import, "_verified_pending_identity",
                          return_value=(TENANT, 7, candidate, identity)) as verify:
            result = company_import.prepare_accounts_capability(profile, "revision-1")
        self.assertEqual(result, {"identifier": "owner@synthetic.test",
                                  "candidate_received_at": "revision-1"})
        self.assertNotIn("role", result)
        self.assertNotIn("token", json.dumps(result))
        verify.assert_called_once_with(profile, "revision-1")

    def test_pending_capability_confirmation_requires_exact_live_identity_before_writing(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        candidate = {"token": "synthetic-jwt", "received_at": "revision-1",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        identity = {"identifier": "Owner@Synthetic.test", "role": "superadmin"}
        with patch.object(company_import, "_verified_pending_identity",
                          return_value=(TENANT, 7, candidate, identity)), \
             patch.object(operator, "_connect") as connect:
            with self.assertRaises(operator.OperatorError):
                company_import.confirm_accounts_capability(
                    profile, acknowledged_identifier="other@synthetic.test",
                    candidate_received_at="revision-1")
        connect.assert_not_called()

    def test_pending_capability_is_a_separate_fingerprinted_expiring_grant(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        candidate = {"token": "synthetic-jwt", "received_at": "revision-1",
                     "base_url": "https://operator-api.beinsystems.az/v1"}
        identity = {"identifier": "Owner@Synthetic.test", "role": "superadmin"}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.return_value = {"received_at": "revision-1"}
        connection.cursor.return_value = cursor
        with patch.object(company_import, "_verified_pending_identity",
                          return_value=(TENANT, 7, candidate, identity)), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_lock_accounts_capability_scope") as lock_scope, \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_operator_row", return_value=None):
            result = company_import.confirm_accounts_capability(
                profile, acknowledged_identifier="owner@synthetic.TEST",
                candidate_received_at="revision-1")
        self.assertEqual(result, {"ready": True, "label": "accounts_only",
                                  "expires_in_hours": 24})
        self.assertEqual(lock_scope.call_count, 1)
        sql = "\\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("saas_beinsystems_operator_accounts_capabilities", sql)
        self.assertNotIn("saas_tenant_integrations", sql)
        self.assertNotIn("synthetic-jwt", repr(cursor.execute.call_args_list))
        insert_args = cursor.execute.call_args_list[-1].args[1]
        self.assertEqual(insert_args[3], company_import._identifier_fingerprint(
            "Owner@Synthetic.test"))
        self.assertNotEqual(insert_args[3], "Owner@Synthetic.test")
        connection.commit.assert_called_once()

    def test_disconnect_revokes_capability_and_deletes_v2_candidate_under_scope_lock(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchone.return_value = None
        connection.cursor.return_value = cursor
        with patch.dict("os.environ", {access.WORKSPACE_ENV: TENANT}), \
             patch.object(operator, "_connect", return_value=connection), \
             patch.object(operator, "_ensure_schema"), \
             patch.object(operator, "_current_profile", return_value=profile), \
             patch.object(operator, "_lock_accounts_capability_scope") as lock_scope:
            operator.disconnect(profile)
        lock_scope.assert_called_once_with(cursor, TENANT)
        sql = "\\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertIn("DELETE FROM saas_beinsystems_operator_accounts_capabilities", sql)
        self.assertIn("DELETE FROM saas_beinsystems_operator_pending_auth_v2", sql)
        self.assertIn("UPDATE saas_tenant_integrations SET status='not_connected'", sql)
        connection.commit.assert_called_once()

    def test_selected_account_rechecks_identity_and_posts_only_one_exact_account(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        credentials = {"token": "synthetic-jwt", "base_url": "https://operator-api.beinsystems.az/v1",
            "generation": "g1", "connection_instance_id": "instance-1", "status": "connected"}
        responses = [
            {"status": "success", "data": {"identifier": "owner@synthetic.test",
                "role": "superadmin", "partnerPin": "private"}},
            {"status": "success", "data": [{"account": "Exact Account"}]},
        ]
        with patch.object(operator, "_record_operator_read") as audit, \
             patch.object(company_import, "_selected_source_fence") as fence, \
             patch.object(operator, "_request_operator", side_effect=responses) as request:
            result = company_import._verify_selected_account(
                profile, TENANT, 7, "active", credentials, "Exact Account")
        self.assertIsNone(result)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["me", "accounts"])
        self.assertTrue(request.call_args_list[0].kwargs["requests_me"])
        self.assertEqual(request.call_args_list[1].args[1],
                         {"search": "Exact Account", "limit": 1})
        self.assertTrue(all(call.kwargs["token"] == "synthetic-jwt"
                            for call in request.call_args_list))
        self.assertEqual(fence.call_count, 4,
                         "the same source is fenced before and after each provider call")
        self.assertEqual([call.args[1] for call in audit.call_args_list], ["me", "accounts"])
        self.assertNotIn("partnerPin", repr(result))

    def test_selected_account_rejects_provider_ignoring_limit_and_case_only_match(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        credentials = {"token": "synthetic-jwt", "base_url": "https://operator-api.beinsystems.az/v1",
            "generation": "g1", "connection_instance_id": "instance-1", "status": "connected"}
        identity = {"status": "success", "data": {"identifier": "owner@synthetic.test",
            "role": "operator"}}
        for response in (
            {"status": "success", "data": [{"account": "Exact Account"},
                                             {"account": "Second Account"}]},
            {"status": "success", "data": [{"account": "exact account"}]},
            {"status": "success", "data": [{"account": "Exact Account"}],
             "identity": {"email": "private"}},
        ):
            with self.subTest(response=response), \
                 patch.object(operator, "_record_operator_read"), \
                 patch.object(company_import, "_selected_source_fence"), \
                 patch.object(operator, "_request_operator", side_effect=[identity, response]):
                with self.assertRaises(operator.OperatorError):
                    company_import._verify_selected_account(
                        profile, TENANT, 7, "active", credentials, "Exact Account")

    def test_selected_account_fails_after_identity_or_generation_changes_before_accounts(self):
        profile = {"tenant_id": TENANT, "telegram_id": 7, "role": "owner", "active": True}
        credentials = {"token": "synthetic-jwt", "base_url": "https://operator-api.beinsystems.az/v1",
            "generation": "g1", "connection_instance_id": "instance-1", "status": "connected"}
        identity = {"status": "success", "data": {"identifier": "owner@synthetic.test",
            "role": "operator"}}
        with patch.object(operator, "_record_operator_read"), \
             patch.object(company_import, "_selected_source_fence",
                          side_effect=[None, operator.OperatorError("rotated", 409)]) as fence, \
             patch.object(operator, "_request_operator", return_value=identity) as request:
            with self.assertRaises(operator.OperatorError):
                company_import._verify_selected_account(
                    profile, TENANT, 7, "active", credentials, "Exact Account")
        self.assertEqual(fence.call_count, 2)
        request.assert_called_once()

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
