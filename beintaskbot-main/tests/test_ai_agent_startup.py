"""AI Agent startup fails before a worker can claim queued work."""
import ast
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from tenant_ai_agent_startup import prepare_database

ROOT = Path(__file__).resolve().parents[1]


class AiAgentStartupTests(unittest.TestCase):
    def test_encryption_and_both_schemas_are_prepared_in_order(self):
        calls = []
        connection = Mock()
        manager = MagicMock()
        manager.__enter__.return_value = connection
        platform = types.ModuleType("tenant_platform_core")
        platform._fernet = lambda: calls.append("fernet")
        platform._connect = lambda: manager
        agents = types.ModuleType("tenant_ai_agents_schema")
        agents.ensure_schema = lambda conn: calls.append("agents")
        chat = types.ModuleType("tenant_chat_ai_schema")
        chat.ensure_schema = lambda conn: calls.append("chat_ai")
        with patch.dict("sys.modules", {
            "tenant_platform_core": platform,
            "tenant_ai_agents_schema": agents,
            "tenant_chat_ai_schema": chat,
        }):
            prepare_database()
        self.assertEqual(calls, ["fernet", "agents", "chat_ai"])
        manager.__enter__.assert_called_once_with()
        manager.__exit__.assert_called_once()

    def test_worker_startup_migrates_first_only_when_requested(self):
        source = (ROOT / "tenant_ai_agent_startup.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        start = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "start_worker")
        calls = [node for node in ast.walk(start) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)]
        thread_call = next(node for node in calls if node.func.attr == "to_thread")
        task_call = next(node for node in calls if node.func.attr == "create_task")
        self.assertLess(thread_call.lineno, task_call.lineno)
        self.assertTrue(any(isinstance(node, ast.Name) and node.id == "prepare_database_first"
                            for node in ast.walk(start)))
        bot = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn("prepare_database_first=STAGING_MODE", bot)


if __name__ == "__main__":
    unittest.main()
