"""Batch persistence contract tests; no production connections or provider calls."""
import ast
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock

from tenant_crm_snapshots import deal_rows, task_rows


class SnapshotTests(unittest.TestCase):
    def test_valid_ids_and_original_order_are_preserved(self):
        for normalize, key in ((deal_rows, 'kommo_lead_id'), (task_rows, 'kommo_task_id')):
            items = [{key: 3}, {'id': '4'}, {key: 3}, {key: -1}, {key: 'bad'}, {key: None}]
            self.assertEqual([row[1] for row in normalize('tenant-a', items, json.dumps)], [3, 4, 3])

    def test_company_is_taken_from_context_not_provider_payload(self):
        item = {'id': 10, 'tenant_id': 'tenant-b', 'raw': {'tenant_id': 'tenant-b'}}
        for normalize in (deal_rows, task_rows):
            self.assertEqual(normalize('tenant-a', [item], json.dumps)[0][0], 'tenant-a')

    def test_deal_limits_and_nullable_dates_are_compatible(self):
        item = {'id': 1, 'name': 'ə' * 501, 'contact_name': 'a' * 501, 'phone': '1' * 81,
                'stage_name': 's' * 241, 'channel': 'c' * 101, 'last_message': 'm' * 4001,
                'pipeline_id': '2', 'status_id': '3', 'raw': [], 'last_message_at': ''}
        row = deal_rows('company', [item], json.dumps)[0]
        self.assertEqual(row[:4], ('company', 1, 2, 3))
        self.assertEqual([len(value) for value in row[4:10]], [240, 500, 500, 80, 100, 4000])
        self.assertEqual(row[10:12], (None, None))
        self.assertEqual(json.loads(row[12]), {})

    def test_task_markers_and_shared_provider_admin_are_unchanged(self):
        text = 'İş tapşırığı\n[CRM:company-member-marker]'
        item = {'id': 5, 'kommo_lead_id': '6', 'text': text, 'responsible_id': 7,
                'completed': True, 'due_at': '2026-10-08T12:00:00+00:00', 'raw': {'x': 'ə'}}
        original = copy.deepcopy(item)
        row = task_rows('company', [item], json.dumps)[0]
        self.assertEqual(row[:7], ('company', 5, 6, text, item['due_at'], 7, True))
        self.assertEqual(json.loads(row[7]), item['raw'])
        self.assertEqual(item, original)


class BatchStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'tenant_platform.py').read_text(encoding='utf-8-sig'))
        names = {'upsert_crm_deals', 'upsert_crm_tasks'}
        cls.code = compile(ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef)
                                          and n.name in names], type_ignores=[]), '<batch-storage>', 'exec')

    def setUp(self):
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.connect = MagicMock()
        self.connect.return_value.__enter__.return_value = self.conn
        self.ns = {'_connect': self.connect, '_ensure_schema': Mock(), '_json': json.dumps}
        exec(self.code, self.ns)

    def test_250_records_use_one_batch_and_one_commit(self):
        for kind in ('deals', 'tasks'):
            with self.subTest(kind=kind):
                self.cur.reset_mock(); self.conn.commit.reset_mock()
                rows = [{'id': index} for index in range(1, 251)]
                count = self.ns['upsert_crm_' + kind](tenant_id='company-a', **{kind: rows})
                self.assertEqual(count, 250)
                self.cur.execute.assert_not_called()
                self.cur.executemany.assert_called_once()
                sql, params = self.cur.executemany.call_args.args
                self.assertIn('ON CONFLICT (tenant_id, kommo_', sql)
                self.assertEqual(len(params), 250)
                self.assertTrue(all(row[0] == 'company-a' for row in params))
                self.conn.commit.assert_called_once()

    def test_empty_or_invalid_only_batch_does_not_open_database(self):
        for kind in ('deals', 'tasks'):
            for items in ([], [{'id': 'invalid'}, {'id': -2}]):
                self.assertEqual(self.ns['upsert_crm_' + kind](tenant_id='company', **{kind: items}), 0)
        self.connect.assert_not_called()

    def test_database_failure_does_not_commit_or_report_saved_records(self):
        self.cur.executemany.side_effect = RuntimeError('write failed')
        for kind in ('deals', 'tasks'):
            with self.assertRaises(RuntimeError):
                self.ns['upsert_crm_' + kind](tenant_id='company', **{kind: [{'id': 1}]})
        self.conn.commit.assert_not_called()

    def test_invalid_secondary_id_fails_before_writing_any_record(self):
        with self.assertRaises(ValueError):
            self.ns['upsert_crm_deals'](tenant_id='company', deals=[{'id': 1}, {'id': 2, 'pipeline_id': 'bad'}])
        self.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
