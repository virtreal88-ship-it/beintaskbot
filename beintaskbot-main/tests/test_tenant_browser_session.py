import hashlib
import hmac
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from tenant_browser_session import TenantBrowserSession, COOKIE_NAME, TTL_SECONDS

TENANT = "11111111-1111-1111-1111-111111111111"


class StoreError(Exception):
    pass


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.secret = "test-only"
        self.member = Mock(return_value={"role": "owner"})
        self.demo = Mock(return_value=False)
        self.session = TenantBrowserSession(lambda: self.secret, self.member,
            self.demo, StoreError, clock=lambda: self.now)

    def request(self, token=None):
        return SimpleNamespace(cookies={COOKIE_NAME: token or self.session.issue(TENANT, 42)})

    def test_legacy_wire_format(self):
        payload = f"{TENANT}.42.{1000 + TTL_SECONDS}"
        key = hashlib.sha256(b"tenant-session:test-only").digest()
        token = payload + "." + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(self.session.issue(TENANT, 42), token)
        self.assertEqual(self.session.identity(self.request(token)), (TENANT, 42))

    def test_cookie_survives_new_instance(self):
        request = self.request()
        restarted = TenantBrowserSession(lambda: self.secret, self.member, self.demo, StoreError,
            clock=lambda: self.now)
        self.assertEqual(restarted.identity(request), (TENANT, 42))

    def test_tampering_missing_malformed_and_invalid_uuid(self):
        token = self.session.issue(TENANT, 42)
        for raw in ("", "junk", token.replace(".42.", ".43."), token[:-1],
                    self.session.issue("not-a-uuid", 42)):
            with self.subTest(raw=raw):
                self.assertIsNone(self.session.identity(SimpleNamespace(cookies={COOKIE_NAME: raw})))
        self.assertIsNone(self.session.identity(SimpleNamespace(cookies={})))

    def test_expiry_boundary_and_key_rotation(self):
        request = self.request()
        self.now += TTL_SECONDS
        self.assertIsNotNone(self.session.identity(request))
        self.now += 1
        self.assertIsNone(self.session.identity(request))
        self.now = 1000
        self.secret = "rotated"
        self.assertIsNone(self.session.identity(request))

    def test_demo_is_tenant_scoped_and_zero_never_authorized(self):
        request = self.request(self.session.issue(TENANT, -42))
        self.assertIsNone(self.session.identity(request))
        self.demo.assert_called_with(TENANT, -42)
        self.demo.return_value = True
        self.assertEqual(self.session.identity(request), (TENANT, -42))
        self.assertIsNone(self.session.identity(self.request(self.session.issue(TENANT, 0))))

    def test_membership_revocation_and_owner_only_are_live(self):
        request = self.request()
        self.assertEqual(self.session.profile(request, owner_only=True), {"role": "owner"})
        self.member.return_value = {"role": "employee"}
        self.assertIsNone(self.session.profile(request, owner_only=True))
        self.assertEqual(self.session.profile(request), {"role": "employee"})
        self.member.return_value = None
        self.assertIsNone(self.session.profile(request))
        self.assertEqual(self.member.call_count, 4)

    def test_store_failures_and_unexpected_errors(self):
        self.member.side_effect = StoreError("unavailable")
        self.assertIsNone(self.session.profile(self.request()))
        self.member.side_effect = RuntimeError("programming error")
        with self.assertRaises(RuntimeError):
            self.session.profile(self.request())
        self.member.reset_mock()
        self.assertIsNone(self.session.profile(SimpleNamespace(cookies={})))
        self.member.assert_not_called()
