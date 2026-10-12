"""Tests for the fixed legacy deal postponement action."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from legacy_action_deals import DealUnreachableContext, handle_deal_unreachable


class UnreachableDealTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.access = Mock(return_value={"active": True, "permissions": ["deals"]})
        self.allowed = Mock(return_value=True)
        self.load_stages = Mock(
            return_value=({"postponed": 123}, {123: "TƏXİRƏ SALINIB"}, [])
        )
        self.update_lead = Mock(return_value={"id": 7})
        self.invalidate = Mock()
        self.clear_reentry = Mock()
        self.record_pulse = Mock()
        self.stage_cache = {8329347: "stale"}
        self.context = DealUnreachableContext(
            employee_access_profile=self.access,
            lead_allowed_for_chat=self.allowed,
            load_pipeline_stage_maps=self.load_stages,
            update_lead=self.update_lead,
            invalidate_overview=self.invalidate,
            clear_terminal_reentry=self.clear_reentry,
            record_pulse=self.record_pulse,
            target_pipeline_id=8329347,
            pipeline_stage_cache=self.stage_cache,
        )
        self.data = {
            "action": "deal_unreachable",
            "lead_id": 7,
            "pipeline_id": 999,
            "status_id": 999,
        }

    async def test_staff_can_move_only_to_fixed_pipeline_and_named_stage(self):
        result = await handle_deal_unreachable(self.data, 20, self.context)
        self.assertEqual(result.status, 200)
        self.assertTrue(result.payload["success"])
        self.update_lead.assert_called_once_with(
            7, {"pipeline_id": 8329347, "status_id": 123}
        )
        self.load_stages.assert_called_once_with(8329347, fallback=False)
        self.assertNotIn(8329347, self.stage_cache)
        self.invalidate.assert_called_once()
        self.record_pulse.assert_called_once_with(
            7, "deal_edit", pipeline_id=8329347, stage_key="postponed"
        )

    async def test_missing_or_ambiguous_stage_never_mutates_deal(self):
        variants = (
            {123: "New"},
            {123: "Təxirə salınıb", 124: "Təxirə salınıb"},
            {142: "Təxirə salınıb"},
        )
        for names in variants:
            with self.subTest(names=names):
                self.load_stages.return_value = ({}, names, [])
                result = await handle_deal_unreachable(self.data, 20, self.context)
                self.assertEqual(result.status, 400)
                self.assertFalse(result.payload["success"])
        self.update_lead.assert_not_called()

    async def test_hidden_page_inactive_employee_and_foreign_deal_are_denied(self):
        for access in (
            {"active": False, "permissions": ["deals"]},
            {"active": True, "permissions": []},
        ):
            with self.subTest(access=access):
                self.access.return_value = access
                result = await handle_deal_unreachable(self.data, 20, self.context)
                self.assertEqual(result.status, 403)
        self.access.return_value = {"active": True, "permissions": ["deals"]}
        self.allowed.return_value = False
        result = await handle_deal_unreachable(self.data, 20, self.context)
        self.assertEqual(result.status, 403)
        self.update_lead.assert_not_called()

    async def test_provider_failure_does_not_report_success_or_invalidate(self):
        self.update_lead.return_value = None
        result = await handle_deal_unreachable(self.data, 20, self.context)
        self.assertEqual(result.status, 502)
        self.assertFalse(result.payload["success"])
        self.invalidate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
