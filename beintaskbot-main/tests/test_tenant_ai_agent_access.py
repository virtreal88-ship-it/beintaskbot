import unittest
import sys
import types
from unittest.mock import MagicMock

if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = MagicMock()
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows

from tenant_ai_agent_access import tenant_status_allows_ai, tenant_status_allows_management
from tenant_ai_agents_store import _authorize
from tenant_linear_policy import TenantLinearError


class TenantAiAgentAccessTests(unittest.TestCase):
    def test_setup_status_is_allowed_for_management_and_connect_only(self):
        for status in ('onboarding', 'ready_for_integration'):
            self.assertTrue(tenant_status_allows_management(status))
            self.assertTrue(tenant_status_allows_ai(status, 'connect'))
            self.assertFalse(tenant_status_allows_ai(status, 'kommo'))

    def test_owner_can_configure_during_onboarding_but_not_after_archival(self):
        class Cursor:
            def __init__(self, row):
                self.row = row

            def execute(self, *_args):
                pass

            def fetchone(self):
                return self.row

        profile = {'tenant_id':'00000000-0000-0000-0000-000000000001', 'telegram_id':7}
        allowed_row = {'role':'owner', 'active':True, 'status':'onboarding'}
        self.assertEqual(_authorize(Cursor(allowed_row), profile), (profile['tenant_id'], 7))
        archived_row = {**allowed_row, 'status':'archived'}
        with self.assertRaises(TenantLinearError):
            _authorize(Cursor(archived_row), profile)

    def test_archived_or_suspended_tenants_remain_blocked(self):
        for status in ('archived', 'suspended', 'inactive', ''):
            self.assertFalse(tenant_status_allows_management(status))
            self.assertFalse(tenant_status_allows_ai(status, 'connect'))
            self.assertFalse(tenant_status_allows_ai(status, 'kommo'))


if __name__ == '__main__':
    unittest.main()
