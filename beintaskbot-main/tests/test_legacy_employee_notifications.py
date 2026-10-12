"""Policy tests without importing the legacy application's dependencies."""

import ast
import unittest
from pathlib import Path


def load_policy():
    path = Path(__file__).resolve().parents[1] / "legacy_employee_notifications.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [
        node for node in tree.body
        if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    profiles = {}
    namespace = {
        "employee_access_profile": lambda chat_id: profiles[int(chat_id)],
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace, profiles


class LegacyEmployeeNotificationTests(unittest.TestCase):
    def setUp(self):
        self.policy, self.profiles = load_policy()

    def test_catalog_is_filtered_by_employee_permissions(self):
        profile = {"active": True, "role": "Əməkdaş", "permissions": ["tasks"]}
        keys = {row["key"] for row in self.policy["notification_event_catalog"](profile)}
        self.assertEqual(keys, {"task_assigned", "task_due", "task_overdue"})

    def test_master_only_gets_hot_order_events(self):
        profile = {"active": True, "role": "Usta", "permissions": ["hot_orders"]}
        settings = self.policy["normalize_employee_notifications"]({}, profile)
        self.assertEqual(set(settings), {"hot_order"})
        self.assertTrue(settings["hot_order"]["telegram"])

    def test_admin_has_every_event_even_when_legacy_permission_is_absent(self):
        profile = {"active": True, "role": "Admin", "permissions": ["tasks"]}
        self.assertEqual(
            len(self.policy["notification_event_catalog"](profile)),
            len(self.policy["NOTIFICATION_EVENTS"]),
        )

    def test_explicit_channel_choice_overrides_compatibility_default(self):
        self.profiles[7] = {
            "active": True,
            "role": "Əməkdaş",
            "permissions": ["customers"],
            "notifications": {"customer_message": {"push": False, "telegram": True}},
        }
        enabled = self.policy["notification_channel_enabled"]
        self.assertFalse(enabled(7, "customer_message", "push"))
        self.assertTrue(enabled(7, "customer_message", "telegram"))

    def test_missing_configuration_preserves_historical_defaults(self):
        self.profiles[8] = {
            "active": True, "role": "Əməkdaş", "permissions": ["customers"],
        }
        enabled = self.policy["notification_channel_enabled"]
        self.assertTrue(enabled(8, "customer_message", "push"))
        self.assertFalse(enabled(8, "customer_message", "telegram"))


if __name__ == "__main__":
    unittest.main()
