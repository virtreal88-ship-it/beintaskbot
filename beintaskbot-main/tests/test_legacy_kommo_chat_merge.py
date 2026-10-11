"""Merged Kommo timeline contracts."""

import ast
import unittest
from pathlib import Path

import legacy_kommo_chat_merge as merge


ROOT = Path(__file__).resolve().parents[1]


class ChatMergeTests(unittest.TestCase):
    def setUp(self):
        merge.configure_chat_merge(split_quote=self._split_quote)

    @staticmethod
    def _split_quote(text: str) -> tuple[str, str]:
        if text.startswith(">") and "\n" in text:
            quote, body = text.split("\n", 1)
            return quote.lstrip("> "), body.strip()
        return "", text

    def test_real_message_replaces_local_stub_without_duplicate(self):
        chat = [{
            "id": "sent-1", "external_id": "wamid.1", "text": "Salam",
            "created_at": 100, "incoming": False, "delivery_status": "sent",
        }]
        seen = {merge._chat_item_key(chat[0])}
        merge._merge_chat_item(chat, seen, {
            "id": "42", "external_id": "wamid.1", "text": "Salam",
            "created_at": 102, "incoming": False, "delivery_status": "read",
            "talk_id": 9,
        })
        self.assertEqual(len(chat), 1)
        self.assertEqual(chat[0]["id"], "42")
        self.assertEqual(chat[0]["delivery_status"], "read")
        self.assertEqual(chat[0]["talk_id"], 9)

    def test_quote_prefix_does_not_create_duplicate(self):
        rows = [
            {"id": "note-1", "text": "Salam", "created_at": 100, "incoming": False},
            {"id": "8", "text": "> əvvəlki\nSalam", "created_at": 101, "incoming": False},
        ]
        clean = merge._deduplicate_chat(rows)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]["id"], "8")

    def test_opposite_directions_are_not_merged(self):
        rows = [
            {"id": "1", "text": "Salam", "created_at": 100, "incoming": True},
            {"id": "2", "text": "Salam", "created_at": 101, "incoming": False},
        ]
        self.assertEqual(len(merge._deduplicate_chat(rows)), 2)

    def test_delivery_status_has_stable_precedence(self):
        self.assertGreater(merge._delivery_rank("read"), merge._delivery_rank("delivered"))
        self.assertGreater(merge._delivery_rank("sent"), merge._delivery_rank("failed"))


class ChatMergeArchitectureTests(unittest.TestCase):
    def test_module_is_bounded_and_moved_from_bot(self):
        source = (ROOT / "legacy_kommo_chat_merge.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertFalse({"_chat_item_key", "_clean_body_text", "_delivery_rank"} & definitions)


if __name__ == "__main__":
    unittest.main()
