"""Tests for explicit deal routing and stage updates."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from legacy_action_deal_routing import DealRoutingContext, handle_deal_edit


class DealRoutingActionTests(unittest.TestCase):
    def setUp(self):
        self.move = Mock(return_value=True)
        self.update = Mock(return_value={"id": 7})
        self.invalidate = Mock()
        self.pulse = Mock()
        self.patch = Mock()
        self.source = Mock()
        self.context = DealRoutingContext(
            lead_allowed_for_chat=lambda _lead, _chat: True,
            normalize_assignee_name=lambda value: str(value or "").strip(),
            move_lead_to_assignee=self.move,
            route_for_employee=lambda _name: (20, 201),
            load_pipeline_stage_maps=lambda _pipeline, **_kwargs: (
                {"new": 201}, {201: "Yeni"}, []
            ),
            invalidate_overview=self.invalidate,
            record_pulse=self.pulse,
            get_partner=lambda _lead: "",
            get_partner_list=lambda _chat: [],
            get_lead=lambda _lead: {
                "pipeline_id": 20,
                "_embedded": {"contacts": [{"id": 9}]},
            },
            lead_pipeline_id=lambda lead: int(lead.get("pipeline_id") or 0),
            pipeline_for_chat=lambda _chat: 20,
            is_admin=lambda _chat: False,
            personal_pipeline_ids=lambda: {20},
            update_lead=self.update,
            apply_source=self.source,
            patch_overview_stage=self.patch,
            clear_terminal_reentry=Mock(),
        )

    def test_assignee_route_uses_only_explicit_transfer_port(self):
        result = handle_deal_edit(
            {"lead_id": 7, "assignee_name": "Rüfət"}, 20, self.context
        )
        self.assertTrue(result.payload["success"])
        self.move.assert_called_once_with(7, "Rüfət")
        self.update.assert_not_called()
        self.assertEqual(result.payload["stage_key"], "new")

    def test_stage_update_remains_inside_current_personal_pipeline(self):
        result = handle_deal_edit(
            {"lead_id": 7, "stage_key": "new", "source": "Instagram"},
            20,
            self.context,
        )
        self.assertTrue(result.payload["success"])
        self.update.assert_called_once_with(7, {"status_id": 201, "pipeline_id": 20})
        self.source.assert_called_once_with(9, 7, "Instagram", overwrite=True)
        self.patch.assert_called_once_with(7, "new")

    def test_foreign_deal_and_unknown_stage_are_rejected(self):
        denied_context = DealRoutingContext(
            **{**self.context.__dict__, "lead_allowed_for_chat": lambda _lead, _chat: False}
        )
        denied = handle_deal_edit({"lead_id": 7, "stage_key": "new"}, 20, denied_context)
        self.assertEqual(denied.status, 403)
        missing = handle_deal_edit({"lead_id": 7, "stage_key": "missing"}, 20, self.context)
        self.assertFalse(missing.payload["success"])
        self.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
