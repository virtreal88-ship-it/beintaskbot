import sys
import types
import unittest
from unittest.mock import MagicMock, patch

try:
    import psycopg
    import psycopg.rows
except ImportError:
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
        self.current_sql = ''
        self.executed = []

    def execute(self, sql, params=None):
        self.current_sql = sql
        self.executed.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def fetchone(self):
        sql = self.current_sql
        if 'FROM saas_ai_agent_settings' in sql:
            return self.settings
        if 'saas_connect_identity_conflicts' in sql:
            return None
        if 'saas_connect_conversations route' in sql:
            return {'connected': True}
        if 'FROM saas_ai_conversation_state cs' in sql:
            return {'agent_id':'00000000-0000-0000-0000-000000000003','revision':1,'version':1,'name':'Assigned'}
        if 'to_regclass' in sql:
            return {'present': None}
        return None

    def fetchall(self):
        # Dialogue-lock guards read any legacy row set; this fixture has none.
        return []


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
    def test_archived_tenant_is_blocked_even_when_send_permissions_are_enabled(self):
            cursor = Cursor({'enabled': True, 'auto_send_enabled': True, 'emergency_stop': False,
                             'status': 'archived'})
            with \
                 patch.object(connect_ai, '_connect', return_value=Connection(cursor)), \
                 patch.object(connect_ai, 'ensure_schema'), \
                 patch.object(connect_ai, 'runtime_flag', return_value=True):
                self.assertFalse(connect_ai.allowed({
                    'provider': 'connect', 'tenant_id': '00000000-0000-0000-0000-000000000001',
                    'channel_id': '00000000-0000-0000-0000-000000000002',
                    'conversation_id': 'test-conversation',
                }))

    def test_company_manual_and_simulation_modes_cannot_enter_active_send_admission(self):
        for mode in ('manual','simulation','test'):
            cursor=Cursor({'enabled':True,'operating_mode':mode,'auto_send_enabled':True,
                           'emergency_stop':False,'status':'active'})
            with self.subTest(mode=mode), \
                 patch.object(connect_ai,'_connect',return_value=Connection(cursor)), \
                 patch.object(connect_ai,'ensure_schema'), \
                 patch.object(connect_ai,'runtime_flag',return_value=True):
                self.assertFalse(connect_ai.allowed({
                    'provider':'connect','tenant_id':'00000000-0000-0000-0000-000000000001',
                    'channel_id':'00000000-0000-0000-0000-000000000002','conversation_id':'test-conversation'}))

    def test_setup_stage_tenants_can_use_connect_but_not_archived_tenants(self):
        for status in ('onboarding', 'ready_for_integration', 'active'):
            cursor = Cursor({'enabled': True, 'auto_send_enabled': True, 'emergency_stop': False,
                             'status': status})
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
        class BlockedEventCursor(Cursor):
            def fetchone(self):
                if 'INSERT INTO saas_tenant_webhook_events' in self.current_sql:
                    return {'fingerprint':'synthetic-fingerprint'}
                return super().fetchone()
        cursor = BlockedEventCursor({'auto_send_enabled': False})
        with patch.object(connect_ai, '_connect', return_value=Connection(cursor)), \
             patch.object(connect_ai, 'ensure_schema'), \
             patch.object(connect_ai, 'allowed', return_value=False), \
             patch.object(connect_ai, 'runtime_flag', return_value=False), \
             patch.object(connect_ai, 'reconcile_unassigned_connect_dialog'):
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
