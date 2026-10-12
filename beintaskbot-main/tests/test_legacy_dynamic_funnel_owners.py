"""Dynamic legacy funnel ownership must come from employee settings."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def namespace(records=None, users=None):
    tree = ast.parse((ROOT / "legacy_kommo_funnels.py").read_text(encoding="utf-8-sig"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    records = records or {}
    users = users or {}
    stages = ({"first": 10, "ugurlu": 142, "imtina": 143}, {10: "First"}, [("first", "First")])
    values = {
        "_load_employee_access_records": lambda: records,
        "employee_access_profile": lambda chat_id: {"active": records.get(str(chat_id), {}).get("active", True)},
        "get_rufat_compat_chat_ids": lambda: {2},
        "load_users": lambda: users,
        "load_pipeline_stage_maps": lambda pipeline_id: stages,
        "ADMIN_CHAT_ID": 1, "RUFAT_CHAT_ID": 2, "HUSEYN_CHAT_ID": 3, "RASIM_CHAT_ID": 4,
        "RUFAT_PIPELINE_ID": 102, "NIZAMI_PIPELINE_ID": 101,
        "HUSEYN_PIPELINE_ID": 103, "RASIM_PIPELINE_ID": 104,
        "GOZLEME_PIPELINE_ID": 200, "PIPELINE_ID": 300,
        "KOMMO_TASK_RESPONSIBLE_USER_ID": 500, "KOMMO_USERS": {},
        "STAGES": {}, "STAGE_NAMES": {},
    }
    exec(compile(tree, "<legacy funnels>", "exec"), values)
    return values


class DynamicFunnelOwnersTests(unittest.TestCase):
    def test_saved_pipeline_replaces_person_specific_fallback(self):
        ns = namespace(
            {"99": {"active": True, "pipeline_id": 777}},
            {"99": {"name": "Yeni menecer"}},
        )
        owner = ns["get_funnel_owner"](99)
        self.assertEqual((owner["chat_id"], owner["pipeline_id"], owner["name"]), (99, 777, "Yeni menecer"))
        self.assertIn(777, ns["all_personal_pipeline_ids"]())
        self.assertEqual(ns["owner_name_for_pipeline"](777), "Yeni menecer")

    def test_explicit_no_pipeline_makes_employee_an_executor(self):
        ns = namespace({"2": {"active": True, "pipeline_id": 0}}, {"2": {"name": "Rüfət"}})
        self.assertIsNone(ns["get_funnel_owner"](2))
        self.assertNotIn(102, ns["all_personal_pipeline_ids"]())

    def test_existing_installation_keeps_legacy_fallback_until_saved(self):
        ns = namespace()
        owner = ns["get_funnel_owner"](3)
        self.assertEqual((owner["pipeline_id"], owner["name"]), (103, "Hüseyn Səfərov"))


if __name__ == "__main__":
    unittest.main()
