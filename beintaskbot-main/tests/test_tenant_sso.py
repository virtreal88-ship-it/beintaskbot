import base64
import hashlib
import unittest
from unittest.mock import MagicMock, patch

from tenant_sso import _pkce, exchange_code


class SsoPkceTests(unittest.TestCase):
    def test_s256_challenge_matches_rfc_encoding(self):
        verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        self.assertEqual(_pkce(verifier), expected)

    @patch('tenant_sso._redirect_uri', return_value='https://connect.example/admin')
    @patch('tenant_sso._client_secret', return_value='x' * 40)
    @patch('tenant_sso._pkce', return_value='challenge')
    @patch('tenant_sso._access', return_value='owner')
    @patch('tenant_sso.ensure_schema')
    @patch('tenant_sso._ensure_schema')
    @patch('tenant_sso._connect')
    def test_exchange_returns_only_verified_active_email(self, connect, _ensure, _schema, _access, _pkce_fn, _secret, _redirect):
        conn = MagicMock()
        cursor_context = MagicMock()
        cursor = MagicMock()
        cursor_context.__enter__.return_value = cursor
        conn.__enter__.return_value = conn
        conn.cursor.return_value = cursor_context
        cursor.fetchone.side_effect = [
            {'id': 'oauth-code', 'actor_id': 42, 'tenant_id': '00000000-0000-0000-0000-000000000001', 'role': 'owner', 'pkce_challenge': 'challenge'},
            {'email': 'Owner@Example.com'},
        ]
        connect.return_value = conn

        claims = exchange_code(code='one-time-code', verifier='v' * 43, client_id='beintaskbot-connect',
                              redirect_uri='https://connect.example/admin', client_secret='x' * 40)

        self.assertEqual(claims['email'], 'owner@example.com')
        self.assertEqual(claims['actor_id'], 42)
