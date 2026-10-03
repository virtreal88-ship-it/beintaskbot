"""Regression for Kommo rejecting legacy responsible_user_id=15532668."""

import ast
import asyncio
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock


class KommoTaskCreationTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig"))
        names = {"KOMMO_TASK_RESPONSIBLE_USER_ID", "ACTIVE_ASSIGNEES", "kommo_user_id_for_employee_funnel", "create_task", "handle_api_action"}
        selected = [
            node for node in source.body
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names)
            or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets))
        ]
        self.http = Mock()
        self.http.post.side_effect = self.create_response
        self.ns = {
            "web": types.SimpleNamespace(Request=dict, Response=dict, json_response=lambda data, status=200: {"data": data, "status": status}),
            "asyncio": asyncio, "datetime": datetime, "timedelta": timedelta, "BAKU_TZ": timezone(timedelta(hours=4)),
            "KOMMO_BASE_URL": "https://example.kommo.com", "HEADERS": {}, "_http": self.http, "logger": Mock(),
            "_mark_pending_bot_task_lead": Mock(), "_save_bot_created_cache": Mock(), "_bot_created_tasks": set(), "_bot_created_tasks_ts": {},
            "_TASK_CREATORS_FILE": "test-only", "read_json": lambda path: {}, "write_json": Mock(),
            "employee_personal_pipeline_ids": lambda: {10, 11, 12},
            "load_users": lambda: {"6824377548": {}}, "lead_allowed_for_chat": lambda lead, chat: True,
            "XATIRLAT_TASK_TYPE_ID": 999, "get_funnel_owner": lambda chat: {"name": "Rüfət Həsənzadə"},
            "get_chat_id_by_name": lambda name: 6824377548,
            "invalidate_rufat_overview_cache": Mock(), "record_lead_pulse_event": Mock(),
            "_created_task_id": lambda result: ((result or {}).get("_embedded", {}).get("tasks") or [{}])[0].get("id"),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "<kommo-task-test>", "exec"), self.ns)

    def create_response(self, url, **kwargs):
        payload = kwargs["json"][0]
        if payload["responsible_user_id"] != 10932455:
            return types.SimpleNamespace(status_code=400, text="NotSupportedChoice: responsible_user_id")
        return types.SimpleNamespace(status_code=201, json=lambda: {"_embedded": {"tasks": [{"id": 456, **payload}]}})

    def test_all_new_employee_assignments_use_active_shared_user(self):
        for name, (_, _, responsible) in self.ns["ACTIVE_ASSIGNEES"].items():
            with self.subTest(name=name):
                result = self.ns["create_task"](123, "Task", 2000000000, responsible_user_id=responsible, entity_type="leads", task_type_id=4232112)
                self.assertEqual(responsible, 10932455)
                self.assertEqual(result["_embedded"]["tasks"][0]["id"], 456)

    def test_moving_a_deal_does_not_reassign_tasks_to_legacy_user(self):
        for pipeline in (10, 11, 12):
            self.assertEqual(self.ns["kommo_user_id_for_employee_funnel"](pipeline), 10932455)
        self.assertIsNone(self.ns["kommo_user_id_for_employee_funnel"](999))

    def test_rufat_adds_task_from_deal_using_actual_api_handler(self):
        class Request:
            headers = {"X-TG-User-ID": "6824377548"}

            async def json(self):
                return {"action": "deal_add_task", "lead_id": 77790178, "executor": "Rüfət Həsənzadə", "text": "Task text", "deadline_ts": 2000000000, "task_type_id": 4232112}

        result = asyncio.run(self.ns["handle_api_action"](Request()))
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["data"]["success"])
        self.assertEqual(result["data"]["task_id"], 456)
        self.assertEqual(self.http.post.call_count, 1)
        sent = self.http.post.call_args.kwargs["json"][0]
        self.assertEqual(sent["entity_id"], 77790178)
        self.assertEqual(sent["responsible_user_id"], 10932455)
        self.assertEqual(sent["text"], "Task text")
        self.assertEqual(sent["task_type_id"], 4232112)


if __name__ == "__main__":
    unittest.main()
