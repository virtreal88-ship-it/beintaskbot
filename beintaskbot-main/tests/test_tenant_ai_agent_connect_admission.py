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

import tenant_ai_agent_connect as connect_ai


class Cursor:
    def __init__(self, settings):
        self.settings = settings
        self.calls = 0
        self.executed = []

    def execute(self, sql, params=None):
        self.calls += 1
        self.executed.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def fetchone(self):
        return self.settings if self.calls == 1 else {'connected': True}


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


class ConnectAdmissionTests(unittest.TestCase):
    def test_archived_tenant_is_blocked_in_active_and_test_modes(self):
        for mode in ('active', 'test'):
            cursor = Cursor({'enabled': True, 'emergency_stop': False,
                             'operating_mode': mode, 'status': 'archived'})
            with self.subTest(mode=mode), \
                 patch.object(connect_ai, '_connect', return_value=Connection(cursor)), \
                 patch.object(connect_ai, 'ensure_schema'), \
                 patch.object(connect_ai, 'test_allowed', return_value=True), \
                 patch.object(connect_ai, 'runtime_flag', return_value=True):
                self.assertFalse(connect_ai.allowed({
                    'provider': 'connect', 'tenant_id': '00000000-0000-0000-0000-000000000001',
                    'channel_id': '00000000-0000-0000-0000-000000000002',
                    'conversation_id': 'test-conversation',
                }))

    def test_setup_stage_tenants_can_use_connect_but_not_archived_tenants(self):
        for status in ('onboarding', 'ready_for_integration', 'active'):
            cursor = Cursor({'enabled': True, 'emergency_stop': False,
                             'operating_mode': 'active', 'status': status})
            with self.subTest(status=status), \
                 patch.object(connect_ai, '_connect', return_value=Connection(cursor)), \
                 patch.object(connect_ai, 'ensure_schema'), \
                 patch.object(connect_ai, 'runtime_flag', return_value=True):
                self.assertTrue(connect_ai.allowed({
                    'provider': 'connect', 'tenant_id': '00000000-0000-0000-0000-000000000001',
                    'channel_id': '00000000-0000-0000-0000-000000000002',
                    'conversation_id': 'test-conversation',
                }))

    def test_server_ai_agents_flag_blocks_connect_queue_admission(self):
        with patch.object(connect_ai, 'runtime_flag', return_value=False), \
             patch.object(connect_ai, '_connect') as database:
            self.assertFalse(connect_ai.allowed({'provider': 'connect', 'tenant_id': 'tenant-a'}))
        database.assert_not_called()

    def test_server_flag_block_is_recorded_for_diagnostics_instead_of_lost(self):
        cursor = Cursor({'operating_mode': 'active'})
        with patch.object(connect_ai, '_connect', return_value=Connection(cursor)), \
             patch.object(connect_ai, 'ensure_schema'), \
             patch.object(connect_ai, 'allowed', return_value=False), \
             patch.object(connect_ai, 'runtime_flag', return_value=False):
            queued = connect_ai.enqueue(
                '00000000-0000-0000-0000-000000000001', channel_id='00000000-0000-0000-0000-000000000002',
                conversation_id='conversation-a', lead_id=1, message_id='message-a', event_id='event-a',
                text='private message text',
            )
        self.assertEqual(queued, 0)
        self.assertTrue(any("state='skipped'" in sql and params[0] == 'ai_agents_server_disabled'
                            for sql, params in cursor.executed if params))


if __name__ == '__main__':
    unittest.main()
