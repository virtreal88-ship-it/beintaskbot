"""Chat batch persistence without live database, providers or client messages."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import MagicMock, Mock

from tenant_message_snapshots import message_rows


class MessageRowsTests(unittest.TestCase):
    def rows(self, messages, tenant='company-a', lead=7):
        return message_rows(tenant, lead, messages, json.dumps, uuid.uuid4)

    def test_context_cannot_be_overridden_by_provider_payload(self):
        message = {'id': '1', 'tenant_id': 'company-b', 'kommo_lead_id': 999,
                   'raw': {'tenant_id': 'company-b'}}
        original = copy.deepcopy(message)
        row = self.rows([message])[0]
        self.assertEqual(row[1:4], ('company-a', 7, '1'))
        self.assertEqual(message, original)

    def test_defaults_and_nullable_dates_remain_compatible(self):
        row = self.rows([{'id': '  ext-1  ', 'raw': [], 'happened_at': ''}])[0]
        self.assertIsInstance(row[0], uuid.UUID)
        self.assertEqual(row[3:11], ('ext-1', 'incoming', '', '', '', 'text', '', None))
        self.assertEqual(json.loads(row[11]), {})

    def test_original_order_and_duplicate_updates_are_preserved(self):
        rows = self.rows([{'id': '3', 'body': 'first'}, {'id': '2'}, {'id': '3', 'body': 'last'}])
        self.assertEqual([row[3] for row in rows], ['3', '2', '3'])
        self.assertEqual([row[7] for row in rows], ['first', '', 'last'])
        self.assertEqual(len({row[0] for row in rows}), 3)

    def test_fallback_hash_is_exactly_the_existing_contract(self):
        item = {'happened_at': '2026-10-07T12:00:00Z', 'body': 'Salam ə', 'direction': 'outgoing'}
        expected = hashlib.sha256(json.dumps({'at': item['happened_at'], 'body': item['body'],
                                             'direction': item['direction']}).encode('utf-8')).hexdigest()
        self.assertEqual(self.rows([item])[0][3], expected)
        self.assertEqual(self.rows([item])[0][3], expected)
        self.assertNotEqual(self.rows([{**item, 'direction': 'incoming'}])[0][3], expected)

    def test_field_limits_and_raw_data_are_preserved(self):
        item = {'id': 'x' * 501, 'direction': 'd' * 31, 'channel': 'c' * 101,
                'author_name': 'a' * 241, 'body': 'ə' * 12001, 'message_type': 't' * 61,
                'media_url': 'm' * 3001, 'raw': {'media': 'example'}}
        row = self.rows([item])[0]
        self.assertEqual([len(value) for value in row[3:10]], [500, 30, 100, 240, 12000, 60, 3000])
        self.assertEqual(json.loads(row[11]), item['raw'])


class MessageStorageTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'tenant_platform.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'upsert_crm_messages')
        self.connect = MagicMock()
        self.conn = self.connect.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.ns = {'_connect': self.connect, '_ensure_schema': Mock(), '_json': json.dumps, 'uuid': uuid}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<message-storage>', 'exec'), self.ns)

    def save(self, messages, lead=7):
        return self.ns['upsert_crm_messages'](tenant_id='company-a', kommo_lead_id=lead, messages=messages)

    def test_250_messages_use_one_batch_and_one_transaction(self):
        self.assertEqual(self.save([{'id': str(i)} for i in range(250)]), 250)
        self.cur.execute.assert_not_called()
        self.cur.executemany.assert_called_once()
        sql, rows = self.cur.executemany.call_args.args
        self.assertIn('ON CONFLICT (tenant_id, external_id) DO UPDATE', sql)
        self.assertEqual(len(rows), 250)
        self.assertTrue(all(row[1:3] == ('company-a', 7) for row in rows))
        # Conflict must not silently reassign an existing message to another deal.
        self.assertNotIn('kommo_lead_id = EXCLUDED', sql)
        self.conn.commit.assert_called_once()

    def test_empty_batch_does_not_open_database(self):
        self.assertEqual(self.save([]), 0)
        self.connect.assert_not_called()

    def test_invalid_input_fails_before_opening_database(self):
        with self.assertRaises(ValueError):
            self.save([{'id': '1'}, {'id': '2'}], lead='bad')
        self.connect.assert_not_called()

    def test_failed_batch_does_not_commit_or_claim_success(self):
        self.cur.executemany.side_effect = RuntimeError('database error')
        with self.assertRaises(RuntimeError):
            self.save([{'id': '1'}, {'id': '2'}])
        self.conn.commit.assert_not_called()
        self.connect.return_value.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
