"""Pure cursor validation: no provider calls or live database access."""
import base64
import json
from datetime import datetime, timezone
import unittest
import uuid

from tenant_approval_cursor import approval_page_size, decode_approval_cursor, encode_approval_cursor


class ApprovalCursorTests(unittest.TestCase):
    def setUp(self):
        self.row = {'created_at': datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc),
                    'actor_id': 20, 'request_id': uuid.UUID(int=1)}

    def test_roundtrip_keeps_stable_order_tuple_and_company(self):
        cursor = encode_approval_cursor(self.row, 'company-a')
        self.assertEqual(decode_approval_cursor(cursor, 'company-a'),
                         (self.row['created_at'], 20, str(self.row['request_id'])))
        self.assertIsNone(decode_approval_cursor('', 'company-a'))
        with self.assertRaises(ValueError):
            decode_approval_cursor(cursor, 'company-b')

    def test_invalid_bounded_cursors_are_rejected(self):
        for cursor in ('garbage!', 'a' * 1025, 'W10', 'e30', None, 123):
            if cursor is None:
                continue  # Empty query means the first page.
            with self.subTest(cursor=str(cursor)[:20]), self.assertRaises(ValueError):
                decode_approval_cursor(cursor, 'company-a')

    def test_bad_date_actor_uuid_and_version_are_rejected(self):
        valid = {'v': 1, 'tenant': 'company-a', 'created_at': self.row['created_at'].isoformat(),
                 'actor_id': 20, 'request_id': str(self.row['request_id'])}
        for patch in ({'v': 2}, {'v': True}, {'actor_id': True}, {'actor_id': -1}, {'actor_id': 2**63},
                      {'created_at': '2026-10-07'}, {'created_at': 1}, {'request_id': 'invalid'}, {'request_id': 123}):
            cursor = base64.urlsafe_b64encode(json.dumps({**valid, **patch}).encode()).decode()
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                decode_approval_cursor(cursor, 'company-a')

    def test_page_size_never_allows_unbounded_query(self):
        self.assertEqual(approval_page_size(), 50)
        self.assertEqual(approval_page_size('200'), 200)
        for value in (0, -1, 201, 'all', None, True, float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                approval_page_size(value)
