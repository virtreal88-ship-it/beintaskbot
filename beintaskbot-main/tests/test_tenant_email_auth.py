import os
import sys
import types
import unittest
from unittest.mock import patch

if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = lambda *args, **kwargs: None
    sys.modules['psycopg'] = psycopg
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg.rows'] = rows

from tenant_email_auth import _email, _hash_password, _password_matches, _phone, _send_mail
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

    def test_resend_https_fallback_after_smtp_failure(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def getcode(self): return 200

        settings = {
            'SMTP_HOST': 'smtp.resend.com', 'SMTP_PORT': '465', 'SMTP_SSL': 'true',
            'SMTP_USERNAME': 'resend', 'SMTP_PASSWORD': 're_test_key',
            'SMTP_FROM': 'Beintaskbot <no-reply@crm.pro.az>',
        }
        with patch.dict(os.environ, settings, clear=False), \
             patch('tenant_email_auth.smtplib.SMTP_SSL', side_effect=OSError('blocked')), \
             patch('tenant_email_auth.urlopen', return_value=Response()) as request:
            _send_mail('owner@example.com', 'Confirm', 'Body')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0].full_url, 'https://api.resend.com/emails')


if __name__ == '__main__':
    unittest.main()
