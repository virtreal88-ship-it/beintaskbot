"""Legacy Kommo pacing without live HTTP calls."""
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from legacy_kommo_transport import install_legacy_kommo_transport, is_kommo_url, token_has_scope


class FakeSession:
    statuses = []
    calls = []

    def __init__(self):
        self.headers = {}
        self.mounts = []

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def request(self, method, url, *args, **kwargs):
        self.calls.append((method, url))
        return SimpleNamespace(status_code=self.statuses.pop(0))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.original_request = FakeSession.request
        FakeSession.calls = []
        FakeSession.statuses = []
        self.requests = SimpleNamespace(
            Session=FakeSession,
            adapters=SimpleNamespace(HTTPAdapter=lambda **kwargs: kwargs),
            get=Mock(return_value=SimpleNamespace(status_code=200)),
        )
        self.logger = Mock()

    def tearDown(self):
        FakeSession.request = self.original_request

    def install(self):
        return install_legacy_kommo_transport(
            token="secret", base_url="https://example.kommo.com",
            logger=self.logger, requests_module=self.requests, probe=False)

    def test_host_matching_is_exact(self):
        self.assertTrue(is_kommo_url("https://example.kommo.com/api/v4/account"))
        self.assertTrue(is_kommo_url("https://sub.amocrm.ru/api/v4/account"))
        self.assertFalse(is_kommo_url("https://kommo.com.attacker.test/api"))
        self.assertFalse(is_kommo_url("https://example.test/?next=kommo.com"))

    def test_scope_reader_is_fail_closed(self):
        payload = base64.urlsafe_b64encode(json.dumps({"scopes": ["messages"]}).encode()).decode().rstrip("=")
        token = f"header.{payload}.signature"
        self.assertTrue(token_has_scope(token, "messages"))
        self.assertFalse(token_has_scope(token, "admin"))
        self.assertFalse(token_has_scope("not-a-token", "messages"))

    def test_429_retries_only_kommo_and_uses_shared_limiter(self):
        runtime = self.install()
        runtime.limiter.wait = Mock()
        runtime.limiter.engage_cooldown = Mock()
        FakeSession.statuses = [429, 200]
        response = FakeSession.request(runtime.session, "GET", "https://example.kommo.com/api/v4/leads")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(runtime.limiter.wait.call_count, 2)
        runtime.limiter.engage_cooldown.assert_called_once_with(2.5)
        self.assertEqual(len(FakeSession.calls), 2)

    def test_non_kommo_request_is_not_delayed_or_retried(self):
        runtime = self.install()
        runtime.limiter.wait = Mock()
        FakeSession.statuses = [429]
        response = FakeSession.request(runtime.session, "GET", "https://example.test/api")
        self.assertEqual(response.status_code, 429)
        runtime.limiter.wait.assert_not_called()
        self.assertEqual(len(FakeSession.calls), 1)
        self.assertEqual(runtime.headers["Authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()
