"""Bounded inbox pulse stream contracts."""

import ast
import unittest
from pathlib import Path

import legacy_inbox_pulse as pulse


ROOT = Path(__file__).resolve().parents[1]


class InboxPulseTests(unittest.TestCase):
    def setUp(self):
        pulse.clear_inbox_events()

    def test_revision_is_monotonic_and_since_is_exclusive(self):
        first = pulse.append_inbox_event({"lead_id": 1})
        second = pulse.append_inbox_event({"lead_id": 2})
        revision, rows = pulse.inbox_events_since(first["rev"])
        self.assertEqual(revision, second["rev"])
        self.assertEqual([row["lead_id"] for row in rows], [2])

    def test_stream_is_bounded_to_latest_events(self):
        for lead_id in range(170):
            pulse.append_inbox_event({"lead_id": lead_id})
        revision, rows = pulse.inbox_events_since(0)
        self.assertEqual(revision, 170)
        self.assertEqual(len(rows), 150)
        self.assertEqual(rows[0]["lead_id"], 20)

    def test_returned_rows_do_not_mutate_store(self):
        pulse.append_inbox_event({"lead_id": 1, "name": "A"})
        _revision, rows = pulse.inbox_events_since(0)
        rows[0]["name"] = "B"
        self.assertEqual(pulse.inbox_events_since(0)[1][0]["name"], "A")


class InboxPulseArchitectureTests(unittest.TestCase):
    def test_state_is_not_owned_by_bot(self):
        source = (ROOT / "legacy_inbox_pulse.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        bot_source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(bot_source)
        assignments = {
            target.id
            for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            ) if isinstance(target, ast.Name)
        }
        self.assertFalse({"_inbox_pulse_rev", "_inbox_pulse_events", "_inbox_pulse_lock"} & assignments)


if __name__ == "__main__":
    unittest.main()
