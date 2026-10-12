from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from legacy_task_presenter import (
    assignee_from_marker,
    deadline_view,
    price_from_marker,
)


class LegacyTaskPresenterTests(unittest.TestCase):
    def test_assignee_marker_expands_short_name(self):
        self.assertEqual(assignee_from_marker("[Rüfət] Müştəriyə zəng et"), "Rüfət Həsənzadə")
        self.assertEqual(assignee_from_marker("[Hüseyn:25] Quraşdır"), "Hüseyn Səfərov")
        self.assertEqual(assignee_from_marker("Adi mətn"), "")

    def test_price_marker_supports_both_legacy_shapes(self):
        self.assertEqual(price_from_marker("[25] İş"), "25")
        self.assertEqual(price_from_marker("[Rüfət:25.50] İş"), "25.50")
        self.assertEqual(price_from_marker("[Rüfət] İş"), "")

    def test_deadline_view_formats_current_and_overdue_tasks(self):
        zone = ZoneInfo("Asia/Baku")
        now = datetime(2026, 10, 12, 12, 0, tzinfo=zone)
        future = int(datetime(2026, 10, 12, 13, 30, tzinfo=zone).timestamp())
        past = int(datetime(2026, 10, 12, 9, 30, tzinfo=zone).timestamp())
        self.assertEqual(deadline_view(future, now)[1:], (False, "12.10 13:30"))
        self.assertEqual(deadline_view(past, now)[1:], (True, "2 saat gecikir"))


if __name__ == "__main__":
    unittest.main()
