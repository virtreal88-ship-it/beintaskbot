"""Task and note service contracts without a live Kommo account."""

import ast
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(filename: str):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.module != "legacy_runtime_config"
    ]
    namespace = {
        "__name__": filename.removesuffix(".py") + "_test",
        "logging": logging,
        "KOMMO_BASE_URL": "https://account.kommo.test",
    }
    exec(compile(tree, filename, "exec"), namespace)
    return SimpleNamespace(**namespace)


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.started = Mock()
        self.created = Mock()
        self.updated = Mock()
        self.completed = Mock()
        self.service = load_module("legacy_kommo_tasks.py")
        self.service.configure_task_service(
            http=self.http,
            headers={"Authorization": "Bearer hidden"},
            on_create_started=self.started,
            on_created=self.created,
            on_updated=self.updated,
            on_completed=self.completed,
        )

    def test_create_preserves_payload_and_side_effect_hooks(self):
        result = {"_embedded": {"tasks": [{"id": 42}]}}
        self.http.post.return_value = Mock(status_code=201, json=lambda: result)
        self.assertEqual(
            self.service.create_task(7, "Call", 2_000_000_000, entity_type="leads", creator_name="Rüfət"),
            result,
        )
        payload = self.http.post.call_args.kwargs["json"][0]
        self.assertEqual(payload["responsible_user_id"], 10932455)
        self.assertEqual(payload["task_type_id"], 1)
        self.started.assert_called_once_with(7)
        self.created.assert_called_once_with(result, "Rüfət")

    def test_complete_updates_dedup_and_urgent_registries(self):
        self.http.patch.return_value = Mock(status_code=200, json=lambda: {"id": 42})
        self.assertEqual(self.service.update_task_kommo(42, {"is_completed": True}), {"id": 42})
        self.updated.assert_called_once_with(42)
        self.completed.assert_called_once_with(42)

    def test_single_task_read_keeps_collection_fallback(self):
        missing = Mock(status_code=404)
        found = Mock(status_code=200, json=lambda: {"_embedded": {"tasks": [{"id": 42}]}})
        self.http.get.side_effect = [missing, found]
        self.assertEqual(self.service.get_kommo_task("42"), {"id": 42})
        self.assertEqual(self.http.get.call_args.kwargs["params"]["filter[id][]"], 42)


class NoteServiceTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.deleted = Mock()
        self.service = load_module("legacy_kommo_notes.py")
        self.service.configure_note_service(
            http=self.http,
            headers={"Authorization": "Bearer hidden"},
            on_deleted=self.deleted,
        )

    def test_add_note_preserves_common_note_payload(self):
        result = {"_embedded": {"notes": [{"id": 9}]}}
        self.http.post.return_value = Mock(status_code=201, json=lambda: result)
        self.assertEqual(self.service.add_note(7, "Text", "leads"), result)
        self.assertEqual(
            self.http.post.call_args.kwargs["json"],
            [{"note_type": "common", "params": {"text": "Text"}}],
        )

    def test_delete_uses_existing_blank_update_contract_and_clears_cache(self):
        self.http.patch.return_value = Mock(status_code=200)
        self.assertEqual(self.service.delete_note(9, "contacts", 7), (True, ""))
        payload = self.http.patch.call_args.kwargs["json"]
        self.assertEqual(payload[0]["params"]["text"], " ")
        self.deleted.assert_called_once_with(9)


if __name__ == "__main__":
    unittest.main()
