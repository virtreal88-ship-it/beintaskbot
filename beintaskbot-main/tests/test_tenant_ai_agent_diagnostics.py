import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = MagicMock()
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows

import tenant_ai_agents_store as store


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []
        self.params = None
        self.fetchone_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql.append(sql)
        self.params = params

    def fetchone(self):
        self.fetchone_calls += 1
        return {'tenant_status': 'active', 'company_ai_enabled': True,
                'operating_mode': 'test', 'emergency_stop': False,
                'auto_send_enabled': False, 'connected_connect_accounts': 1,
                'active_connect_channels': 1}

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class AiAgentDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_are_tenant_scoped_and_explain_safe_draft_gates(self):
        cursor = Cursor([{
            'received_at': datetime(2026, 10, 15, tzinfo=timezone.utc), 'event_state': 'completed',
            'event_error': '', 'channel_id': 'channel-a', 'agent_name': 'Agent A',
            'agent_enabled': True, 'agent_mode': 'manual', 'operating_mode': 'test',
            'company_ai_enabled': True, 'auto_send_enabled': False,
            'job_state': 'drafted', 'job_error': '', 'attempts': 1,
            'run_state': 'completed', 'run_updated_at': datetime(2026, 10, 15, tzinfo=timezone.utc),
        }])
        with patch.object(store, '_connect', return_value=Connection(cursor)), \
             patch.object(store, 'ensure_schema'), \
             patch.object(store, '_authorize', return_value=('tenant-a', 7)):
            result = store.list_connect_diagnostics({'tenant_id': 'tenant-a', 'telegram_id': 7})

        self.assertEqual(cursor.params, ('tenant-a', 50))
        self.assertIn('m.tenant_id=%s::uuid', cursor.sql[1])
        self.assertNotIn('m.body,', cursor.sql[1])
        self.assertTrue(result['latest_event_found'])
        self.assertEqual(result['events'][0]['auto_send_block_reason'], 'manual_mode')
        self.assertNotIn('conversation_id', result['events'][0])


if __name__ == '__main__':
    unittest.main()
