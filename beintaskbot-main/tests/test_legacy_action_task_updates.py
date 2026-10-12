"""Unit tests for task deadline and soft-delete actions."""
from __future__ import annotations

from datetime import timezone
import unittest
from unittest.mock import Mock

from legacy_action_task_updates import TaskUpdateContext, handle_task_update_action


class TaskUpdateActionTests(unittest.TestCase):
    def setUp(self):
        self.allowed = Mock(return_value=True)
        self.update = Mock(return_value=True)
        self.invalidate = Mock()
        self.start = Mock(return_value=True)
        self.finish = Mock()
        self.context = TaskUpdateContext(
            task_allowed_for_chat=self.allowed,
            update_task=self.update,
            invalidate_overview=self.invalidate,
            employee_type=lambda _chat: "salary",
            has_active_session=lambda _chat, _task: False,
            start_session=self.start,
            finish_session=self.finish,
            timezone=timezone.utc,
        )

    def test_deadline_update_and_late_reason_keep_kpi_side_effect(self):
        result = handle_task_update_action(
            "update_task_deadline",
            {"task_id": 7, "time_preset": "+2h", "reason": "Çatdıra bilmirəm"},
            20,
            self.context,
        )
        self.assertTrue(result.payload["success"])
        self.assertIn("complete_till", self.update.call_args.args[1])
        self.invalidate.assert_called_once()
        self.start.assert_called_once_with(20, 7)
        self.finish.assert_called_once_with(20, 7, 1, "Çatdıra bilmirəm", deadline_ts=0)

    def test_delete_is_scoped_and_soft(self):
        result = handle_task_update_action(
            "delete_task", {"task_id": 7}, 20, self.context
        )
        self.assertTrue(result.payload["success"])
        self.update.assert_called_once_with(
            7, {"is_completed": True, "result": {"text": "Tamamlandı"}}
        )
        self.allowed.return_value = False
        denied = handle_task_update_action(
            "delete_task", {"task_id": 8}, 20, self.context
        )
        self.assertEqual(denied.status, 403)


if __name__ == "__main__":
    unittest.main()
