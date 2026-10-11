"""Kommo history media recovery contracts."""

import ast
import unittest
from pathlib import Path

import legacy_kommo_media_linker as linker


ROOT = Path(__file__).resolve().parents[1]


class MediaLinkerTests(unittest.TestCase):
    def setUp(self):
        self.notes = {
            ("leads", 7): [
                {"id": "m1", "created_at": 100, "message_type": "picture", "file_uuid": "photo-uuid", "file_name": "photo.jpg"},
                {"id": "a1", "created_at": 200, "message_type": "audio", "file_uuid": "audio-uuid", "file_name": "voice.ogg"},
            ],
        }
        linker.configure_media_linker(
            fetch_notes=lambda kind, entity_id: self.notes.get((kind, entity_id), []),
            fetch_files=lambda _kind, _entity_id: [],
            media_notice=lambda text: "медиа" in str(text).casefold(),
            looks_audio=lambda name: ".ogg" in str(name).casefold(),
            looks_image=lambda name: ".jpg" in str(name).casefold(),
        )

    def test_click_prefers_matching_media_kind(self):
        found = linker._find_click_media(7, [], 201, "audio", "")
        self.assertEqual(found["file_uuid"], "audio-uuid")

    def test_placeholder_is_linked_to_nearest_photo(self):
        chat = [{"text": "вам пришло медиа", "created_at": 102, "message_type": "picture"}]
        linker._link_missing_chat_media(chat, 7, [])
        self.assertEqual(chat[0]["file_uuid"], "photo-uuid")
        self.assertEqual(chat[0]["text"], "")

    def test_candidates_are_deduplicated(self):
        self.notes[("contacts", 8)] = [dict(self.notes[("leads", 7)][0])]
        rows = linker._deal_media_candidates(7, [8])
        self.assertEqual(len(rows), 2)


class MediaLinkerArchitectureTests(unittest.TestCase):
    def test_module_is_bounded_and_functions_moved(self):
        source = (ROOT / "legacy_kommo_media_linker.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        moved = {"_deal_media_candidates", "_find_click_media", "_link_missing_chat_media"}
        self.assertFalse(moved & definitions)


if __name__ == "__main__":
    unittest.main()
