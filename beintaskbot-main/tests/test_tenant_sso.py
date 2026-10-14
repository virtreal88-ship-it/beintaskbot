import base64
import hashlib
import unittest

from tenant_sso import _pkce


class SsoPkceTests(unittest.TestCase):
    def test_s256_challenge_matches_rfc_encoding(self):
        verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        self.assertEqual(_pkce(verifier), expected)
