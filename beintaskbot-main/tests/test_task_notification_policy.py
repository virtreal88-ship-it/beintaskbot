"""Regression checks without starting the bot or sending real notifications."""
import ast
import asyncio
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig"))
        names = {"task_assignee_is_self", "_notify_linear_status_change", "_notify_linear_status_transitions"}
        cls.code = compile(ast.Module(body=[n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names], type_ignores=[]), "<policy>", "exec")

    def setUp(self):
        self.rows = [{"chat_id": 1, "name": "Admin", "active": True}, {"chat_id": 99, "name": "Rüfət Həsənzadə", "active": True}, {"chat_id": 3, "name": "Hüseyn", "active": True}, {"chat_id": 4, "name": "Former", "active": False}]
        self.push = Mock()
        self.telegram = AsyncMock()
        self.ns = {"asyncio": asyncio, "ADMIN_CHAT_ID": 1, "RUFAT_CHAT_ID": 2,
            "normalize_assignee_name": lambda name: str(name).strip(),
            "_employee_directory_rows": lambda: self.rows,
            "get_chat_id_by_name": lambda name: {"Rüfət": 2, "Hüseyn": 3}.get(name),
            "is_rufat_chat": lambda cid: cid in {2, 99},
            "_linear_description_metadata": lambda text: ({"client": "rufet"}, text),
            "_linear_client_hint": lambda text: "rufet", "logger": Mock(),
            "send_push_notification": self.push,
            "_bot_app": types.SimpleNamespace(bot=types.SimpleNamespace(send_message=self.telegram)),
            "_LINEAR_LAST_NOTIFIED_STATES": {}}
        exec(self.code, self.ns)

    def test_self_assignment_current_id_and_alias(self):
        check = self.ns["task_assignee_is_self"]
        self.assertTrue(check(99, "Rüfət Həsənzadə"))
        self.assertTrue(check(99, "Rüfət"))
        self.assertTrue(check(3, "Hüseyn"))
        self.assertFalse(check(99, "Hüseyn"))
        self.assertFalse(check(99, ""))

    def test_done_broadcast_and_other_states_admin_only(self):
        for state, expected in (("Done", {1, 99, 3}), ("Triage", {1}), ("Testiq", {1})):
            self.push.reset_mock()
            self.telegram.reset_mock()
            asyncio.run(self.ns["_notify_linear_status_change"]({"id": "BS-1", "url": "https://linear.app/issue/1"}, {"name": state}))
            self.assertEqual({int(c.args[0]) for c in self.push.call_args_list}, expected)
            self.assertEqual({c.kwargs["chat_id"] for c in self.telegram.call_args_list}, expected)
            self.assertEqual(self.push.call_count, len(expected))

    def test_transitions_do_not_duplicate_and_track_nonimportant_states(self):
        notice = self.ns["_notify_linear_status_change"] = AsyncMock()
        for state in ("Todo", "Done", "Done", "In progress", "Done"):
            asyncio.run(self.ns["_notify_linear_status_transitions"]([{"source_id": "id", "status": {"name": state}}]))
        self.assertEqual(notice.await_count, 2)

    def test_push_failure_does_not_block_telegram_or_other_staff(self):
        self.push.side_effect = RuntimeError("push failure")
        asyncio.run(self.ns["_notify_linear_status_change"]({"id": "1"}, {"name": "Done"}))
        self.assertEqual(self.telegram.await_count, 3)


if __name__ == "__main__":
    unittest.main()
