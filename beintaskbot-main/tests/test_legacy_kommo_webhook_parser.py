"""Legacy Kommo webhook parsing contracts."""

import ast
import unittest
from pathlib import Path

import legacy_kommo_webhook_parser as parser


ROOT = Path(__file__).resolve().parents[1]


class WebhookParserTests(unittest.TestCase):
    def setUp(self):
        parser.configure_webhook_parser(media_notice=lambda text: "медиа" in text.casefold())
        parser.clear_webhook_dedup()

    def test_json_and_form_message_payloads_have_same_rows(self):
        json_rows = parser._kommo_form_message_rows({
            "message": {"add": [{"id": "m1", "text": "Salam"}]},
        })
        form_rows = parser._kommo_form_message_rows({
            "message[add][0][id]": "m1",
            "message[add][0][text]": "Salam",
        })
        self.assertEqual(json_rows, form_rows)

    def test_preview_normalizes_text_and_media(self):
        self.assertEqual(parser._incoming_message_preview("  Salam\n dünya  ", "text"), "Salam dünya")
        self.assertEqual(parser._incoming_message_preview("вам пришло медиа", "text"), "📷 Şəkil")
        self.assertEqual(parser._incoming_message_preview("", "voice"), "Səs mesajı")

    def test_retry_is_seen_only_after_first_message(self):
        self.assertFalse(parser._incoming_message_seen("external-1"))
        self.assertTrue(parser._incoming_message_seen("external-1"))
        self.assertFalse(parser._incoming_message_seen(""))

    def test_lead_events_preserve_index_and_old_pipeline(self):
        events = parser._lead_webhook_events({
            "leads[status][2][id]": "17",
            "leads[status][2][pipeline_id]": "5",
            "leads[status][2][old_pipeline_id]": "4",
            "leads[status][2][status_id]": "9",
            "leads[add][0][id]": "18",
        })
        self.assertEqual(events[0]["lead_id"], 17)
        self.assertEqual(events[0]["old_pipeline_id"], 4)
        self.assertEqual(events[1]["type"], "add")


class WebhookParserArchitectureTests(unittest.TestCase):
    def test_module_is_bounded_and_parser_moved(self):
        source = (ROOT / "legacy_kommo_webhook_parser.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertFalse({"_kommo_form_message_rows", "_incoming_message_preview", "_incoming_message_seen"} & definitions)


if __name__ == "__main__":
    unittest.main()
