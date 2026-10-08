"""Pure query contracts: isolation, scopes, parameters and serialization."""
from datetime import datetime, timezone
import unittest
from tenant_crm_queries import deal_filters, task_filters, public_crm_row


class CRMQueries(unittest.TestCase):
    def test_every_query_starts_with_tenant_isolation(self):
        for builder in (deal_filters, task_filters):
            sql_a, values_a = builder(tenant_id='company-a')
            sql_b, values_b = builder(tenant_id='company-b')
            self.assertEqual(sql_a, sql_b)
            self.assertTrue(sql_a.startswith('tenant_id = %s::uuid'))
            self.assertEqual(values_a[0], 'company-a')
            self.assertEqual(values_b[0], 'company-b')

    def test_deal_scope_filters_are_intersected_with_user_selection(self):
        sql, values = deal_filters(tenant_id='a', scope=[{'pipeline_id': 10, 'status_ids': [101, '102']}, {'pipeline_id': 20}], pipeline_ids=[20], status_ids=[201])
        self.assertIn('((pipeline_id = %s AND status_id = ANY(%s)) OR (pipeline_id = %s))', sql)
        self.assertTrue(sql.endswith('AND pipeline_id = ANY(%s) AND status_id = ANY(%s)'))
        self.assertEqual(values, ['a', 10, [101, 102], 20, [20], [201]])
        self.assertEqual(sql.count('%s'), len(values))

    def test_empty_deal_scope_denies_even_when_filter_requests_pipeline(self):
        sql, values = deal_filters(tenant_id='a', scope=[], pipeline_ids=[10])
        self.assertIn('AND FALSE AND pipeline_id = ANY(%s)', sql)
        self.assertEqual(values, ['a', [10]])

    def test_search_is_parameterized_and_clipped(self):
        search = "' OR TRUE; DROP TABLE saas_tenants; --"
        sql, values = deal_filters(tenant_id='a', search=search)
        self.assertNotIn(search, sql)
        self.assertEqual(values[1:], ['%' + search + '%'] * 4)
        _, values = deal_filters(tenant_id='a', search='x' * 200)
        self.assertEqual(len(values[1]), 162)

    def test_deleted_deals_and_their_tasks_are_excluded(self):
        sql, _ = deal_filters(tenant_id='a')
        self.assertIn('deleted_at IS NULL', sql)
        sql, _ = task_filters(tenant_id='a', scope={'kind': 'all'})
        self.assertIn('deleted.tenant_id=saas_crm_tasks.tenant_id', sql)
        self.assertIn('deleted.deleted_at IS NOT NULL', sql)

    def test_numeric_selection_keeps_legacy_normalization(self):
        sql, values = deal_filters(tenant_id='a', pipeline_ids=['20', 'bad', -1, 30], status_ids=['40', None])
        self.assertEqual(values, ['a', [20, 30], [40]])
        self.assertEqual(sql.count('%s'), len(values))

    def test_task_pipeline_scope_correlates_to_same_company(self):
        sql, values = task_filters(tenant_id='a', scope={'kind': 'pipelines', 'pipelines': [{'pipeline_id': 10, 'status_ids': [100]}, {'pipeline_id': 20}]})
        self.assertIn('d.tenant_id = saas_crm_tasks.tenant_id', sql)
        self.assertIn('d.kommo_lead_id = saas_crm_tasks.kommo_lead_id', sql)
        self.assertEqual(values, ['a', 10, [100], 20])
        self.assertEqual(sql.count('%s'), len(values))

    def test_empty_task_pipelines_deny(self):
        sql, values = task_filters(tenant_id='a', scope={'kind': 'pipelines', 'pipelines': []})
        self.assertIn('AND (FALSE)', sql)
        self.assertEqual(values, ['a'])

    def test_marker_is_literal_and_not_like_pattern(self):
        marker = "[%_'; DROP TABLE tasks]"
        sql, values = task_filters(tenant_id='a', scope={'kind': 'marker', 'marker': marker})
        self.assertIn('strpos(text, %s) > 0', sql)
        self.assertNotIn(marker, sql)
        self.assertEqual(values, ['a', marker])

    def test_unknown_or_empty_marker_scopes_deny(self):
        for scope in ({}, {'kind': 'other'}, {'kind': 'marker', 'marker': ''}):
            sql, _ = task_filters(tenant_id='a', scope=scope)
            self.assertTrue(sql.endswith('AND FALSE'))

    def test_missing_and_explicit_all_scope_preserve_trusted_contract(self):
        for scope in (None, {'kind': 'all'}):
            sql, values = task_filters(tenant_id='a', scope=scope)
            self.assertNotIn('FALSE', sql)
            self.assertEqual(values, ['a'])

    def test_responsible_is_additional_filter_not_scope_override(self):
        sql, values = task_filters(tenant_id='a', responsible_id=42, scope={'kind': 'marker', 'marker': '[employee]'} )
        self.assertTrue(sql.endswith('AND strpos(text, %s) > 0 AND responsible_id = %s'))
        self.assertEqual(values, ['a', '[employee]', 42])

    def test_public_payload_preserves_input_and_serializes_dates(self):
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        row = {'created_at': now, 'due_at': now, 'raw': None}
        result = public_crm_row(row)
        self.assertEqual(result['created_at'], now.isoformat())
        self.assertEqual(result['due_at'], now.isoformat())
        self.assertEqual(result['raw'], {})
        self.assertIs(row['created_at'], now)
        self.assertIsNone(row['raw'])


if __name__ == '__main__':
    unittest.main()
