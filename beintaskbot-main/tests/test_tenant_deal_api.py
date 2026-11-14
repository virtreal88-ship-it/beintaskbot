import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock, patch

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
            "policies": [{"policy_key":"members","value":{"20":{"pipeline_ids":["10"]}}}],
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
        self.profile["workflow"]["policies"][0]["value"]["20"]["pipeline_ids"] = []
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
            limit=25, offset=5, scope=[{"pipeline_id": 10, "status_ids": [100]}], creator_id=0,
        )
        self.assertEqual(response_body(response)["total"], 1)

    async def test_real_store_boundary_accepts_creator_and_preserves_tenant_scope(self):
        # Exercise the production compatibility wrapper, not a permissive Mock.
        # PR630 passed creator_id through the handler but omitted this adapter.
        from dataclasses import replace
        from tenant_crm_store import list_crm_deals
        tenant = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        self.profile['tenant_id'] = tenant
        from datetime import datetime, timezone
        from tenant_kommo_board_provenance import digest
        for role, creator in [('owner', 0), ('worker', 77)]:
            for proof_ready in (False, True):
                with self.subTest(role=role, proof_ready=proof_ready):
                    self.profile['role'] = role
                    self.profile['workflow']['policies'][0]['value']['20']['kommo_user_id'] = creator
                    connection = MagicMock()
                    cur = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                    raw = {'id': 7, 'pipeline_id': 10, 'status_id': 100, 'updated_at': 1000}
                    stamp = datetime(2026, 11, 14, tzinfo=timezone.utc)
                    cur.fetchone.side_effect = [{'total': 1}, {'ready': proof_ready}]
                    batches = [[{'tenant_id': tenant, 'kommo_lead_id': 7, 'raw': raw}]]
                    if proof_ready:
                        batches.extend([
                            [{'provider': 'kommo', 'status': 'connected', 'account_domain': 'company-a.kommo.com',
                              'connected_at': stamp}],
                            [{'external_id': 7, 'raw_digest': digest(raw), 'source_revision': 'a' * 64, 'raw': raw}],
                        ])
                    cur.fetchall.side_effect = batches
                    with patch('tenant_crm_store._connect', connection), patch('tenant_crm_store._ensure_schema'):
                        api = handlers(replace(self.context, list_deals=list_crm_deals))
                        response = await api['deals'](Request())
                    self.assertEqual(response.status, 200)
                    payload = response_body(response)
                    self.assertEqual(payload['total'], 1)
                    self.assertEqual(payload['deals'][0]['source_revision'], 'a' * 64 if proof_ready else None)
                    calls = cur.execute.call_args_list
                    self.assertEqual(len(calls), 5 if proof_ready else 3)
                    # Count and page still use the identical tenant/funnel/creator
                    # predicate. Read-only schema metadata is not a deal query.
                    for call in calls[:2]:
                        sql, values = call.args
                        self.assertIn('tenant_id = %s::uuid', sql)
                        self.assertEqual(values[0], tenant)
                        self.assertIn('pipeline_id = %s', sql)
                        if creator:
                            self.assertIn("raw->>'created_by_id'=%s", sql)
                            self.assertIn('77', values)
                    self.assertEqual(calls[2].args, (
                        "SELECT to_regclass('saas_crm_board_provenance') IS NOT NULL AS ready",))
                    if proof_ready:
                        integration_sql, integration_values = calls[3].args
                        self.assertIn('tenant_id=%s::uuid', integration_sql)
                        self.assertEqual(integration_values, (tenant,))
                        proof_sql, proof_values = calls[4].args
                        self.assertIn('p.tenant_id=%s::uuid', proof_sql)
                        self.assertIn('p.external_id=ANY(%s::bigint[])', proof_sql)
                        self.assertIn('p.account_id=%s AND p.connection_generation=%s', proof_sql)
                        self.assertEqual(proof_values, (tenant, 'leads', [7],
                            'company-a.kommo.com', 'kommo:' + stamp.isoformat()))

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
        self.profile["workflow"]["policies"][0]["value"]["20"]["pipeline_ids"] = []
        response = await self.api["deals"](Request())
        self.assertEqual(response.status, 403)
        self.profile["workflow"]["policies"][0]["value"]["20"]["pipeline_ids"] = ["10"]
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
