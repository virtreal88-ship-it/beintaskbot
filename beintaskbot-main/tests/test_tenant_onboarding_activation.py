"""Onboarding/OAuth lifecycle guards without a live database."""
import ast
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock


class OnboardingActivation(unittest.TestCase):
    def setUp(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'tenant_platform.py').read_text(encoding='utf-8-sig'))
        names = {'finalize_onboarding', 'save_kommo_oauth_tokens'}
        code = compile(ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), '<activation>', 'exec')
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        connect = MagicMock()
        connect.return_value.__enter__.return_value = self.conn
        self.ns = {'member': Mock(return_value={'role': 'owner'}), '_connect': connect,
                   '_ensure_schema': Mock(), '_tenant_payload': lambda row: row,
                   'TenantPlatformError': ValueError, '_kommo_domain': lambda value: value,
                   '_fernet': MagicMock(), '_json': json.dumps, 'datetime': datetime,
                   'timezone': timezone, 'timedelta': timedelta}
        exec(code, self.ns)

    def test_finalize_requires_connected_same_tenant_and_preserves_disabled_status(self):
        self.ns['finalize_onboarding'](tenant_id='company-a', owner_id=1)
        sql, params = self.cur.execute.call_args.args
        self.assertIn("t.status IN ('onboarding', 'ready_for_integration')", sql)
        self.assertIn("i.tenant_id=t.id AND i.provider='kommo' AND i.status='connected'", sql)
        self.assertIn("THEN 'active' ELSE 'ready_for_integration'", sql)
        self.assertIn('ELSE t.status END', sql)
        self.assertEqual(params, ('company-a',))

    def test_nonowner_cannot_activate(self):
        self.ns['member'].return_value = {'role': 'worker'}
        with self.assertRaises(ValueError):
            self.ns['finalize_onboarding'](tenant_id='company-a', owner_id=1)
        self.conn.commit.assert_not_called()

    def test_successful_oauth_activates_only_completed_workspace_in_same_transaction(self):
        self.cur.fetchone.return_value = {'provider': 'kommo'}
        self.ns['save_kommo_oauth_tokens'](tenant_id='company-a', account_domain='test.kommo.com',
                                         token_payload={'access_token': 'test', 'refresh_token': 'test'})
        sql, params = self.cur.execute.call_args.args
        self.assertIn("status='ready_for_integration'", sql)
        self.assertEqual(params, ('company-a',))
        self.conn.commit.assert_called_once()

    def test_missing_oauth_row_does_not_activate(self):
        self.cur.fetchone.return_value = None
        with self.assertRaises(ValueError):
            self.ns['save_kommo_oauth_tokens'](tenant_id='company-a', account_domain='test.kommo.com',
                                             token_payload={'access_token': 'test', 'refresh_token': 'test'})
        self.assertEqual(self.cur.execute.call_count, 1)
