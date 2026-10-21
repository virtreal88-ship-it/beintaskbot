import os
import sys
import types
import unittest
from unittest.mock import patch

import requests

if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = lambda *args, **kwargs: None
    sys.modules['psycopg'] = psycopg
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg.rows'] = rows

from tenant_email_auth import _email, _login_identity, _hash_password, _password_matches, _phone, _send_mail
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

    def test_login_accepts_email_or_safe_username(self):
        self.assertEqual(_login_identity(' User.Name '),'user.name')
        self.assertEqual(_login_identity(' Staff@Example.com '),'staff@example.com')
        with self.assertRaises(TenantPlatformError): _login_identity('bad username')

    def test_resend_https_fallback_after_smtp_failure(self):
        class Response:
            def raise_for_status(self): return None

        settings = {
            'SMTP_HOST': 'smtp.resend.com', 'SMTP_PORT': '465', 'SMTP_SSL': 'true',
            'SMTP_USERNAME': 'resend', 'SMTP_PASSWORD': 're_test_key',
            'SMTP_FROM': 'Beintaskbot <no-reply@crm.pro.az>',
        }
        with patch.dict(os.environ, settings, clear=False), \
             patch('tenant_email_auth.smtplib.SMTP_SSL', side_effect=OSError('blocked')), \
             patch('tenant_email_auth.requests.post', return_value=Response()) as request:
            _send_mail('owner@example.com', 'Confirm', 'Body')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], 'https://api.resend.com/emails')

    def test_resend_rejection_has_safe_status(self):
        settings = {
            'SMTP_HOST': 'smtp.resend.com', 'SMTP_PORT': '465', 'SMTP_SSL': 'true',
            'SMTP_USERNAME': 'resend', 'SMTP_PASSWORD': 're_test_key',
            'SMTP_FROM': 'Beintaskbot <no-reply@crm.pro.az>',
        }
        response = requests.Response(); response.status_code = 403
        rejection = requests.HTTPError(response=response)
        with patch.dict(os.environ, settings, clear=False), \
             patch('tenant_email_auth.smtplib.SMTP_SSL', side_effect=OSError('blocked')), \
             patch('tenant_email_auth.requests.post', side_effect=rejection), \
             self.assertRaises(TenantPlatformError) as raised:
            _send_mail('owner@example.com', 'Confirm', 'Body')
        self.assertIn('Resend HTTP 403', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
