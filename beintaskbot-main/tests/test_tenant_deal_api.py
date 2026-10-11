import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tenant_deal_api import DealApiContext, handlers
from tenant_policy import TenantPolicy


class PlatformError(Exception):
    pass


class Query(dict):
    def getall(self, key, default):
        value = self.get(key, default)
        return value if isinstance(value, list) else [value]


class Request:
    def __init__(self, query=None):
        self.rel_url = SimpleNamespace(query=Query(query or {}))


def response_body(response):
    return json.loads(response.body)


def manager_profile():
    return {
        "tenant_id": "company-a", "telegram_id": 20, "role": "manager", "active": True,
        "permissions": ["deals", "customers", "tasks"],
        "modules": {"deals": True, "customers": True, "tasks": True},
        "workflow": {
            "pipelines": [{"pipeline_id": 10, "owner_telegram_id": 20, "active": True}],
            "stages": [{"pipeline_id": 10, "stage_id": 100, "settings": {"visible": True}}],
            "policies": [],
        },
    }


class TenantDealApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = manager_profile()
        self.member = Mock(return_value=self.profile)
        self.sync = AsyncMock(return_value={"jobs": []})
        self.status = Mock(return_value=[{"resource": "leads", "status": "idle"}])
        self.list_deals = Mock(return_value=([{"kommo_lead_id": 7}], 1))
        self.get_deal = Mock(return_value={
            "tenant_id": "company-a", "kommo_lead_id": 7, "pipeline_id": 10, "status_id": 100,
        })
        self.log = Mock()
        self.context = DealApiContext(
            member=self.member, policy=TenantPolicy, sync_crm=self.sync, sync_status=self.status,
            list_deals=self.list_deals, get_deal=self.get_deal,
            platform_error=PlatformError, logger=self.log,
        )
        self.api = handlers(self.context)

    async def test_sync_status_uses_only_resources_allowed_to_current_member(self):
        response = await self.api["sync_status"](Request())
        self.assertEqual(response.status, 200)
        self.status.assert_called_once_with("company-a", ["leads", "tasks"])
        self.profile.update(role="worker", permissions=["tasks"], modules={"tasks": True})
        self.status.reset_mock()
        response = await self.api["sync_status"](Request())
        self.assertEqual(response.status, 200)
        self.status.assert_called_once_with("company-a", ["tasks"])

    async def test_sync_status_denies_inactive_member_and_hides_storage_error(self):
        self.profile["active"] = False
        response = await self.api["sync_status"](Request())
        self.assertEqual(response.status, 403)
        self.status.assert_not_called()
        self.profile["active"] = True
        self.status.side_effect = RuntimeError("postgres password")
        response = await self.api["sync_status"](Request())
        self.assertEqual(response.status, 503)
        self.assertNotIn("password", response.text)

    async def test_deal_list_forwards_filters_inside_policy_scope(self):
        request = Request({"pipeline_id": ["10"], "status_id": ["100"], "search": "Ali",
                           "limit": "25", "offset": "5"})
        response = await self.api["deals"](request)
        self.assertEqual(response.status, 200)
        self.list_deals.assert_called_once_with(
            tenant_id="company-a", search="Ali", pipeline_ids=[10], status_ids=[100],
            limit=25, offset=5, scope=[{"pipeline_id": 10, "status_ids": [100]}],
        )
        self.assertEqual(response_body(response)["total"], 1)

    async def test_invalid_filter_is_rejected_before_storage(self):
        response = await self.api["deals"](Request({"pipeline_id": ["invalid"]}))
        self.assertEqual(response.status, 400)
        self.list_deals.assert_not_called()

    async def test_explicit_refresh_is_queued_and_failure_returns_cached_rows(self):
        response = await self.api["deals"](Request({"refresh": "1", "full": "1"}))
        self.assertEqual(response.status, 200)
        self.sync.assert_awaited_once_with(self.profile, include_tasks=False, force_full=True)
        self.sync.side_effect = PlatformError("Kommo unavailable")
        response = await self.api["deals"](Request({"refresh": "1"}))
        self.assertTrue(response_body(response)["stale"])
        self.assertEqual(response_body(response)["warning"], "Kommo unavailable")

    async def test_direct_deal_rechecks_current_pipeline_and_tenant(self):
        response = await self.api["deal"](Request({"lead_id": "7"}))
        self.assertEqual(response.status, 200)
        self.get_deal.assert_called_once_with(tenant_id="company-a", kommo_lead_id=7)
        self.get_deal.return_value = {"tenant_id": "company-a", "pipeline_id": 11, "status_id": 100}
        response = await self.api["deal"](Request({"lead_id": "7"}))
        self.assertEqual(response.status, 404)

    async def test_missing_permission_and_bad_lead_id_fail_before_storage(self):
        self.profile["permissions"] = []
        response = await self.api["deals"](Request())
        self.assertEqual(response.status, 403)
        self.profile["permissions"] = ["deals"]
        response = await self.api["deal"](Request({"lead_id": "bad"}))
        self.assertEqual(response.status, 400)
        self.get_deal.assert_not_called()

    def test_bot_uses_extracted_deal_and_duplicate_handlers(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn("tenant_deal_handlers(DealApiContext(", source)
        self.assertIn("tenant_duplicate_handler(", source)
        for handler in ("deals", "sync_status", "deal"):
            self.assertIn(f'deal_api["{handler}"]', source)
        for legacy in ("handle_platform_crm_sync_status", "handle_platform_crm_deals",
                       "handle_platform_crm_deal", "handle_platform_deal_duplicates"):
            self.assertNotIn(f"def {legacy}", source)


if __name__ == "__main__":
    unittest.main()
