"""Regression coverage for PostgreSQL lock targets in the AI run store."""
import inspect
import re
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = MagicMock()
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows


import tenant_ai_agent_run_store as run_store


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


class AiAgentRunStoreLockTests(unittest.TestCase):
    def assert_nullable_integration_is_not_locked(self, function):
        source = inspect.getsource(function)
        # A platform-key agent does not need a tenant Chat AI integration, so
        # the integration is correctly optional. PostgreSQL rejects `FOR
        # UPDATE OF i` when i is on the nullable side of that LEFT JOIN.
        self.assertIn("LEFT JOIN saas_tenant_integrations i", source)
        lock_targets = re.findall(r"FOR UPDATE OF ([a-z,]+)", source)
        self.assertEqual(lock_targets, ['j,a,s'])
        self.assertNotIn('i', lock_targets[0].split(','))

    def test_reserve_run_never_locks_nullable_chat_ai_integration(self):
        self.assert_nullable_integration_is_not_locked(run_store.reserve_run)

    def test_complete_run_never_locks_nullable_chat_ai_integration(self):
        self.assert_nullable_integration_is_not_locked(run_store.complete_run)

    def test_deferring_reserved_run_supersedes_receipt_before_requeue(self):
        cursor = Cursor(({'id': 'run-a'}, {'ok': 1}))
        connection = Connection(cursor)
        job = {
            'tenant_id': '00000000-0000-0000-0000-000000000001',
            'agent_id': '00000000-0000-0000-0000-000000000002',
            'provider': 'connect', 'conversation_id': 'conversation-a',
            'channel_id': '00000000-0000-0000-0000-000000000003',
            'lease_token': 'lease-a', 'revision': 3, 'event_fingerprint': 'event-a',
        }
        with patch.object(run_store, '_connect', return_value=connection), \
             patch.object(run_store, 'ensure_schema'):
            self.assertTrue(run_store.defer_job(job, '2026-10-17T06:00:00+00:00', {'run_id': 'run-a'}))

        self.assertEqual(len(cursor.executed), 3)
        run_sql, run_params = cursor.executed[0]
        self.assertIn("state='superseded'", run_sql)
        self.assertIn('event_fingerprint=%s', run_sql)
        self.assertEqual(run_params[-2:], ('event-a', 3))
        self.assertIn('RETURNING 1', cursor.executed[1][0])
        self.assertIn('revision=revision+1', cursor.executed[1][0])
        self.assertIn("SET state='queued'", cursor.executed[2][0])
        self.assertTrue(connection.committed)

    def test_deferring_unreserved_job_keeps_its_revision(self):
        cursor = Cursor(({'ok': 1},))
        connection = Connection(cursor)
        job = {
            'tenant_id': '00000000-0000-0000-0000-000000000001',
            'agent_id': '00000000-0000-0000-0000-000000000002',
            'provider': 'connect', 'conversation_id': 'conversation-a',
            'channel_id': '00000000-0000-0000-0000-000000000003',
            'lease_token': 'lease-a', 'revision': 3, 'event_fingerprint': 'event-a',
        }
        with patch.object(run_store, '_connect', return_value=connection), \
             patch.object(run_store, 'ensure_schema'):
            self.assertTrue(run_store.defer_job(job, '2026-10-17T06:00:00+00:00'))

        self.assertEqual(len(cursor.executed), 2)
        self.assertNotIn('revision=revision+1', cursor.executed[0][0])

    def test_stale_reserved_run_does_not_reopen_job_or_event(self):
        cursor = Cursor((None,))
        connection = Connection(cursor)
        job = {
            'tenant_id': '00000000-0000-0000-0000-000000000001',
            'agent_id': '00000000-0000-0000-0000-000000000002',
            'provider': 'connect', 'conversation_id': 'conversation-a',
            'channel_id': '00000000-0000-0000-0000-000000000003',
            'lease_token': 'lease-a', 'revision': 3, 'event_fingerprint': 'event-a',
        }
        with patch.object(run_store, '_connect', return_value=connection), \
             patch.object(run_store, 'ensure_schema'):
            self.assertFalse(run_store.defer_job(job, '2026-10-17T06:00:00+00:00', {'run_id': 'run-a'}))

        self.assertEqual(len(cursor.executed), 1)
        self.assertTrue(connection.committed)


if __name__ == '__main__':
    unittest.main()
