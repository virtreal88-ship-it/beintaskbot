import sys
import types
import unittest
from pathlib import Path
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
    def __init__(self):
        self.calls = []
        self._fetches = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        self._fetches += 1
        # The first read is the saved company settings; the second is the
        # separate tenant-scoped runtime summary.
        if self._fetches == 1:
            return {
                'company_instructions': '', 'enabled': True,
                'emergency_stop': False, 'auto_send_enabled': True,
                'crm_actions_enabled': False, 'max_replies_per_chat': 20,
                'updated_at': None,
            }
        return {
            'connect_connected': True, 'active_connect_channels': 1,
            'enabled_agents': 1,
            'daily_ai_limit': 50,
            'platform_token_limit': 0,
        }


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


class TenantAiAgentSettingsRuntimeTests(unittest.TestCase):
    def test_runtime_summary_is_scoped_to_the_authorized_tenant(self):
        cursor = Cursor()
        connection = Connection(cursor)
        tenant = '00000000-0000-0000-0000-000000000101'
        with patch.object(store, '_connect', return_value=connection), \
             patch.object(store, 'ensure_schema'), \
             patch('tenant_connect_transport._schema'), \
             patch.object(store, '_authorize', return_value=(tenant, 7)):
            result = store.read_settings({'tenant_id': tenant, 'telegram_id': 7})

        self.assertEqual(result['runtime'], {
            'tenant_id': tenant,
            'connect_connected': True,
            'active_connect_channels': 1,
            'enabled_agents': 1,
            'daily_ai_limit': 50,
            'platform_token_limit': 0,
            'simulation_runtime_enabled': False,
            'simulation_runtime_block_reason': 'global_feature_flag_disabled',
            'support_case_extraction_runtime_enabled': False,
            'support_case_extraction_block_reason': 'server_or_pilot_scope_disabled',
        })
        summary = next(call for call in cursor.calls if 'active_connect_channels' in call[0])
        self.assertEqual(summary[1], (tenant, tenant, tenant, tenant))
        self.assertIn("provider='connect'", summary[0])
        self.assertTrue(connection.committed)

    def test_settings_api_refreshes_saved_state_and_exposes_runtime_flags(self):
        source = Path('tenant_ai_agents_api.py').read_text(encoding='utf-8')
        # GET and PATCH must both expose the same runtime decision, while PATCH
        # reads the committed tenant row again instead of trusting browser data.
        self.assertGreaterEqual(source.count('_attach_runtime_status(result)'), 2)
        self.assertIn("'configured_auto_send_enabled': runtime_configured('AI_AGENTS_AUTO_SEND_ENABLED')", source)
        self.assertIn('await asyncio.to_thread(save_settings, profile, payload)', source)
        self.assertIn('result = await asyncio.to_thread(read_settings, profile)', source)


if __name__ == '__main__':
    unittest.main()
