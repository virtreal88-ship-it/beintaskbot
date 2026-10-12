"""Architecture contracts for the extracted legacy employee access modules."""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


class LegacyEmployeeAccessModuleTests(unittest.TestCase):
    def test_bot_only_keeps_cleanup_composition_wrapper(self):
        bot_names = definitions(ROOT / "bot.py")
        extracted = {
            "load_users", "save_users", "ensure_known_employee_registrations",
            "get_chat_id_for_kommo_user", "get_kommo_user_id_for_chat",
            "employee_access_profile", "employee_requires_completion_approval",
            "get_rufat_compat_chat_ids", "employee_has_permission",
            "can_receive_staff_notification", "is_admin", "can_use_deal_ai",
        }
        self.assertFalse(extracted & bot_names)
        self.assertIn("remove_retired_employee_access", bot_names)

    def test_each_module_has_one_bounded_responsibility(self):
        expected = {
            "legacy_user_registry.py": {
                "load_users", "save_users", "ensure_known_employee_registrations",
                "get_chat_id_for_kommo_user", "get_kommo_user_id_for_chat",
            },
            "legacy_employee_access.py": {
                "employee_access_profile", "employee_requires_completion_approval",
                "get_rufat_compat_chat_ids", "employee_has_permission",
                "can_receive_staff_notification", "is_admin", "can_use_deal_ai",
            },
            "legacy_roster_cleanup.py": {"remove_retired_employee_access"},
            "legacy_employee_notifications.py": {
                "notification_event_catalog", "normalize_employee_notifications",
                "effective_employee_notifications", "notification_channel_enabled",
            },
        }
        for filename, names in expected.items():
            path = ROOT / filename
            with self.subTest(filename=filename):
                self.assertTrue(names <= definitions(path))
                self.assertLessEqual(len(path.read_text(encoding="utf-8-sig").splitlines()), 250)
                self.assertNotIn("import bot", path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
