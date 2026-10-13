"""Fail-closed configuration checks for an isolated Railway staging service."""
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from tenant_ai_agent_runtime_flags import enabled


ROOT = Path(__file__).resolve().parents[1]


class RailwayStagingSafetyTests(unittest.TestCase):
    def test_railway_uses_root_launcher_and_app_has_health_route(self):
        repository = ROOT.parent
        config = (repository / "railway.toml").read_text(encoding="utf-8")
        launcher = (repository / "start.sh").read_text(encoding="utf-8")
        requirements = (repository / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn('startCommand = "bash start.sh"', config)
        self.assertIn('cd "$(dirname "$0")/beintaskbot-main"', launcher)
        self.assertIn("cryptography>=", requirements)
        self.assertIn("pypdf>=", requirements)
        source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn("app_web.router.add_get(\"/health\", health_check)", source)

    def _runtime_config(self, extra=None):
        env = os.environ.copy()
        for name in ("KOMMO_TOKEN", "GH_TOKEN", "TELEGRAM_POLLING_ENABLED", "CANONICAL_WEB_ORIGIN"):
            env.pop(name, None)
        env.update({
            "STAGING_MODE": "true",
            "TELEGRAM_TOKEN": "staging-bot-token",
            "VAPID_PRIVATE_KEY": "staging-private",
            "VAPID_PUBLIC_KEY": "staging-public",
            "CANONICAL_WEB_ORIGIN": "https://staging.example.test",
            "TELEGRAM_POLLING_ENABLED": "false",
        })
        env.update(extra or {})
        return subprocess.run(
            [sys.executable, "-c", "import legacy_runtime_config as c; print(c.STAGING_MODE, c.TELEGRAM_POLLING_ENABLED, repr(c.KOMMO_TOKEN), repr(c.KOMMO_BASE_URL), repr(c.GH_TOKEN))"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=20,
        )

    def test_staging_omits_global_production_credentials_and_can_disable_polling(self):
        result = self._runtime_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("True False '' '' ''", result.stdout)

    def test_production_defaults_keep_existing_kommo_and_telegram_bootstrap(self):
        env = os.environ.copy()
        for name in ("STAGING_MODE", "TELEGRAM_POLLING_ENABLED", "CANONICAL_WEB_ORIGIN"):
            env.pop(name, None)
        env.update({
            "TELEGRAM_TOKEN": "existing-token",
            "KOMMO_TOKEN": "existing-kommo-token",
            "GH_TOKEN": "existing-gh-token",
            "VAPID_PRIVATE_KEY": "private",
            "VAPID_PUBLIC_KEY": "public",
        })
        result = subprocess.run(
            [sys.executable, "-c", "import legacy_runtime_config as c; print(c.STAGING_MODE, c.TELEGRAM_POLLING_ENABLED, c.KOMMO_DOMAIN, bool(c.KOMMO_TOKEN), bool(c.GH_TOKEN))"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("False True "), result.stdout)
        parts = result.stdout.split()
        self.assertTrue(parts[2].endswith(".kommo.com"), result.stdout)
        self.assertEqual(parts[3:], ["True", "True"])

    def test_staging_rejects_a_global_kommo_token(self):
        result = self._runtime_config({"KOMMO_TOKEN": "accidentally-copied-token"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STAGING_MODE forbids KOMMO_TOKEN", result.stderr)

    def test_staging_rejects_the_production_web_origin(self):
        result = self._runtime_config({"CANONICAL_WEB_ORIGIN": "https://crm.pro.az"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staging-specific HTTPS CANONICAL_WEB_ORIGIN", result.stderr)
        result = self._runtime_config({"CANONICAL_WEB_ORIGIN": "http://crm.pro.az"})
        self.assertNotEqual(result.returncode, 0)

    def test_staging_forces_agent_auto_send_and_crm_actions_off(self):
        with patch.dict(os.environ, {
            "STAGING_MODE": "true",
            "AI_AGENTS_AUTO_SEND_ENABLED": "true",
            "AI_AGENTS_CRM_ENABLED": "true",
            "AI_AGENTS_ENABLED": "true",
        }):
            self.assertFalse(enabled("AI_AGENTS_AUTO_SEND_ENABLED"))
            self.assertFalse(enabled("AI_AGENTS_CRM_ENABLED"))
            self.assertTrue(enabled("AI_AGENTS_ENABLED"))

    def test_staging_legacy_storage_never_reads_or_writes_github(self):
        fake_pg = types.ModuleType("psycopg")
        fake_pg.connect = lambda *_args, **_kwargs: None
        fake_rows = types.ModuleType("psycopg.rows")
        fake_rows.dict_row = object()
        with patch.dict(os.environ, {"STAGING_MODE": "true"}), patch.dict(sys.modules, {
            "psycopg": fake_pg, "psycopg.rows": fake_rows,
        }):
            spec = importlib.util.spec_from_file_location(
                "staging_test_legacy_document_storage", ROOT / "legacy_document_storage.py")
            storage = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(storage)
            self.assertIsNone(storage._GH_REPO)
            with (patch.object(storage, "_DATABASE_URL", None),
                  patch.object(storage.requests, "get") as get,
                  patch.object(storage.requests, "put") as put):
                self.assertEqual(storage._github_load_file("balance.json", force=True), {})
                self.assertFalse(storage._save_file("balance.json"))
        get.assert_not_called()
        put.assert_not_called()

    def test_staging_does_not_register_the_legacy_kommo_webhook(self):
        import ast
        source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        register = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_register_legacy_web_routes")
        guarded = [node for node in ast.walk(register) if isinstance(node, ast.If)
                   and isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)
                   and isinstance(node.test.operand, ast.Name) and node.test.operand.id == "STAGING_MODE"]
        self.assertTrue(any(any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                                and call.func.attr == "add_post" and len(call.args) > 1
                                and isinstance(call.args[0], ast.Constant)
                                and call.args[0].value == "/webhook/kommo"
                                for call in ast.walk(node)) for node in guarded))


if __name__ == "__main__":
    unittest.main()
