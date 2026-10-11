"""Note formatting and reader contracts without a live Kommo account."""

import ast
import logging
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(filename: str, injected: dict, excluded: set[str]):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.module not in excluded
    ]
    namespace = {"__name__": filename.removesuffix(".py") + "_test", **injected}
    exec(compile(tree, filename, "exec"), namespace)
    return SimpleNamespace(**namespace)


class NoteFormatterTests(unittest.TestCase):
    def setUp(self):
        self.formatter = load_module(
            "legacy_kommo_note_formatter.py",
            {
                "datetime": datetime,
                "BAKU_TZ": timezone.utc,
                "_extract_file_uuid": lambda value: str(value.get("file_uuid") or ""),
                "_extract_media_url": lambda value: str(value.get("url") or ""),
                "_extract_nested_text": lambda value: str(value.get("text") or ""),
                "_is_media_notice_text": lambda value: "медиа" in str(value).lower(),
                "_looks_audio_name": lambda value: str(value).lower().endswith(".ogg"),
                "_looks_image_name": lambda value: str(value).lower().endswith(".jpg"),
                "_note_channel_key": lambda note: "whatsapp",
                "_note_is_incoming": lambda note_type: str(note_type).endswith("_in"),
            },
            {
                "legacy_kommo_chat_channels",
                "legacy_kommo_media_parser",
                "legacy_runtime_config",
            },
        )
        self.formatter.configure_note_formatter(
            users={7: "Rüfət"},
            first_msgid=lambda params, note: params.get("msgid", ""),
        )

    def test_call_and_attachment_are_normalized(self):
        missed = self.formatter._format_deal_note({
            "id": 1,
            "note_type": "call_in",
            "created_at": 1_700_000_000,
            "created_by": 7,
            "params": {"phone": "+99450", "duration": 0},
        })
        self.assertEqual(missed["message_type"], "call_missed")
        self.assertEqual(missed["text"], "Buraxılmış zəng")
        self.assertEqual(missed["author"], "Rüfət")
        self.assertTrue(missed["incoming"])

        voice = self.formatter._format_deal_note({
            "note_type": "attachment",
            "params": {"file_name": "voice.ogg", "url": "https://drive.kommo.test/a"},
        })
        self.assertEqual(voice["message_type"], "audio")
        self.assertTrue(voice["is_chat"])

    def test_empty_note_is_not_added_to_timeline(self):
        self.assertIsNone(self.formatter._format_deal_note({"note_type": "common", "params": {}}))


class NoteReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = load_module(
            "legacy_kommo_note_reader.py",
            {"logging": logging, "KOMMO_BASE_URL": "https://account.kommo.test"},
            {"legacy_runtime_config"},
        )
        self.http = Mock()
        self.reader.configure_note_reader(
            http=self.http,
            headers={"Authorization": "Bearer hidden"},
            formatter=lambda note, entity_type: {"id": note["id"], "entity_type": entity_type},
            lead_contact_ids=lambda lead: [11],
        )
        self.reader.clear_entity_notes_cache()

    def test_formatted_reads_are_cached(self):
        response = Mock(status_code=200)
        response.json.return_value = {"_embedded": {"notes": [{"id": 5}]}}
        self.http.get.return_value = response
        first = self.reader._fetch_entity_notes("leads", 7)
        second = self.reader._fetch_entity_notes("leads", 7)
        self.assertEqual(first, [{"id": 5, "entity_type": "leads"}])
        self.assertEqual(second, first)
        self.http.get.assert_called_once()

    def test_raw_note_search_checks_lead_then_contacts(self):
        lead_response = Mock(status_code=200)
        lead_response.json.return_value = {"_embedded": {"notes": [{"id": 3}]}}
        contact_response = Mock(status_code=200)
        contact_response.json.return_value = {"_embedded": {"notes": [{"id": 9}]}}
        self.http.get.side_effect = [lead_response, contact_response]
        found = self.reader._find_raw_note(9, lead={"id": 7})
        self.assertEqual(found["id"], 9)
        self.assertIn("/contacts/11/notes", self.http.get.call_args.args[0])


class NoteHistoryArchitectureTests(unittest.TestCase):
    def test_modules_are_bounded_and_moved_out_of_bot(self):
        for filename in ("legacy_kommo_note_formatter.py", "legacy_kommo_note_reader.py"):
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
            "_deal_fmt_ts", "_call_is_missed", "_format_deal_note",
            "_fetch_raw_notes", "_find_raw_note", "_fetch_entity_notes",
        }
        self.assertFalse(moved & definitions)


if __name__ == "__main__":
    unittest.main()
