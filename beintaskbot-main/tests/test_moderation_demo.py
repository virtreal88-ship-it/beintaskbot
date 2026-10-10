import hashlib
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import moderation_demo as demo
import moderation_demo_api as api


TEST_PASSWORD = "review-example-only-123"
SALT = "00" * 16
HASH = "pbkdf2_sha256$200000$" + SALT + "$" + hashlib.pbkdf2_hmac(
    "sha256", TEST_PASSWORD.encode(), bytes.fromhex(SALT), 200000).hex()
SETTINGS = {"MODERATION_DEMO_ENABLED": "true", "MODERATION_DEMO_TENANT_ID": "5a1dba60-ea6c-4afd-ab66-79d32795bb87",
            "MODERATION_DEMO_USERNAME": "review-test", "MODERATION_DEMO_PASSWORD_HASH": HASH,
            "MODERATION_DEMO_EXPIRES_AT": "2099-01-01T00:00:00Z"}


def complete_immediate(coroutine):
    """Fixtures do not suspend; no sockets/event loop needed for these cases."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Fixture unexpectedly suspended")


class ModerationDemoTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(demo.config())
            self.assertFalse(demo.identity_allowed(SETTINGS["MODERATION_DEMO_TENANT_ID"], -42))
            self.assertTrue(demo.identity_allowed("production-tenant", 42))

    def test_isolated_identity_and_revocation(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            current = demo.config()
            self.assertLess(current.user_id, 0)
            self.assertTrue(demo.identity_allowed(current.tenant_id, current.user_id))
            self.assertFalse(demo.identity_allowed("another-company", current.user_id))
            self.assertFalse(demo.identity_allowed(current.tenant_id, current.user_id - 1))
            with patch.dict(os.environ, {"MODERATION_DEMO_ENABLED": "false"}):
                self.assertFalse(demo.identity_allowed(current.tenant_id, current.user_id))

    def test_invalid_and_expired_configuration(self):
        for key, value in (("MODERATION_DEMO_TENANT_ID", "invalid"),
                           ("MODERATION_DEMO_EXPIRES_AT", "2000-01-01T00:00:00Z"),
                           ("MODERATION_DEMO_EXPIRES_AT", "2099-01-01T00:00:00"),
                           ("MODERATION_DEMO_USERNAME", "")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {**SETTINGS, key: value}, clear=True):
                self.assertIsNone(demo.config())

    def test_password_validation_fails_closed(self):
        self.assertTrue(demo.password_matches(TEST_PASSWORD, HASH))
        self.assertFalse(demo.password_matches("wrong-password-123", HASH))
        for bad in ("", "plain-password", HASH.replace("200000", "1"), HASH.replace("pbkdf2_sha256", "md5")):
            self.assertFalse(demo.password_matches(TEST_PASSWORD, bad))


class LoginTests(unittest.TestCase):
    def setUp(self):
        api._attempts.clear()
        self.current = demo.DemoConfig(SETTINGS["MODERATION_DEMO_TENANT_ID"], "review-test", HASH, int(time.time()) + 600)
        self.session = Mock(return_value="signed-test-cookie")
        self.login = api.handler(self.session, "demo-cookie", "https://crm.pro.az", Mock())

    def request(self, data, origin="https://crm.pro.az", size=200):
        async def body():
            return data
        return SimpleNamespace(headers={"Origin": origin}, content_type="application/json", content_length=size, json=body)

    def call(self, request, current=True):
        async def threaded(function, *args):
            return function(*args)
        with patch.object(api, "config", return_value=self.current if current else None), \
             patch.object(api.asyncio, "to_thread", threaded), patch.object(api, "provision") as provision:
            response = complete_immediate(self.login(request))
            return response, provision

    def test_disabled_endpoint_does_not_grant_session(self):
        from aiohttp.web import HTTPNotFound
        with self.assertRaises(HTTPNotFound):
            self.call(self.request({}), current=False)
        self.session.assert_not_called()

    def test_bad_origin_body_and_credentials_never_provision(self):
        for request, status in ((self.request({}, origin="https://evil.test"), 403),
                                (self.request({}, size=5000), 400),
                                (self.request([]), 400),
                                (self.request({"username": "wrong", "password": TEST_PASSWORD}), 401)):
            response, provision = self.call(request)
            self.assertEqual(response.status, status)
            provision.assert_not_called()
            self.session.assert_not_called()

    def test_valid_login_only_uses_server_configured_company(self):
        response, provision = self.call(self.request({"username": "review-test", "password": TEST_PASSWORD,
                                                     "tenant_id": "production", "telegram_id": 123}))
        self.assertEqual(response.status, 200)
        provision.assert_called_once_with(self.current)
        self.session.assert_called_once_with(self.current.tenant_id, self.current.user_id)
        cookie = response.cookies["demo-cookie"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertLessEqual(int(cookie["max-age"]), 600)
        self.assertEqual(json.loads(response.text)["redirect"], "/setup")

    def test_attempts_are_bounded(self):
        for _ in range(5):
            self.call(self.request({}))
        response, provision = self.call(self.request({"username": "review-test", "password": TEST_PASSWORD}))
        self.assertEqual(response.status, 429)
        provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
