"""Unit tests for task timer and KPI actions."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from legacy_action_task_sessions import TaskSessionContext, handle_task_session_action


class TaskSessionActionTests(unittest.TestCase):
    def setUp(self):
        self.has_active = Mock(return_value=True)
        self.start = Mock(return_value=True)
        self.pause = Mock(return_value=True)
        self.finish = Mock(return_value={
            "kpi_score": 100,
            "actual_minutes": 12,
            "target_minutes": 30,
            "needs_reason": False,
        })
        self.get_task = Mock(return_value={"task_type_id": 8, "complete_till": 123})
        self.context = TaskSessionContext(
            has_active_session=self.has_active,
            start_session=self.start,
            pause_session=self.pause,
            finish_session=self.finish,
            get_task=self.get_task,
        )

    def test_check_and_start_keep_existing_messages(self):
        checked = handle_task_session_action(
            "check_started", {"task_id": 7}, 20, self.context
        )
        self.assertTrue(checked.payload["started"])
        started = handle_task_session_action("start_task", {"task_id": 7}, 20, self.context)
        self.assertIn("Başladı", started.payload["message"])

    def test_pause_passes_elapsed_seconds(self):
        result = handle_task_session_action(
            "pause_task", {"task_id": 7, "elapsed_seconds": 95}, 20, self.context
        )
        self.assertTrue(result.payload["success"])
        self.pause.assert_called_once_with(20, 7, 95)

    def test_finish_uses_current_kommo_deadline_and_type(self):
        result = handle_task_session_action(
            "finish_task", {"task_id": 7, "delay_reason": "gecikdi"}, 20, self.context
        )
        self.assertEqual(result.payload["kpi_score"], 100)
        self.finish.assert_called_once_with(
            20, 7, 8, "gecikdi", deadline_ts=123
        )

    def test_missing_task_id_is_rejected(self):
        result = handle_task_session_action("start_task", {}, 20, self.context)
        self.assertFalse(result.payload["success"])
        self.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
