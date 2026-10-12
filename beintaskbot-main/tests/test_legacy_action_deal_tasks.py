"""Unit tests for extracted legacy deal task actions."""
from __future__ import annotations

from datetime import timezone
import unittest
from unittest.mock import Mock

from legacy_action_tasks import DealTaskContext, handle_deal_task


class DealTaskActionTests(unittest.TestCase):
    def setUp(self):
        self.allowed = Mock(return_value=True)
        self.create_task = Mock(return_value={"_embedded": {"tasks": [{"id": 41}]}})
        self.update_task = Mock(return_value=True)
        self.invalidate = Mock()
        self.pulse = Mock()
        self.telegram = Mock()
        self.push = Mock()
        self.notice = Mock()
        self.context = DealTaskContext(
            lead_allowed_for_chat=self.allowed,
            create_task=self.create_task,
            update_task=self.update_task,
            active_assignees={"rufat": ("Rüfət Həsənzadə", "Rüfət", 10932455)},
            reminder_task_type_id=99,
            timezone=timezone.utc,
            get_funnel_owner=lambda _chat: {"name": "Nizami"},
            get_employee_name=lambda _chat, fallback: fallback,
            get_chat_id_by_name=lambda _name: 30,
            send_telegram_text=self.telegram,
            send_push_notification=self.push,
            remember_staff_notice=self.notice,
            kommo_base_url="https://example.kommo.com",
            invalidate_overview=self.invalidate,
            record_pulse=self.pulse,
            created_task_id=lambda value: value["_embedded"]["tasks"][0]["id"],
        )

    def test_create_uses_known_assignee_and_notifies_other_employee(self):
        result = handle_deal_task(
            "deal_add_task",
            {
                "lead_id": 7,
                "text": "Tapşırıq",
                "executor": "Rüfət Həsənzadə",
                "deadline_ts": 2_000_000_000,
            },
            20,
            self.context,
        )
        self.assertEqual(result.payload["task_id"], 41)
        self.create_task.assert_called_once()
        self.telegram.assert_called_once()
        self.push.assert_called_once()
        self.notice.assert_called_once()

    def test_edit_preserves_deadline_and_text(self):
        result = handle_deal_task(
            "deal_edit_task",
            {"lead_id": 7, "task_id": 8, "text": "Yeni", "deadline_ts": 123},
            20,
            self.context,
        )
        self.assertTrue(result.payload["success"])
        self.update_task.assert_called_once_with(8, {"text": "Yeni", "complete_till": 123})
        self.pulse.assert_called_once_with(7, "deal_edit_task")

    def test_delete_is_soft_completion(self):
        result = handle_deal_task(
            "deal_delete_task", {"lead_id": 7, "task_id": 8}, 20, self.context
        )
        self.assertTrue(result.payload["success"])
        self.update_task.assert_called_once_with(
            8, {"is_completed": True, "result": {"text": "Silindi"}}
        )

    def test_foreign_deal_is_rejected_before_provider_call(self):
        self.allowed.return_value = False
        result = handle_deal_task(
            "deal_delete_task", {"lead_id": 7, "task_id": 8}, 20, self.context
        )
        self.assertEqual(result.status, 400)
        self.update_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
