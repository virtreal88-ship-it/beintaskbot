"""Pure Kommo message normalization contracts."""

import ast
import unittest
from pathlib import Path

import legacy_kommo_chat_message_meta as meta
import legacy_kommo_chat_normalizer as normalizer


ROOT = Path(__file__).resolve().parents[1]


class ChatNormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        meta.configure_chat_message_meta(users={7: "Nizami"})
        normalizer.configure_chat_message_normalizer(
            extract_media_url=lambda value: str((value or {}).get("url") or ""),
            absolute_media_url=lambda value: value,
            extract_file_uuid=lambda value: str((value or {}).get("file_uuid") or ""),
            generic_placeholder=lambda value: value == "placeholder",
            call_is_missed=lambda value, text: bool((value or {}).get("missed")),
            media_notice=lambda value: value == "media notice",
            looks_audio=lambda value: str(value).endswith(".ogg"),
            looks_image=lambda value: str(value).endswith(".jpg"),
            looks_profile_photo=lambda name, kind: False,
            split_quote=lambda value: ("", value),
            message_ids=lambda message, nested: (
                str(message.get("id") or ""),
                str(nested.get("msgid") or ""),
                str(message.get("external_id") or ""),
            ),
            format_timestamp=lambda value: f"date:{value}",
            origin_channel=lambda value: str(value or "").split(":", 1)[0].lower(),
            allowed_media=lambda value: str(value).startswith("https://"),
        )

    def test_incoming_is_always_client_and_keeps_real_name(self):
        row = normalizer._format_chat_message({
            "id": "m1",
            "type": "incoming",
            "created_at": 10,
            "author": {"type": "external", "name": "Chingiz"},
            "message": {"type": "text", "text": "Salam"},
        }, "instagram:profile")
        self.assertTrue(row["incoming"])
        self.assertEqual(row["direction"], "incoming")
        self.assertEqual(row["author"], "Chingiz")
        self.assertEqual(row["channel"], "instagram")

    def test_outgoing_generic_channel_author_maps_to_manager(self):
        row = normalizer._format_chat_message({
            "id": "m2",
            "type": "outgoing",
            "created_at": 20,
            "author": {"id": 7, "name": "WhatsApp manager"},
            "message": {"type": "text", "text": "Salam", "status": "seen"},
        }, "whatsapp:line")
        self.assertFalse(row["incoming"])
        self.assertEqual(row["author"], "Nizami")
        self.assertEqual(row["delivery_status"], "read")
        self.assertEqual(row["channel"], "whatsapp")

    def test_media_and_reply_context_keep_web_contract(self):
        row = normalizer._format_chat_message({
            "id": "m3",
            "type": "incoming",
            "created_at": 30,
            "author": {"type": "external"},
            "attachment": {"url": "https://cdn.test/photo.jpg", "file_name": "photo.jpg"},
            "message": {
                "type": "text",
                "text": "Cavab",
                "reply_to": {"message": {"id": "old", "text": "Əvvəlki"}},
            },
        }, "tiktok:profile")
        self.assertEqual(row["message_type"], "picture")
        self.assertEqual(row["media_url"], "https://cdn.test/photo.jpg")
        self.assertEqual(row["reply_to_message_id"], "old")
        self.assertEqual(row["reply_to_text"], "Əvvəlki")
        self.assertEqual(row["channel"], "tiktok")

    def test_ai_transport_notice_and_placeholder_are_hidden(self):
        hidden = normalizer._format_chat_message({
            "type": "incoming", "text": "AI agents have been stopped",
        })
        placeholder = normalizer._format_chat_message({
            "type": "incoming", "text": "placeholder",
        })
        self.assertIsNone(hidden)
        self.assertIsNone(placeholder)

    def test_delivery_status_contract(self):
        self.assertEqual(meta._chat_delivery_status({}, {"status": "failed"}, False), "error")
        self.assertEqual(meta._chat_delivery_status({}, {"delivered": True}, False), "delivered")
        self.assertEqual(meta._chat_delivery_status({}, {"status": "seen"}, False), "read")
        self.assertEqual(meta._chat_delivery_status({}, {"status": "seen"}, True), "")


class ChatNormalizerArchitectureTests(unittest.TestCase):
    def test_modules_are_bounded_and_bot_only_composes_them(self):
        for filename in (
            "legacy_kommo_chat_message_meta.py",
            "legacy_kommo_chat_normalizer.py",
        ):
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
            "_chat_delivery_status", "_is_generic_chat_author",
            "_chat_author_name", "_format_chat_message",
        }
        self.assertFalse(moved & definitions)


if __name__ == "__main__":
    unittest.main()
