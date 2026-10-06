"""Exercise Linear access without importing the bot's startup side effects."""

import ast
import asyncio
import re
import time
import types
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


ADMIN = 1628569350
RUFAT = 6824377548
HUSEYN = 7329891614
RASIM = 7920785774
MOVED_RUFAT = 8835096199


def load_linear_functions():
    source = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig"))
    names = {
        "_LINEAR_ACCOUNT_ALIASES", "_LINEAR_OPERATOR_BY_CHAT", "_LINEAR_CREATE_ACCOUNT_BY_CHAT",
        "get_rufat_compat_chat_ids", "is_rufat_chat", "_linear_identity_chat_id",
        "_linear_operator_for_chat", "_linear_create_identity_for_chat", "_linear_can_create_for_chat",
        "_linear_account_key", "_linear_account_matches", "_linear_account_scope", "_linear_issue_matches_scope", "_linear_save_test_result",
        "_linear_client_hint", "_linear_description_metadata", "_load_linear_tesdiq_issues",
        "handle_api_linear_tesdiq", "handle_api_session",
    }
    selected = [
        node for node in source.body
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names)
        or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets))
    ]
    permissions = {ADMIN: {"linear", "linear_edit"}, RUFAT: {"linear", "linear_create"}, HUSEYN: {"linear", "linear_create"}, RASIM: {"linear", "linear_create"}, MOVED_RUFAT: {"linear", "linear_create"}}
    web = types.SimpleNamespace(Request=dict, Response=dict, json_response=lambda data, status=200: {"status": status, "data": data})
    namespace = {
        "ADMIN_CHAT_ID": ADMIN, "RUFAT_CHAT_ID": RUFAT, "HUSEYN_CHAT_ID": HUSEYN, "RASIM_CHAT_ID": RASIM,
        "asyncio": asyncio, "re": re, "unicodedata": unicodedata, "_time_module": time, "web": web,
        "is_admin": lambda chat_id: chat_id == ADMIN,
        "employee_has_permission": lambda chat_id, permission: permission in permissions.get(chat_id, set()),
        "_load_employee_access_records": lambda: {str(RUFAT): {"migrated_to": MOVED_RUFAT}},
        "employee_access_profile": lambda chat_id: {"active": bool(permissions.get(chat_id)), "role": "Əməkdaş", "permissions": list(permissions.get(chat_id, set()))},
        "logger": Mock(), "list_linear_accounts": Mock(side_effect=AssertionError("Local dictionary must not control access")),
        "LINEAR_TEAM_ID": "team", "LINEAR_TESDIQ_STATE_ID": "testiq", "LINEAR_TRIAGE_STATE_ID": "triage",
        "LINEAR_ALLOWED_STATUS_NAMES": {"testiq", "triage", "todo", "in progress", "in review", "done"},
        "_LINEAR_TESDIQ_CACHE": {"at": 0.0}, "_LINEAR_TESDIQ_CACHE_TTL": 45,
        "_linear_uuid": lambda value, label: value,
        "_linear_allowed_state_options": lambda: [], "_linear_projects": lambda: [], "_linear_team_members": lambda: [],
        "_notify_linear_status_transitions": AsyncMock(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "<linear-access>", "exec"), namespace)
    return namespace, permissions


class Request(dict):
    def __init__(self, chat_id, method="GET", payload=None, query=None):
        super().__init__(authenticated_chat_id=chat_id)
        self.method = method
        self.payload = payload or {}
        self.query = query or {}

    async def json(self):
        return self.payload


def issue(identifier, account, operator="nurane@beinsystems", state="Triage"):
    return {
        "id": identifier, "identifier": identifier, "title": "Task title",
        "description": f"Operator: {operator}\nAccount: {account}\n\nTask text",
        "state": {"id": state.lower(), "name": state},
    }


class LinearAccessTests(unittest.TestCase):
    def setUp(self):
        self.ns, self.permissions = load_linear_functions()
        self.rows = [
            issue("own-other-operator", "rufet"),
            issue("own-done", " RÜFƏT ", state="Done"),
            issue("other-own-operator", "huseyn", "rufet@beinsystems"),
            issue("prefix-collision", "rufet2"), issue("blank", ""),
        ]
        self.ns["_linear_graphql"] = Mock(return_value={"issues": {"nodes": self.rows}})

    def call(self, request):
        return asyncio.run(self.ns["handle_api_linear_tesdiq"](request))

    def test_rufat_sees_own_account_without_dictionary_or_operator_filter(self):
        for query in ({"scope": "all"}, {"scope": "all", "q": "rufet"}):
            with self.subTest(query=query):
                result = self.call(Request(RUFAT, query=query))
                self.assertEqual(result["status"], 200)
                self.assertTrue(result["data"]["connected"])
                self.assertEqual([row["id"] for row in result["data"]["issues"]], ["own-other-operator", "own-done", "other-own-operator"])
        self.ns["list_linear_accounts"].assert_not_called()

    def test_huseyn_and_admin_keep_their_account_scope(self):
        result = self.call(Request(HUSEYN, query={"scope": "all"}))
        self.assertEqual([row["id"] for row in result["data"]["issues"]], ["other-own-operator"])
        result = self.call(Request(ADMIN, query={"scope": "all"}))
        self.assertEqual(len(result["data"]["issues"]), len(self.rows))
        self.assertEqual(self.ns["_linear_account_scope"](999), set())

    def test_rufat_moved_telegram_account_keeps_visibility_and_creation(self):
        result = self.call(Request(MOVED_RUFAT, query={"scope": "all"}))
        self.assertEqual([row["id"] for row in result["data"]["issues"]], ["own-other-operator", "own-done", "other-own-operator"])
        self.assertTrue(self.ns["_linear_can_create_for_chat"](MOVED_RUFAT))
        self.assertEqual(self.ns["_linear_create_identity_for_chat"](MOVED_RUFAT), ("rufet", "rufet@beinsystems"))
        self.ns["_load_employee_access_records"] = lambda: {}
        self.assertEqual(self.ns["_linear_account_scope"](MOVED_RUFAT), set())
        self.assertFalse(self.ns["_linear_can_create_for_chat"](MOVED_RUFAT))

    def test_rufat_and_huseyn_can_create_without_edit_permission(self):
        create = Mock(return_value={"id": "new"})
        self.ns["_linear_create_issue"] = create
        for chat_id, account, operator in ((RUFAT, "rufet", "rufet@beinsystems"), (MOVED_RUFAT, "rufet", "rufet@beinsystems"), (HUSEYN, "huseyn", "huseyn@beinsystems")):
            with self.subTest(chat_id=chat_id):
                result = self.call(Request(chat_id, "POST", {
                    "action": "create", "title": "Task", "description": "Text", "project_id": "project",
                    "account": "spoofed", "operator": "spoofed",
                }))
                self.assertEqual(result["status"], 200)
                self.assertEqual(create.call_args.kwargs["account"], account)
                self.assertEqual(create.call_args.kwargs["operator"], operator)
                self.assertNotIn("linear_edit", self.permissions[chat_id])

    def test_creation_does_not_open_editing_or_disabled_page(self):
        create = Mock()
        self.ns["_linear_create_issue"] = create
        for chat_id in (RUFAT, MOVED_RUFAT, HUSEYN):
            for action in ("edit", "delete", "status", "priority"):
                result = self.call(Request(chat_id, "POST", {"action": action, "issue_id": "issue"}))
                self.assertEqual(result["status"], 403)
            self.permissions[chat_id] = set()
            self.assertFalse(self.ns["_linear_can_create_for_chat"](chat_id))
            self.assertEqual(self.call(Request(chat_id, "POST", {"action": "create"}))["status"], 403)
        self.permissions[RASIM] = {"linear"}
        self.assertEqual(self.call(Request(RASIM, "POST", {"action": "create"}))["status"], 403)
        create.assert_not_called()

    def test_operator_scope_for_huseyn(self):
        matches = self.ns["_linear_issue_matches_scope"]
        self.assertTrue(matches(issue("a", "other", "huseyn@beinsystems"), {"huseyn"}, "huseyn@beinsystems"))
        self.assertFalse(matches(issue("a", "other", "huseyn@beinsystems.fake"), {"huseyn"}, "huseyn@beinsystems"))

    def test_staff_legacy_edit_permission_cannot_change_status(self):
        self.permissions[RUFAT].add("linear_edit")
        for action in ("status", "priority", "confirm", "discussion", "cancel", "delete", "edit"):
            self.assertEqual(self.call(Request(RUFAT, "POST", {"action": action, "issue_id": "a"}))["status"], 403)

    def test_done_buttons_are_scoped_and_require_done(self):
        self.ns["_linear_resolve_named_state_id"] = Mock(return_value="accept")
        move = self.ns["_linear_move_any_issue"] = Mock(return_value={"id": "a"})
        for account, operator in (("rufet", "other"), ("other", "rufet@beinsystems")):
            self.ns["_linear_issue_context"] = Mock(return_value=issue("a", account, operator, "Done"))
            self.assertEqual(self.call(Request(RUFAT, "POST", {"action": "test_accept", "issue_id": "a"}))["status"], 200)
        move.reset_mock()
        self.ns["_linear_issue_context"] = Mock(return_value=issue("a", "huseyn", "other", "Done"))
        self.assertEqual(self.call(Request(RUFAT, "POST", {"action": "test_accept", "issue_id": "a"}))["status"], 403)
        self.ns["_linear_issue_context"] = Mock(return_value=issue("a", "rufet", state="Triage"))
        self.assertNotEqual(self.call(Request(RUFAT, "POST", {"action": "test_accept", "issue_id": "a"}))["status"], 200)
        move.assert_not_called()

    def test_creation_rewrite_needs_no_issue_and_respects_ai_access(self):
        rewrite = self.ns["_linear_ai_rewrite"] = Mock(return_value="Edited text")
        self.ns["can_use_deal_ai"] = lambda chat: True
        for chat in (ADMIN, MOVED_RUFAT, HUSEYN):
            response = self.call(Request(chat, "POST", {"action": "ai_rewrite", "text": "Draft"}))
            self.assertEqual(response["status"], 200)
            self.assertEqual(response["data"]["text"], "Edited text")
        rewrite.reset_mock()
        self.ns["can_use_deal_ai"] = lambda chat: False
        self.ns["_deal_ai_access_denied"] = lambda: {"status": 403}
        self.assertEqual(self.call(Request(MOVED_RUFAT, "POST", {"action": "ai_rewrite", "text": "Draft"}))["status"], 403)
        self.permissions[RASIM] = {"linear"}
        self.assertEqual(self.call(Request(RASIM, "POST", {"action": "ai_rewrite", "text": "Draft"}))["status"], 403)
        rewrite.assert_not_called()

    def test_session_advertises_creation_separately_from_editing(self):
        self.ns["employee_access_profile"] = lambda chat_id: {"active": True, "role": "Əməkdaş", "permissions": list(self.permissions[chat_id])}
        self.ns["get_employee_whatsapp_number"] = lambda chat_id: ""
        for chat_id, allowed in ((RUFAT, True), (MOVED_RUFAT, True), (HUSEYN, True), (RASIM, True), (ADMIN, True)):
            result = asyncio.run(self.ns["handle_api_session"](Request(chat_id)))
            self.assertEqual(result["data"]["linear_can_create"], allowed)
            if chat_id in (RUFAT, MOVED_RUFAT, HUSEYN):
                self.assertNotIn("linear_edit", result["data"]["permissions"])

    def test_rasim_creation_and_explicit_permission_revocation(self):
        create = self.ns["_linear_create_issue"] = Mock(return_value={"id": "new"})
        result = self.call(Request(RASIM, "POST", {"action": "create", "title": "Task", "description": "Text", "project_id": "p"}))
        self.assertEqual(result["status"], 200)
        self.assertEqual(create.call_args.kwargs["account"], "rasim")
        self.assertEqual(create.call_args.kwargs["operator"], "rasim@beinsystems")
        self.permissions[RASIM] = {"linear", "linear_edit"}
        self.assertFalse(self.ns["_linear_can_create_for_chat"](RASIM))
        self.assertEqual(self.call(Request(RASIM, "POST", {"action": "create"}))["status"], 403)

    def test_saved_create_switch_is_not_restored_by_migration(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "employee_access_profile")
        record = {"role": "Əməkdaş", "active": True, "permissions": ["linear"]}
        ns = dict(self.ns, _load_employee_access_records=lambda: {str(RASIM): record},
                  _save_employee_access_records=Mock(),
                  _RETIRED_EMPLOYEE_CHAT_IDS=set(), _normalize_employee_permissions=lambda value, role: list(value),
                  _normalize_hot_order_skills=lambda value: [], _EMPLOYEE_PERMISSIONS=("linear", "linear_create"))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<profile>", "exec"), ns)
        self.assertIn("linear_create", ns["employee_access_profile"](RASIM)["permissions"])
        record["linear_create_permission_version"] = 1
        self.assertNotIn("linear_create", ns["employee_access_profile"](RASIM)["permissions"])


if __name__ == "__main__":
    unittest.main()
