import unittest
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

# The mapping tests mock the database connection. Keep them runnable in local
# test environments that omit the production PostgreSQL driver.
try:
    import psycopg  # noqa: F401
except ModuleNotFoundError as error:
    if error.name != 'psycopg':
        raise
    psycopg_stub = ModuleType('psycopg')
    psycopg_stub.connect = MagicMock()
    rows_stub = ModuleType('psycopg.rows')
    rows_stub.dict_row = object()
    sys.modules['psycopg'] = psycopg_stub
    sys.modules['psycopg.rows'] = rows_stub

from tenant_member_store import tenants_for_kommo_account_user


class TenantKommoAccountMappingTests(unittest.TestCase):
    @patch('tenant_member_store._ensure_schema')
    @patch('tenant_member_store._connect')
    def test_lookup_requires_active_membership_and_exact_connected_account(self, connect, ensure_schema):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{
            'id': 'tenant-a', 'status': 'active', 'name': 'Company',
            'modules': {}, 'notification_rules': {}, 'onboarding': {},
        }]
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connect.return_value.__enter__.return_value = connection

        result = tenants_for_kommo_account_user(7, ' TEXNIKIDESTEK50.KOMMO.COM ', '32524359')

        self.assertEqual([row['id'] for row in result], ['tenant-a'])
        ensure_schema.assert_called_once_with(connection)
        sql, params = cursor.execute.call_args.args
        self.assertIn("m.active = TRUE", sql)
        self.assertIn("t.status = 'active'", sql)
        self.assertIn("i.provider = 'kommo' AND i.status = 'connected'", sql)
        self.assertIn("lower(i.account_domain) = %s", sql)
        self.assertIn("i.metadata->>'credential_source' IS DISTINCT FROM 'legacy_environment'", sql)
        self.assertEqual(params, (7, 'texnikidestek50.kommo.com', '32524359', '32524359'))

    @patch('tenant_member_store._connect')
    def test_empty_account_never_queries_database(self, connect):
        self.assertEqual(tenants_for_kommo_account_user(7, '  '), [])
        connect.assert_not_called()

if __name__ == '__main__':
    unittest.main()
