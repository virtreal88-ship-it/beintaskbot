import json
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from tenant_onboarding_api import handlers, safe_patch, fallback, SYSTEM_PROMPT


class StoreError(Exception):
    pass


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.member = Mock(return_value={"tenant_id": "company", "telegram_id": 42,
                                       "onboarding": {"team": ["employee"]}})
        self.ask = Mock(return_value='{"reply":"Next?","patch":{"industry":"shop","token":"hidden"}}')
        self.update = Mock(return_value={"id": "company"})
        self.finish = Mock(return_value={"id": "company", "status": "active"})
        self.logger = Mock()
        self.assistant, self.complete = handlers(self.member, self.ask, self.update,
            self.finish, StoreError, self.logger)
        self.request = Mock()
        self.request.json = AsyncMock(return_value={"message": "Shop"})

    def payload(self, response):
        return json.loads(response.text)

    async def test_owner_guard_before_parsing_or_side_effects(self):
        self.member.return_value = None
        for handler in (self.assistant, self.complete):
            self.assertEqual((await handler(self.request)).status, 403)
            self.member.assert_called_with(self.request, owner_only=True)
        self.request.json.assert_not_called()
        self.ask.assert_not_called()
        self.update.assert_not_called()
        self.finish.assert_not_called()

    async def test_empty_and_invalid_json(self):
        for data in ({}, {"message": "   "}, None):
            self.request.json.return_value = data
            self.assertEqual((await self.assistant(self.request)).status, 400)
        self.request.json.side_effect = ValueError("invalid")
        self.assertEqual((await self.assistant(self.request)).status, 400)
        self.ask.assert_not_called()

    async def test_provider_and_store_contract(self):
        result = self.payload(await self.assistant(self.request))
        self.assertEqual(result, {"success": True, "reply": "Next?",
            "patch": {"industry": "shop"}, "tenant": {"id": "company"}})
        self.update.assert_called_once_with(tenant_id="company", owner_id=42, patch={"industry": "shop"})
        system, prompt = self.ask.call_args.args
        self.assertEqual(system, SYSTEM_PROMPT)
        self.assertIn('"team": ["employee"]', prompt)
        self.assertTrue(prompt.endswith("Shop"))

    async def test_failed_provider_falls_back_without_leaking_exception(self):
        self.ask.side_effect = RuntimeError("private token and client text")
        result = self.payload(await self.assistant(self.request))
        self.assertEqual(result["patch"], {"notes": ["Shop"]})
        self.assertIn("haradan", result["reply"])
        self.assertNotIn("private", str(self.logger.mock_calls))

    async def test_invalid_response_uses_fallback_and_limits(self):
        for raw in ("not json", "{broken}", '{"patch":{"secret":"x"}}'):
            self.ask.return_value = raw
            self.assertEqual(self.payload(await self.assistant(self.request))["patch"], {"notes": ["Shop"]})
        self.request.json.return_value = {"message": "x" * 3000}
        self.ask.return_value = json.dumps({"reply": "y" * 1500, "patch": {}})
        result = self.payload(await self.assistant(self.request))
        self.assertEqual(len(result["reply"]), 1200)
        self.assertEqual(len(result["patch"]["notes"][0]), 2500)

    async def test_store_errors_and_finish_contract(self):
        self.update.side_effect = StoreError("not allowed")
        self.assertEqual((await self.assistant(self.request)).status, 400)
        result = self.payload(await self.complete(self.request))
        self.assertEqual(result["tenant"]["status"], "active")
        self.finish.assert_called_once_with(tenant_id="company", owner_id=42)
        self.finish.side_effect = StoreError("connect first")
        self.assertEqual(self.payload(await self.complete(self.request)),
            {"success": False, "error": "connect first"})

    def test_patch_allowlist_and_fallback_stages(self):
        self.assertEqual(safe_patch(None), {})
        self.assertEqual(safe_patch({"company_name": "test", "role": "owner"}), {"company_name": "test"})
        self.assertIn("Komandada", fallback("", {})[0])
        self.assertEqual(fallback("", {})[1], {"notes": []})
        self.assertIn("haradan", fallback("text", {"team": [1]})[0])
        self.assertIn("daha tez", fallback("text", {"team": [1], "sources": [1]})[0])

    def test_bot_routes_and_provider_adapter_are_preserved(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        factories = [node for node in calls if isinstance(node.func, ast.Name)
                     and node.func.id == "tenant_onboarding_handlers"]
        self.assertEqual(len(factories), 1)
        self.assertEqual([arg.id for arg in factories[0].args], [
            "_tenant_member_from_request", "_ask_tenant_onboarding", "update_onboarding",
            "finalize_onboarding", "TenantPlatformError", "logger"])
        routes = {}
        for node in calls:
            if isinstance(node.func, ast.Attribute) and node.func.attr == "add_post" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    routes[node.args[0].value] = ast.unparse(node.args[1])
        self.assertEqual(routes["/api/platform/onboarding/assistant"], "onboarding_assistant")
        self.assertEqual(routes["/api/platform/onboarding/finish"], "onboarding_complete")
        adapter = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "_ask_tenant_onboarding")
        create = next(node for node in ast.walk(adapter) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute) and node.func.attr == "create")
        options = {item.arg: ast.unparse(item.value) for item in create.keywords}
        self.assertEqual(options["model"], "LLM_MODEL")
        self.assertEqual(options["temperature"], "0.25")
        self.assertEqual(options["max_tokens"], "500")
