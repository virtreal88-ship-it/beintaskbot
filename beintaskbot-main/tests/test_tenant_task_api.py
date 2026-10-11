import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tenant_task_api import TaskApiContext, handlers


class PlatformError(Exception):
    pass


class PendingError(Exception):
    pass


class Policy:
    def __init__(self, profile):
        self.profile = profile

    @property
    def privileged(self):
        return self.profile.get("role") in {"owner", "admin"}

    def allows(self, module):
        return self.profile.get("active", True) and module in self.profile.get("permissions", [])

    def task_scope(self):
        return {"kind": "all"} if self.privileged else {"kind": "marker", "marker": "member"}

    def pipeline_scope(self):
        return self.profile.get("scope", [])


class Request:
    def __init__(self, body=None, *, method="GET", query=None, origin="https://crm.pro.az",
                 content_type="application/json"):
        self.body = body
        self.method = method
        self.rel_url = SimpleNamespace(query=query or {})
        self.headers = {} if origin is None else {"Origin": origin}
        self.content_type = content_type

    async def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def body(response):
    return json.loads(response.body)


class TenantTaskApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {"tenant_id": "company-a", "telegram_id": 1, "role": "owner",
                        "active": True, "permissions": ["tasks"],
                        "scope": [{"pipeline_id": 10, "status_ids": [100]}]}
        self.member = Mock(return_value=self.profile)
        self.sync = AsyncMock()
        self.list_tasks = Mock(return_value=[{"kommo_task_id": 9}])
        self.executors = Mock(return_value=[
            {**self.profile, "display_name": "Owner", "workflow": {"pipelines": [{"pipeline_id": 10, "name": "Sales"}]}},
            {"tenant_id": "company-a", "telegram_id": 2, "role": "master", "permissions": [], "active": True},
        ])
        self.list_deals = Mock(return_value=([{"kommo_lead_id": 7, "name": "Lead", "pipeline_id": 10}], 1))
        self.create = AsyncMock(return_value={"task_id": 9})
        self.complete = AsyncMock(return_value={"completed": True})
        self.approval_page = Mock(return_value={"approvals": [], "has_more": False, "next_cursor": None})
        self.decide = AsyncMock(return_value={"approved": True})
        self.crm_request = AsyncMock()
        self.log = Mock()
        self.context = TaskApiContext(
            member=self.member, policy=Policy, sync_crm=self.sync, list_tasks=self.list_tasks,
            executor_profiles=self.executors, list_deals=self.list_deals,
            create_task=self.create, complete_task=self.complete,
            approval_page=self.approval_page, decide_approval=self.decide,
            crm_request=self.crm_request, origin="https://crm.pro.az",
            platform_error=PlatformError, pending_error=PendingError, logger=self.log,
        )
        self.api = handlers(self.context)

    async def test_task_list_is_scoped_and_refresh_is_explicit(self):
        response = await self.api["tasks"](Request(query={"refresh": "1", "full": "1"}))
        self.assertEqual(response.status, 200)
        self.sync.assert_awaited_once_with(self.profile, include_tasks=True, force_full=True)
        self.list_tasks.assert_called_once_with(tenant_id="company-a", limit=200, scope={"kind": "all"})
        self.assertEqual(body(response)["tasks"], [{"kommo_task_id": 9}])

    async def test_refresh_failure_returns_cached_tasks_with_warning(self):
        self.sync.side_effect = PlatformError("Kommo unavailable")
        response = await self.api["tasks"](Request(query={"refresh": "true"}))
        self.assertEqual(response.status, 200)
        self.assertTrue(body(response)["stale"])
        self.assertEqual(body(response)["warning"], "Kommo unavailable")

    async def test_tasks_are_denied_before_sync_or_storage(self):
        self.profile["permissions"] = []
        response = await self.api["tasks"](Request(query={"refresh": "1"}))
        self.assertEqual(response.status, 403)
        self.sync.assert_not_awaited()
        self.list_tasks.assert_not_called()

    async def test_worker_task_list_uses_personal_marker_scope(self):
        self.profile["role"] = "worker"
        response = await self.api["tasks"](Request())
        self.assertEqual(response.status, 200)
        self.assertEqual(self.list_tasks.call_args.kwargs["scope"], {"kind": "marker", "marker": "member"})

    async def test_options_filter_executors_and_keep_deals_in_actor_scope(self):
        response = await self.api["options"](Request())
        data = body(response)
        self.assertEqual([row["name"] for row in data["executors"]], ["Owner"])
        self.assertEqual(data["executors"][0]["pipelines"][0]["name"], "Sales")
        self.assertEqual(data["deals"], [{"id": 7, "name": "Lead", "pipeline_id": 10}])
        self.list_deals.assert_called_once_with(tenant_id="company-a", scope=self.profile["scope"], limit=200)

    async def test_create_and_complete_forward_once_to_domain_services(self):
        create_request = Request({"text": "Call"}, method="POST")
        response = await self.api["create"](create_request)
        self.assertEqual(body(response)["task_id"], 9)
        self.create.assert_awaited_once_with(self.profile, {"text": "Call"}, self.crm_request)
        complete_request = Request({"task_id": 9}, method="POST")
        response = await self.api["complete"](complete_request)
        self.assertTrue(body(response)["completed"])
        self.complete.assert_awaited_once_with(self.profile, {"task_id": 9}, self.crm_request)

    async def test_writes_reject_foreign_origin_and_non_object_body(self):
        for endpoint in ("create", "complete"):
            response = await self.api[endpoint](Request({}, method="POST", origin="https://evil.example"))
            self.assertEqual(response.status, 403)
            response = await self.api[endpoint](Request([], method="POST"))
            self.assertEqual(response.status, 400)
        self.create.assert_not_awaited()
        self.complete.assert_not_awaited()

    async def test_unknown_write_result_requires_same_request_retry(self):
        self.create.side_effect = PendingError("check request")
        response = await self.api["create"](Request({}, method="POST"))
        self.assertEqual(response.status, 409)
        self.assertTrue(body(response)["retry_same_request"])
        self.complete.side_effect = RuntimeError("provider secret")
        response = await self.api["complete"](Request({}, method="POST"))
        self.assertEqual(response.status, 500)
        self.assertNotIn("provider secret", response.text)

    async def test_approval_queue_is_privileged_paginated_and_not_cached(self):
        request = Request(method="GET", query={"limit": "25", "cursor": "next"})
        response = await self.api["approvals"](request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.approval_page.assert_called_once_with(self.profile, limit="25", cursor="next")
        self.profile["role"] = "manager"
        response = await self.api["approvals"](request)
        self.assertEqual(response.status, 403)

    async def test_approval_decision_uses_same_write_guard_and_service(self):
        request = Request({"action": "approve"}, method="POST")
        response = await self.api["approvals"](request)
        self.assertTrue(body(response)["approved"])
        self.decide.assert_awaited_once_with(self.profile, {"action": "approve"}, self.crm_request)
        self.decide.side_effect = PendingError("check")
        response = await self.api["approvals"](request)
        self.assertEqual(response.status, 409)

    def test_bot_wires_only_extracted_task_handlers(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn("tenant_task_handlers(TaskApiContext(", source)
        for route, handler in (("/api/platform/crm/task-approvals", "approvals"),
                               ("/api/platform/crm/tasks/complete", "complete"),
                               ("/api/platform/crm/tasks/options", "options")):
            self.assertIn(route, source)
            self.assertIn(f'task_api["{handler}"]', source)
        for legacy in ("handle_platform_crm_tasks", "handle_platform_task_options", "handle_platform_task_create",
                       "handle_platform_task_complete", "handle_platform_task_approvals"):
            self.assertNotIn(f"def {legacy}", source)


if __name__ == "__main__":
    unittest.main()
