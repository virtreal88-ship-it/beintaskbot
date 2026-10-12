"""Tests for the legacy action request boundary."""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from legacy_action_request import parse_action_request


class ActionRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_user_and_object_json_are_accepted(self):
        request = SimpleNamespace(
            headers={"X-TG-User-ID": "20"}, json=lambda: _async_value({"action": "info"})
        )
        result = await parse_action_request(request, lambda: {"20": {}})
        self.assertEqual(result.chat_id, 20)
        self.assertEqual(result.data["action"], "info")

    async def test_numeric_telegram_user_keeps_legacy_fallback(self):
        request = SimpleNamespace(
            headers={"X-TG-User-ID": "21"}, json=lambda: _async_value({})
        )
        result = await parse_action_request(request, lambda: {})
        self.assertEqual(result.chat_id, 21)

    async def test_invalid_json_and_missing_identity_are_explicit(self):
        async def broken_json():
            raise ValueError("bad")

        invalid = await parse_action_request(
            SimpleNamespace(headers={}, json=broken_json), lambda: {}
        )
        self.assertEqual((invalid.status, invalid.error), (400, "Invalid JSON"))
        missing = await parse_action_request(
            SimpleNamespace(headers={}, json=lambda: _async_value({})), lambda: {}
        )
        self.assertEqual(missing.status, 403)

    async def test_array_json_is_rejected(self):
        request = SimpleNamespace(headers={"X-TG-User-ID": "20"}, json=lambda: _async_value([]))
        result = await parse_action_request(request, lambda: {"20": {}})
        self.assertEqual(result.status, 400)


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()
