import unittest
from unittest.mock import Mock, patch

from tenant_bein_crm import BeinCrm, normalize_base_url
from tenant_crm_provider import CrmProviderError


class BeinCrmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = BeinCrm("https://erp.example.test", "bei_int_public.secret-value", "branch-uuid")

    async def test_read_uses_bearer_locale_and_query_branch(self):
        response = Mock(status_code=200, content=b"json", headers={})
        response.json.return_value = {"status": "success", "data": []}
        with patch("tenant_bein_crm.requests.request", return_value=response) as request:
            result = await self.client.list_resource("deals", page=2)
        self.assertEqual(result["data"], [])
        args, kwargs = request.call_args
        self.assertEqual(args, ("GET", "https://erp.example.test/api/v1/crm/leads"))
        self.assertEqual(kwargs["params"], {"page": 2, "filter[branch_id]": "branch-uuid"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer bei_int_public.secret-value")
        self.assertEqual(kwargs["headers"]["X-Locale"], "az")

    async def test_write_requires_stable_key_and_places_branch_in_body(self):
        with self.assertRaises(CrmProviderError):
            await self.client.create("tasks", {"subject": "Call"}, idempotency_key="short")
        response = Mock(status_code=201, content=b"json", headers={})
        response.json.return_value = {"status": "success", "data": {"id": "task-uuid"}}
        with patch("tenant_bein_crm.requests.request", return_value=response) as request:
            await self.client.create("tasks", {"subject": "Call"}, idempotency_key="command-123456")
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Idempotency-Key"], "command-123456")
        self.assertEqual(kwargs["json"], {"subject": "Call", "branch_id": "branch-uuid"})

    async def test_error_keeps_only_safe_provider_diagnostics(self):
        response = Mock(status_code=422, content=b"json", headers={"X-Request-Id": "request-header"})
        response.json.return_value = {"message": "Validation failed", "code": "INVALID", "request_id": "request-body", "token": "secret"}
        with patch("tenant_bein_crm.requests.request", return_value=response):
            with self.assertRaises(CrmProviderError) as caught:
                await self.client.update("deals", "lead-uuid", {"name": "A"}, idempotency_key="command-123456")
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.code, "INVALID")
        self.assertEqual(caught.exception.request_id, "request-body")
        self.assertNotIn("secret", str(caught.exception))

    def test_base_url_and_token_are_restricted(self):
        self.assertEqual(normalize_base_url("https://erp.example.test/api/v1"), "https://erp.example.test/api/v1")
        self.assertNotIn("secret-value", repr(self.client))
        for url in ("http://erp.example.test", "https://user:pass@erp.example.test", "https://erp.example.test?token=x",
                    "https://localhost", "https://127.0.0.1/api/v1"):
            with self.assertRaises(CrmProviderError):
                normalize_base_url(url)
        with self.assertRaises(CrmProviderError):
            BeinCrm("https://erp.example.test", "wrong-token")

    def test_documented_capabilities_do_not_claim_missing_notes_or_chats(self):
        self.assertTrue(self.client.capabilities.deals)
        self.assertTrue(self.client.capabilities.customers)
        self.assertFalse(self.client.capabilities.notes)
        self.assertFalse(self.client.capabilities.chats)


if __name__ == "__main__":
    unittest.main()
