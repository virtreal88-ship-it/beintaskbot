"""Unit tests for legacy deal note actions."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from legacy_action_deals import DealNoteContext, handle_deal_note


class DealNoteActionTests(unittest.TestCase):
    def setUp(self):
        self.allowed = Mock(return_value=True)
        self.add_note = Mock(return_value={"_embedded": {"notes": [{"id": 81}]}})
        self.update_note = Mock(return_value=(True, ""))
        self.delete_note = Mock(return_value=(True, ""))
        self.invalidate = Mock()
        self.pulse = Mock()
        self.context = DealNoteContext(
            lead_allowed_for_chat=self.allowed,
            add_note=self.add_note,
            update_note=self.update_note,
            delete_note=self.delete_note,
            note_entity_kind=lambda value: "leads" if value == "leads" else "contacts",
            first_note_id=lambda value: value["_embedded"]["notes"][0]["id"],
            invalidate_overview=self.invalidate,
            record_pulse=self.pulse,
        )

    def test_add_note_preserves_response_and_side_effects(self):
        result = handle_deal_note(
            "deal_add_note", {"lead_id": 7, "text": "  qeyd  "}, 20, self.context
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload["note_id"], 81)
        self.add_note.assert_called_once_with(7, "qeyd", "leads")
        self.invalidate.assert_called_once()
        self.pulse.assert_called_once_with(7, "deal_add_note")

    def test_edit_contact_note_keeps_explicit_entity(self):
        result = handle_deal_note(
            "deal_edit_note",
            {
                "lead_id": 7,
                "note_id": 9,
                "text": "yenilənib",
                "entity_type": "contacts",
                "entity_id": 11,
            },
            20,
            self.context,
        )
        self.assertTrue(result.payload["success"])
        self.update_note.assert_called_once_with(9, "yenilənib", "contacts", 11)

    def test_invalid_or_foreign_note_never_mutates(self):
        self.allowed.return_value = False
        result = handle_deal_note(
            "deal_delete_note", {"lead_id": 7, "note_id": 9}, 20, self.context
        )
        self.assertEqual(result.status, 400)
        self.delete_note.assert_not_called()

    def test_provider_failure_returns_original_error_contract(self):
        self.delete_note.return_value = (False, "provider error")
        result = handle_deal_note(
            "deal_delete_note", {"lead_id": 7, "note_id": 9}, 20, self.context
        )
        self.assertEqual(result.status, 400)
        self.assertEqual(result.payload["error"], "provider error")
        self.invalidate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
