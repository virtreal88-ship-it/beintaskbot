"""Repository contracts without PostgreSQL or external provider access."""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock
from tenant_crm_repository import CRMRepository


class CRMStorage(unittest.TestCase):
    def setUp(self):
        self.connect = MagicMock()
        self.conn = self.connect.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.schema = Mock()
        self.store = CRMRepository(self.connect, self.schema, serialize=json.dumps)

    def test_constructor_does_not_connect_or_initialize_schema(self):
        self.connect.assert_not_called()
        self.schema.assert_not_called()

    def test_empty_writes_and_disabled_responsible_do_not_connect(self):
        self.assertEqual(self.store.upsert_deals(tenant_id='a', deals=[]), 0)
        self.assertEqual(self.store.upsert_tasks(tenant_id='a', tasks=[]), 0)
        self.assertEqual(self.store.upsert_messages(tenant_id='a', kommo_lead_id=7, messages=[]), 0)
        self.assertEqual(self.store.list_tasks(tenant_id='a', responsible_id=0), [])
        self.connect.assert_not_called()

    def test_write_uses_one_batch_schema_and_commit(self):
        rows = [{'id': index} for index in range(1, 301)]
        self.assertEqual(self.store.upsert_deals(tenant_id='company-a', deals=rows), 300)
        self.connect.assert_called_once_with()
        self.schema.assert_called_once_with(self.conn)
        self.cur.executemany.assert_called_once()
        self.cur.execute.assert_not_called()
        self.conn.commit.assert_called_once_with()

    def test_failed_write_propagates_and_exits_transaction_without_commit(self):
        self.cur.executemany.side_effect = RuntimeError('write failed')
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            self.store.upsert_tasks(tenant_id='a', tasks=[{'id': 1}])
        self.conn.commit.assert_not_called()
        self.assertIs(self.connect.return_value.__exit__.call_args.args[0], RuntimeError)

    def test_commit_failure_never_returns_saved_count(self):
        self.conn.commit.side_effect = RuntimeError('commit failed')
        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
            self.store.upsert_messages(tenant_id='a', kommo_lead_id=7, messages=[{'id': '1'}])
        self.assertIs(self.connect.return_value.__exit__.call_args.args[0], RuntimeError)

    def test_schema_failure_prevents_queries_and_commit(self):
        self.schema.side_effect = RuntimeError('schema failed')
        with self.assertRaisesRegex(RuntimeError, 'schema failed'):
            self.store.upsert_deals(tenant_id='a', deals=[{'id': 1}])
        self.cur.executemany.assert_not_called()
        self.conn.commit.assert_not_called()

    def test_all_rows_validated_before_opening_connection(self):
        with self.assertRaises(ValueError):
            self.store.upsert_deals(tenant_id='a', deals=[{'id': 1}, {'id': 2, 'pipeline_id': 'bad'}])
        self.connect.assert_not_called()

    def test_count_and_page_share_connection_and_identical_filters(self):
        self.cur.fetchone.return_value = {'total': 3}
        self.cur.fetchall.return_value = [{'id': 1, 'created_at': datetime(2026, 10, 9, tzinfo=timezone.utc)}]
        rows, total = self.store.list_deals(tenant_id='a', scope=[], limit=1000, offset=-2)
        self.assertEqual(total, 3)
        self.assertEqual(rows[0]['created_at'], '2026-10-09T00:00:00+00:00')
        self.connect.assert_called_once()
        count, page = self.cur.execute.call_args_list
        self.assertIn('AND FALSE', count.args[0])
        self.assertEqual(count.args[1], page.args[1][:-2])
        self.assertEqual(page.args[1][-2:], [200, 0])
        self.conn.commit.assert_not_called()

    def test_latest_messages_keep_chronology_and_limit(self):
        self.cur.fetchall.return_value = []
        self.store.list_messages(tenant_id='a', kommo_lead_id=7, limit=1000)
        sql, params = self.cur.execute.call_args.args
        self.assertEqual(params, ('a', 7, 300))
        self.assertLess(sql.index('DESC'), sql.index('LIMIT'))
        self.assertLess(sql.index('LIMIT'), sql.index(' ASC'))
        self.assertIn('tenant_id = %s::uuid AND kommo_lead_id = %s', sql)

    def test_individual_reads_use_tenant_and_deleted_filter(self):
        self.cur.fetchone.return_value = None
        self.assertIsNone(self.store.get_deal(tenant_id='a', kommo_lead_id=7))
        self.assertIsNone(self.store.get_task(tenant_id='b', kommo_task_id=8))
        for call, expected in zip(self.cur.execute.call_args_list, [('a', 7), ('b', 8)]):
            self.assertEqual(call.args[1], expected)
            self.assertIn('tenant_id', call.args[0])
            self.assertIn('deleted_at IS', call.args[0])

    def test_injected_id_factory_and_payload_are_used(self):
        identity = Mock(return_value='fixed-id')
        payload = Mock(return_value={'visible': True})
        store = CRMRepository(self.connect, self.schema, id_factory=identity, payload=payload)
        store.upsert_messages(tenant_id='a', kommo_lead_id=7, messages=[{'id': '1'}])
        self.assertEqual(self.cur.executemany.call_args.args[1][0][0], 'fixed-id')
        self.cur.fetchone.return_value = {'kommo_lead_id': 7}
        self.assertEqual(store.get_deal(tenant_id='a', kommo_lead_id=7), {'visible': True})
        payload.assert_called_once_with({'kommo_lead_id': 7})

    def test_no_platform_or_provider_dependency(self):
        source = (Path(__file__).resolve().parents[1] / 'tenant_crm_repository.py').read_text(encoding='utf-8')
        imports = [node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn('tenant_platform', imports)
        for forbidden in ('requests', 'openai', 'psycopg'):
            self.assertNotIn(forbidden, imports)


if __name__ == '__main__':
    unittest.main()
