import sys
import types
import unittest

if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = lambda *args, **kwargs: None
    sys.modules['psycopg'] = psycopg
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg.rows'] = rows

from tenant_email_auth import _email, _hash_password, _password_matches, _phone
from tenant_platform_core import TenantPlatformError


class EmailAuthValidationTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self):
        first = _hash_password('a secure passphrase')
        second = _hash_password('a secure passphrase')
        self.assertNotEqual(first, second)
        self.assertTrue(_password_matches('a secure passphrase', first))
        self.assertFalse(_password_matches('wrong passphrase', first))

    def test_normalizes_email_and_phone(self):
        self.assertEqual(_email(' Owner@Example.COM '), 'owner@example.com')
        self.assertEqual(_phone('00 994 (50) 207-22-40'), '+994502072240')

    def test_rejects_invalid_contact_details(self):
        with self.assertRaises(TenantPlatformError):
            _email('not-an-email')
        with self.assertRaises(TenantPlatformError):
            _phone('123')


if __name__ == '__main__':
    unittest.main()
