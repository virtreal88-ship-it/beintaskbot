"""Unit tests for editing a deal contact through the legacy webapp."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from legacy_action_contacts import DealContactContext, handle_deal_contact_edit


class DealContactActionTests(unittest.TestCase):
    def setUp(self):
        self.update_contact = Mock(return_value={"id": 9})
        self.create_contact = Mock(return_value={"id": 9})
        self.update_lead = Mock(return_value={"id": 7})
        self.patch_cache = Mock()
        self.invalidate = Mock()
        self.pulse = Mock()
        self.context = DealContactContext(
            is_funnel_chat=lambda _chat: True,
            is_admin=lambda _chat: False,
            lead_allowed_for_chat=lambda _lead, _chat: True,
            get_lead=lambda _lead: {"_embedded": {"contacts": [{"id": 9}]}},
            update_contact=self.update_contact,
            create_contact_for_lead=self.create_contact,
            update_lead=self.update_lead,
            patch_overview_contact=self.patch_cache,
            invalidate_overview=self.invalidate,
            record_pulse=self.pulse,
        )

    def test_updates_existing_linked_contact_and_cached_preview(self):
        result = handle_deal_contact_edit(
            {"lead_id": 7, "name": "Aysel", "phone": "+994501112233"},
            20,
            self.context,
        )
        self.assertTrue(result.payload["success"])
        self.update_contact.assert_called_once()
        self.create_contact.assert_not_called()
        self.update_lead.assert_called_once_with(7, {"name": "Aysel"})
        self.patch_cache.assert_called_once_with(7, "Aysel", "+994501112233")
        self.pulse.assert_called_once_with(
            7,
            "deal_edit_contact",
            contact_name="Aysel",
            phone="+994501112233",
        )

    def test_creates_linked_contact_when_no_contact_can_be_updated(self):
        self.context = DealContactContext(
            **{
                **self.context.__dict__,
                "get_lead": lambda _lead: {},
                "update_contact": Mock(return_value=None),
            }
        )
        result = handle_deal_contact_edit(
            {"lead_id": 7, "phone": "0501112233"}, 20, self.context
        )
        self.assertTrue(result.payload["success"])
        self.create_contact.assert_called_once_with(7, "", "0501112233")

    def test_empty_edit_and_foreign_deal_are_rejected(self):
        empty = handle_deal_contact_edit({"lead_id": 7}, 20, self.context)
        self.assertEqual(empty.status, 400)
        denied_context = DealContactContext(
            **{
                **self.context.__dict__,
                "is_funnel_chat": lambda _chat: False,
                "lead_allowed_for_chat": lambda _lead, _chat: False,
            }
        )
        denied = handle_deal_contact_edit(
            {"lead_id": 7, "name": "Aysel"}, 20, denied_context
        )
        self.assertEqual(denied.status, 400)
        self.create_contact.assert_not_called()


if __name__ == "__main__":
    unittest.main()
