import sys
import types
import unittest
import os
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
        self.events = rows
        self.calls = []
        self.sql = []
        self.params = None
        self.current_sql = ''

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql.append(sql)
        self.params = params
        self.current_sql = sql
        self.calls.append((sql, params))

    def fetchone(self):
        return {'tenant_status': 'active', 'company_ai_enabled': True,
                'company_settings_configured': True, 'emergency_stop': False,
                'auto_send_enabled': False, 'connected_connect_accounts': 1,
                'active_connect_channels': 1}

    def fetchall(self):
        if 'FROM saas_connect_channels' in self.current_sql:
            return [{'channel_id': 'channel-a', 'name': 'WhatsApp A', 'status': 'connected', 'active': True}]
        if 'FROM saas_ai_agents' in self.current_sql:
            return [{'agent_id': 'agent-a', 'name': 'Agent A', 'enabled': True, 'mode': 'automatic',
                     'scopes': [{'provider': 'connect', 'channel_id': 'channel-a'}]}]
        return self.events


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
            'agent_enabled': True, 'agent_mode': 'manual',
            'company_ai_enabled': True, 'auto_send_enabled': False,
            'job_state': 'drafted', 'job_error': '', 'attempts': 1,
            'run_state': 'completed', 'run_updated_at': datetime(2026, 10, 15, tzinfo=timezone.utc),
        }])
        with patch.dict(os.environ, {'AI_AGENTS_ENABLED': 'true', 'AI_AGENTS_AUTO_SEND_ENABLED': 'true', 'STAGING_MODE': 'false'}), \
             patch.object(store, '_connect', return_value=Connection(cursor)), \
             patch.object(store, 'ensure_schema'), \
             patch.object(store, '_authorize', return_value=('tenant-a', 7)):
            result = store.list_connect_diagnostics({'tenant_id': 'tenant-a', 'telegram_id': 7})

        self.assertEqual(cursor.params, ('tenant-a', 50))
        self.assertEqual(result['system']['tenant_id'], 'tenant-a')
        self.assertFalse(result['system']['auto_send_enabled'])
        self.assertEqual(result['channels'][0]['channel_id'], 'channel-a')
        self.assertEqual(result['agents'][0]['connect_scope_channel_ids'], ['channel-a'])
        self.assertTrue(result['runtime']['ai_agents_enabled'])
        self.assertTrue(result['runtime']['auto_send_enabled'])
        tenant_queries = [sql for sql, params in cursor.calls if params and 'tenant-a' in params]
        self.assertGreaterEqual(len(tenant_queries), 4)
        self.assertTrue(all('tenant_id=%s::uuid' in sql or 't.id=%s::uuid' in sql for sql in tenant_queries))
        event_sql = next(sql for sql in cursor.sql if 'FROM saas_crm_messages m' in sql)
        self.assertNotIn('m.body,', event_sql)
        self.assertTrue(result['latest_event_found'])
        self.assertEqual(result['events'][0]['auto_send_block_reason'], 'manual_mode')
        self.assertNotIn('conversation_id', result['events'][0])

    def test_diagnostics_reflect_process_flags_and_staging_override(self):
        cursor = Cursor([])
        with patch.dict(os.environ, {'AI_AGENTS_ENABLED': 'true', 'AI_AGENTS_AUTO_SEND_ENABLED': 'true', 'STAGING_MODE': 'true'}), \
             patch.object(store, '_connect', return_value=Connection(cursor)), \
             patch.object(store, 'ensure_schema'), \
             patch.object(store, '_authorize', return_value=('tenant-a', 7)):
            result = store.list_connect_diagnostics({'tenant_id': 'tenant-a', 'telegram_id': 7})

        self.assertTrue(result['runtime']['ai_agents_enabled'])
        self.assertFalse(result['runtime']['auto_send_enabled'])
        self.assertTrue(result['runtime']['staging_mode'])
        self.assertFalse(result['server_flags']['auto_send_enabled'])


if __name__ == '__main__':
    unittest.main()
