"""Contact/deal CRUD contracts without a live Kommo account."""

import ast
import logging
import re
import unittest
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


class ContactServiceTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.service = load_module(
            "legacy_kommo_contacts.py",
            {"logging": logging, "re": re, "KOMMO_BASE_URL": "https://account.kommo.test"},
            {"legacy_runtime_config"},
        )
        self.service.configure_contact_service(http=self.http, headers={"Authorization": "Bearer hidden"})

    def test_phone_search_normalizes_azerbaijan_variants_and_deduplicates(self):
        contact = {
            "id": 7,
            "custom_fields_values": [{
                "field_code": "PHONE", "values": [{"value": "+994 55 123 45 67"}],
            }],
        }
        response = Mock(status_code=200)
        response.json.return_value = {"_embedded": {"contacts": [contact]}}
        self.http.get.return_value = response
        self.assertEqual(self.service.search_contact_by_phone("0551234567"), [contact])
        self.assertGreaterEqual(self.http.get.call_count, 2)
        for call in self.http.get.call_args_list:
            self.assertEqual(call.args[0], "https://account.kommo.test/api/v4/contacts")
            self.assertEqual(call.kwargs["timeout"], 8)

    def test_name_search_and_contact_crud_keep_payload_contract(self):
        response = Mock(status_code=200)
        response.json.return_value = {"_embedded": {"contacts": [{"id": 8, "name": "Client"}]}}
        self.http.get.return_value = response
        self.assertEqual(self.service.search_contacts_by_query("Client")[0]["id"], 8)
        self.http.patch.return_value = Mock(status_code=200, json=lambda: {"id": 8})
        self.assertEqual(self.service.update_contact_kommo(8, {"name": "New"}), {"id": 8})
        self.http.post.return_value = Mock(status_code=201, json=lambda: {"_embedded": {"contacts": [{"id": 9}]}})
        created = self.service.create_contact_kommo("Client", "+994551234567")
        self.assertEqual(created["_embedded"]["contacts"][0]["id"], 9)
        payload = self.http.post.call_args.kwargs["json"][0]
        self.assertEqual(payload["responsible_user_id"], 10932455)
        self.assertEqual(payload["custom_fields_values"][0]["field_code"], "PHONE")


class DealServiceTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.stage = Mock()
        self.moved = Mock()
        self.service = load_module(
            "legacy_kommo_deals.py",
            {
                "logging": logging,
                "KOMMO_BASE_URL": "https://account.kommo.test",
                "PIPELINE_ID": 1,
                "_lead_pipeline_id": lambda lead: int((lead or {}).get("pipeline_id") or 0),
            },
            {"legacy_kommo_funnels", "legacy_runtime_config"},
        )
        self.service.configure_deal_service(
            http=self.http,
            headers={"Authorization": "Bearer hidden"},
            employee_funnel_user=lambda pipeline: 10932455 if pipeline == 20 else None,
            pipeline_for_chat=lambda chat: 30 if chat == 5 else 1,
            on_stage_change=self.stage,
            on_pipeline_move=self.moved,
        )

    def test_update_preserves_webhook_and_task_routing_side_effects(self):
        old = Mock(status_code=200, json=lambda: {"id": 7, "pipeline_id": 10})
        changed = Mock(status_code=200, json=lambda: {"id": 7, "pipeline_id": 20, "status_id": 40})
        self.http.get.return_value = old
        self.http.patch.return_value = changed
        result = self.service.update_lead_kommo(7, {"pipeline_id": 20, "status_id": 40})
        self.assertEqual(result["pipeline_id"], 20)
        self.stage.assert_called_once_with(7)
        self.moved.assert_called_once_with(7, 20)

    def test_non_employee_pipeline_does_not_fetch_or_reassign(self):
        self.http.patch.return_value = Mock(status_code=200, json=lambda: {"id": 7})
        self.service.update_lead_kommo(7, {"pipeline_id": 99})
        self.http.get.assert_not_called()
        self.moved.assert_not_called()

    def test_create_and_status_filter_keep_existing_shapes(self):
        self.http.post.return_value = Mock(
            status_code=201,
            json=lambda: {"_embedded": {"leads": [{"id": 77}]}},
        )
        lead_id = self.service.create_lead_for_contact(8, "Client", pipeline_id=20, tags=[{"id": 3}])
        self.assertEqual(lead_id, 77)
        payload = self.http.post.call_args.kwargs["json"][0]
        self.assertEqual(payload["_embedded"]["contacts"], [{"id": 8}])
        self.assertEqual(payload["_embedded"]["tags"], [{"id": 3}])

        self.http.get.return_value = Mock(
            status_code=200,
            json=lambda: {"_embedded": {"leads": [{"id": 77}]}},
        )
        self.assertEqual(self.service.get_leads_by_status(40, chat_id=5), [{"id": 77}])
        self.assertEqual(self.http.get.call_args.kwargs["params"]["filter[statuses][0][pipeline_id]"], 30)


class EntityArchitectureTests(unittest.TestCase):
    def test_bot_only_composes_extracted_entity_functions(self):
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        moved = {
            "search_contact_by_phone", "search_contacts_by_query", "get_contact_details",
            "update_contact_kommo", "create_contact_kommo", "get_lead_details",
            "update_lead_kommo", "create_lead_for_contact", "get_leads_by_status",
            "get_contact_notes", "get_entity_tasks", "get_tasks",
            "get_all_incomplete_tasks", "create_task", "add_note", "delete_note",
            "update_note", "update_task_kommo", "get_kommo_task",
        }
        self.assertFalse(moved & definitions)
        source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn("configure_contact_service(", source)
        self.assertIn("configure_deal_service(", source)

    def test_modules_are_bounded_and_do_not_import_bot(self):
        for filename in (
            "legacy_kommo_contacts.py", "legacy_kommo_deals.py",
            "legacy_kommo_tasks.py", "legacy_kommo_notes.py",
        ):
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            with self.subTest(filename=filename):
                self.assertLessEqual(len(source.splitlines()), 250)
                self.assertNotIn("import bot", source)


if __name__ == "__main__":
    unittest.main()
