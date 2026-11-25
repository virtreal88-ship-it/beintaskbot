import unittest
from unittest.mock import call, patch
import telegram_monitor as monitor


class AvailabilityTests(unittest.TestCase):
    def test_retry_only_after_failure(self):
        for results, expected in (([True], "up"), ([False, True], "up"), ([False, False], "down")):
            with self.subTest(results=results), patch.object(monitor, "healthy", side_effect=results), patch.object(monitor.time, "sleep") as sleep:
                self.assertEqual(monitor.sample(), expected)
                self.assertEqual(sleep.call_args_list, [call(60)] if len(results) == 2 else [])

    def test_transitions_recovery_and_repeat(self):
        for previous, state, send in (("up", "down", True), ("down", "down", False),
                                      ("down", "up", True), ("up", "up", False)):
            with self.subTest(previous=previous, state=state), patch.object(monitor, "telegram") as api:
                monitor.publish(state, previous)
                self.assertEqual(api.call_args_list, [call("sendMessage", {"text": monitor.MESSAGES[state]})] if send else [])

    def test_initial_run_verifies_recipient_and_only_pages_for_an_outage(self):
        for state in ("up", "down"):
            with patch.dict("os.environ", CRM_CHAT_ID="1234"), patch.object(monitor, "telegram", return_value={"id": 1234, "type": "private"}) as api:
                monitor.publish(state, None)
                self.assertEqual(api.call_args_list[0], call("getChat", {}))
                self.assertEqual(api.call_count, 2 if state == "down" else 1)
        with patch.dict("os.environ", CRM_CHAT_ID="1234"), patch.object(monitor, "telegram", return_value={"type": "group"}) as api:
            with self.assertRaises(RuntimeError):
                monitor.publish("down", None)
            api.assert_called_once_with("getChat", {})

    def test_previous_state_spans_scheduled_and_manual_runs(self):
        scheduled = {"workflow_runs": [{"id": 1, "updated_at": "2026-01-01T01:00:00Z"}]}
        manual = {"workflow_runs": [{"id": 2, "updated_at": "2026-01-01T02:00:00Z"}]}
        jobs = {"jobs": [{"name": "Monitor", "conclusion": "success", "steps": [
            {"name":"Checkpoint up","conclusion":"skipped"}, {"name":"Checkpoint down","conclusion":"success"}]}]}
        with patch.object(monitor, "github", side_effect=[scheduled, manual, jobs]) as api:
            self.assertEqual(monitor.previous_state(), "down")
            self.assertEqual(api.call_args_list[-1], call("runs/2/jobs"))
        with patch.object(monitor, "github", side_effect=[scheduled, manual, {"jobs": []}]):
            with self.assertRaises(RuntimeError):
                monitor.previous_state()
        with patch.object(monitor, "github", return_value={"workflow_runs": []}):
            self.assertIsNone(monitor.previous_state())

    def test_marker_required_and_connections_closed(self):
        with patch.object(monitor.http.client, "HTTPSConnection") as factory:
            conn = factory.return_value
            response = conn.getresponse.return_value
            response.status = 200
            response.getheader.return_value = "worker"
            self.assertTrue(monitor.healthy())
            response.getheader.return_value = None
            self.assertFalse(monitor.healthy())
            response.status = 502
            self.assertFalse(monitor.healthy())
            conn.getresponse.side_effect = TimeoutError
            self.assertFalse(monitor.healthy())
            self.assertEqual(conn.close.call_count, 4)
