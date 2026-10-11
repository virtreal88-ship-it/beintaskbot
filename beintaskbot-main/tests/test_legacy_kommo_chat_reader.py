"""Legacy Kommo chat reader contracts without external network calls."""

import ast
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_reader():
    tree = ast.parse((ROOT / "legacy_kommo_chat_reader.py").read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.module != "legacy_runtime_config"
    ]
    namespace = {
        "__name__": "legacy_kommo_chat_reader_test",
        "logging": logging,
        "KOMMO_BASE_URL": "https://account.kommo.test",
    }
    exec(compile(tree, "legacy_kommo_chat_reader.py", "exec"), namespace)
    return SimpleNamespace(**namespace)


def response(status: int, payload=None):
    return Mock(status_code=status, content=b"{}", json=lambda: payload or {})


class ChatReaderTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.raw_http = Mock()
        self.reader = load_reader()
        self.reader.configure_chat_reader(
            http=self.http,
            raw_http=self.raw_http,
            headers={"Authorization": "Bearer hidden"},
            token="secret",
        )

    def test_talk_reader_paginates_only_the_working_endpoint(self):
        first = [{"id": 1}, {"id": 2}]
        second = [{"id": 3}]
        self.http.get.side_effect = [
            response(404),
            response(200, {"_embedded": {"messages": first}}),
            response(200, {"messages": second}),
        ]
        rows, blocked, more = self.reader._fetch_talk_messages(7, pages=3, page_limit=2)
        self.assertEqual(rows, first + second)
        self.assertFalse(blocked)
        self.assertFalse(more)
        calls = self.http.get.call_args_list
        self.assertIn("/ajax/v4/talks/7/messages", calls[1].args[0])
        self.assertEqual(calls[2].args[0], calls[1].args[0])
        self.assertEqual(calls[2].kwargs["params"]["page"], 2)

    def test_permission_failure_stops_fallback_and_marks_reader_blocked(self):
        self.http.get.return_value = response(403)
        self.assertEqual(self.reader._fetch_talk_messages(7), ([], True, False))
        self.assertEqual(self.http.get.call_count, 1)

    def test_chat_history_falls_back_across_hosts_with_server_only_token(self):
        messages = [{"id": "m1", "type": "incoming"}]
        self.raw_http.get.side_effect = [response(404), response(200, {"items": messages})]
        self.assertEqual(self.reader._fetch_chat_history_by_chat_id("chat-1"), messages)
        first_headers = self.raw_http.get.call_args_list[0].kwargs["headers"]
        self.assertEqual(first_headers, {"Authorization": "Bearer secret"})
        self.assertNotIn("secret", self.raw_http.get.call_args_list[0].args[0])

    def test_payload_extractor_rejects_non_lists(self):
        self.assertEqual(self.reader._extract_messages_payload({"messages": {"id": 1}}), [])
        self.assertEqual(self.reader._extract_messages_payload(None), [])


class ChatReaderArchitectureTests(unittest.TestCase):
    def test_reader_is_bounded_and_bot_only_composes_it(self):
        source = (ROOT / "legacy_kommo_chat_reader.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertFalse({"_fetch_talk_messages", "_fetch_chat_history_by_chat_id"} & definitions)
        self.assertIn("configure_chat_reader(", (ROOT / "bot.py").read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
