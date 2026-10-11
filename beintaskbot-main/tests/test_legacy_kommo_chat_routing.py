"""Kommo channel selection and sending contracts without live requests."""

import ast
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import legacy_kommo_chat_channels as channels
import legacy_kommo_chat_resolver as resolver


ROOT = Path(__file__).resolve().parents[1]


def channel_key(value: str) -> str:
    text = str(value or "").lower()
    for key in ("tiktok", "instagram", "facebook", "telegram", "whatsapp"):
        if key in text:
            return key
    return ""


def load_sender():
    tree = ast.parse((ROOT / "legacy_kommo_chat_sender.py").read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.module != "legacy_runtime_config"
    ]
    namespace = {
        "__name__": "legacy_kommo_chat_sender_test",
        "logging": logging,
        "KOMMO_BASE_URL": "https://account.kommo.test",
    }
    exec(compile(tree, "legacy_kommo_chat_sender.py", "exec"), namespace)
    return SimpleNamespace(**namespace)


class ChannelSelectionTests(unittest.TestCase):
    def setUp(self):
        channels.configure_chat_channels(
            origin_channel_key=channel_key,
            channel_labels=channels.CHAT_CHANNEL_LABELS,
            display_number=lambda value: f"+{value}" if value else "",
        )

    def test_channels_keep_instagram_and_tiktok_separate_and_drop_waba(self):
        talks = [
            {"id": 1, "origin": "instagram", "updated_at": 10},
            {"id": 2, "origin": "tiktok", "updated_at": 20},
            {"id": 3, "origin": "whatsapp business waba", "updated_at": 40},
            {"id": 4, "origin": "whatsapp grey", "phone": "+994501112233", "updated_at": 30},
        ]
        rows = channels._channels_from_talks(talks, "994501112233")
        self.assertEqual({row["key"] for row in rows}, {"whatsapp", "instagram", "tiktok"})
        self.assertEqual(next(row for row in rows if row["key"] == "whatsapp")["talk_id"], 4)

    def test_resolver_never_cross_routes_hinted_social_talk(self):
        talks = [
            {"id": 11, "origin": "instagram", "chat_id": "ig"},
            {"id": 12, "origin": "tiktok", "chat_id": "tt", "updated_at": 2},
        ]
        resolver.configure_chat_resolver(
            fetch_talks=lambda lead_id, contact_ids: talks,
            lead_contact_ids=lambda lead: [9],
            remembered_talk_id=lambda lead_id: 11,
        )
        self.assertEqual(resolver._resolve_channel_talk({"id": 7}, "tiktok", hinted=11), (12, "tt"))
        self.assertEqual(resolver._resolve_channel_talk({"id": 7}, "instagram", hinted=11), (11, "ig"))


class ChatSenderTests(unittest.TestCase):
    def setUp(self):
        self.sender = load_sender()
        self.post = Mock()
        self.sender.configure_chat_sender(
            quote_bodies=lambda payload, reply_id, reply_text="": [
                {**payload, "reply_to": {"message": {"id": reply_id, "text": reply_text}}}
            ],
            post_json=self.post,
            error_detail=lambda response: str(getattr(response, "detail", "")),
        )

    def test_plain_text_uses_official_talk_endpoint(self):
        self.post.return_value = Mock(status_code=200, text="")
        self.assertEqual(self.sender._send_kommo_talk_text(5, "Salam"), (True, "", 200))
        self.assertEqual(
            self.post.call_args.args[0],
            "https://account.kommo.test/api/v4/talks/5/send_message",
        )
        self.assertEqual(self.post.call_args.args[1], {"text": "Salam"})

    def test_native_reply_uses_exact_chat_before_plain_fallback(self):
        self.post.return_value = Mock(status_code=201, text="")
        result = self.sender._send_kommo_talk_message(
            5, "Cavab", reply_to="old", chat_id="ig-chat", reply_text="Əvvəlki",
        )
        self.assertEqual(result, (True, "", 201))
        self.assertEqual(self.post.call_args.args[0], "https://amojo.kommo.com/v2/chats/ig-chat")
        self.assertEqual(self.post.call_args.args[1]["reply_to"]["message"]["id"], "old")

    def test_provider_failures_keep_user_facing_errors(self):
        denied = Mock(status_code=403, text="Sending to external chats", detail="")
        limited = Mock(status_code=429, text="limit", detail="limit")
        self.post.return_value = denied
        ok, error, status = self.sender._send_kommo_talk_text(5, "Salam")
        self.assertFalse(ok)
        self.assertEqual(status, 403)
        self.assertIn("Sending to external chats", error)
        self.post.return_value = limited
        self.assertEqual(self.sender._send_kommo_talk_text(5, "Salam")[2], 429)


class RoutingArchitectureTests(unittest.TestCase):
    def test_modules_are_bounded_and_bot_only_composes_them(self):
        files = (
            "legacy_kommo_chat_channels.py",
            "legacy_kommo_chat_resolver.py",
            "legacy_kommo_chat_sender.py",
        )
        for filename in files:
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            with self.subTest(filename=filename):
                self.assertLessEqual(len(source.splitlines()), 250)
                self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        moved = {
            "_talk_channel_key", "_channels_from_talks", "_resolve_channel_talk",
            "_send_kommo_talk_message", "_send_kommo_talk_text",
        }
        self.assertFalse(moved & definitions)


if __name__ == "__main__":
    unittest.main()
