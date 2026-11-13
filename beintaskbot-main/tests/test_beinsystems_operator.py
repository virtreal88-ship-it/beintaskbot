import json
import unittest
from unittest.mock import patch

import tenant_beinsystems_operator as operator
import tenant_beinsystems_operator_access as access
import tenant_beinsystems_operator_ai_tools as ai_tools
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


class FakeOpener:
    def __init__(self, payload):
        self.response = FakeResponse(payload)
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return self.response


class FakeCursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))


class BeinSystemsOperatorTests(unittest.TestCase):
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

    def test_api_base_url_requires_server_side_https_origin(self):
        with patch.dict("os.environ", {access.API_BASE_URL_ENV: "https://operator.example/api"}):
            self.assertEqual(access.api_base_url(), "https://operator.example/api")
        for value in ("http://operator.example", "https://user:pass@operator.example",
                      "https://operator.example:444", "//operator.example"):
            with patch.dict("os.environ", {access.API_BASE_URL_ENV: value}):
                self.assertEqual(access.api_base_url(), "")

    def test_read_adapter_uses_only_documented_endpoint_and_bearer(self):
        fake = FakeOpener({"status": "success", "data": [{"account": "client-1"}]})
        result = operator._request_operator(
            "accounts", {"search": "client"}, token="secret-jwt",
            base_url="https://operator.example/api", opener=fake)
        self.assertEqual(result["data"][0]["account"], "client-1")
        self.assertEqual(fake.request.full_url, "https://operator.example/api/user/accounts")
        self.assertEqual(fake.request.get_method(), "POST")
        self.assertEqual(fake.request.get_header("Authorization"), "Bearer secret-jwt")
        self.assertEqual(json.loads(fake.request.data), {"search": "client"})
        self.assertEqual(fake.timeout, operator.REQUEST_TIMEOUT_SECONDS)

    def test_adapter_rejects_undocumented_mutations_and_short_search(self):
        with self.assertRaises(operator.OperatorError):
            operator._request_operator("task_create", {"title": "x"}, token="jwt",
                                       base_url="https://operator.example",
                                       opener=FakeOpener({}))
        with self.assertRaises(operator.OperatorError):
            operator._request_operator("accounts", {"search": "abc"}, token="jwt",
                                       base_url="https://operator.example",
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

    def test_account_summary_drops_financial_and_administrative_fields(self):
        response = {"status": "success", "data": {
            "account": "acct-7", "balance": 500, "role": "superadmin",
            "last_login": "private", "last_contact": "2026-11-01",
            "partner_name": "Partner", "token": "secret",
        }}
        self.assertEqual(operator.account_info_payload(response, "acct-7"), {
            "account": "acct-7", "last_contact": "2026-11-01", "partner_name": "Partner",
        })

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
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_employee_links", sql)
        self.assertIn("FOREIGN KEY(tenant_id,member_telegram_id)", sql)
        self.assertIn("saas_operator_active_member_link_idx", sql)
        self.assertIn("saas_operator_active_staff_identity_idx", sql)


if __name__ == "__main__":
    unittest.main()
