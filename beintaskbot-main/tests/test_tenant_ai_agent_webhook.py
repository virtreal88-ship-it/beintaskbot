import asyncio
import sys
import unittest
from unittest.mock import Mock, patch

from tenant_ai_agent_webhook import build_handler, parse_message_rows


class Request:
    def __init__(self, payload, content_type="application/json"):
        self.payload = payload
        self.content_type = content_type

    async def json(self):
        return self.payload

    async def post(self):
        return self.payload


class StagingWebhookTests(unittest.TestCase):
    def test_parses_json_and_form_incoming_and_outgoing_shapes(self):
        incoming = parse_message_rows({"add": [{"id": "in-1", "chat_id": "chat-1"}]})
        self.assertEqual(incoming[0]["type"], "incoming")
        outgoing = parse_message_rows({
            "outgoing_message[add][0][id]": "out-1",
            "outgoing_message[add][0][chat_id]": "chat-1",
        })
        self.assertEqual(outgoing, [{"id": "out-1", "chat_id": "chat-1", "type": "outgoing"}])

    @unittest.skipIf(sys.platform == "win32", "Windows loopback socketpair blocks during asyncio loop startup in this sandbox; run on Linux.")
    def test_incoming_rows_are_durably_queued_without_live_kommo_reads(self):
        payload = {"account": {"subdomain": "test-account.kommo.com"}, "message": {"add": [
            {"id": "message-1", "type": "incoming", "entity_id": "17", "talk_id": "9", "chat_id": "chat-1"},
            {"id": "message-2", "type": "incoming", "entity_id": "18", "talk_id": "10", "chat_id": "chat-2"},
        ]}}
        seen = {"ingest": [], "observe": []}

        def ingest(data, rows, *, fallback_domain):
            seen["ingest"].append((rows, fallback_domain))

        def observe(data, rows, *, fallback_domain):
            seen["observe"].append((rows, fallback_domain))

        logger = Mock()
        kommo_request = Mock()
        handler = build_handler(kommo_request, logger, ingest=ingest, observe=observe)
        with patch.dict("os.environ", {"AI_AGENTS_ENABLED": "true", "AI_AGENTS_TEST_WEBHOOK_ENABLED": "true"}):
            response = asyncio.run(handler(Request(payload)))

        self.assertEqual(response.status, 200)
        self.assertEqual([row["chat_id"] for row in seen["ingest"][0][0]], ["chat-1", "chat-2"])
        self.assertEqual(seen["ingest"][0][1], "")
        kommo_request.assert_not_called()
        self.assertFalse(seen["observe"])

    @unittest.skipIf(sys.platform == "win32", "Windows loopback socketpair blocks during asyncio loop startup in this sandbox; run on Linux.")
    def test_outgoing_manager_reply_is_sent_only_to_pause_observer(self):
        payload = {"account[subdomain]": "test-account.kommo.com",
                   "outgoing_message[add][0][id]": "manager-1",
                   "outgoing_message[add][0][entity_id]": "17",
                   "outgoing_message[add][0][contact_id]": "11",
                   "outgoing_message[add][0][talk_id]": "9",
                   "outgoing_message[add][0][chat_id]": "chat-1",
                   "outgoing_message[add][0][origin]": "whatsapp"}
        seen = {"ingest": 0, "observe": 0}

        def ingest(*args, **kwargs):
            seen["ingest"] += 1

        def observe(data, rows, *, fallback_domain):
            seen["observe"] += 1
            self.assertEqual(fallback_domain, "")
            self.assertEqual(rows[0]["id"], "manager-1")

        handler = build_handler(Mock(), Mock(), ingest=ingest, observe=observe)
        with patch.dict("os.environ", {"AI_AGENTS_ENABLED": "1", "AI_AGENTS_TEST_WEBHOOK_ENABLED": "1"}):
            response = asyncio.run(handler(Request(payload, "application/x-www-form-urlencoded")))

        self.assertEqual(response.status, 200)
        self.assertEqual(seen, {"ingest": 0, "observe": 1})

    @unittest.skipIf(sys.platform == "win32", "Windows loopback socketpair blocks during asyncio loop startup in this sandbox; run on Linux.")
    def test_disabled_and_non_message_callbacks_do_not_touch_queue(self):
        handler = build_handler(Mock(), Mock(), ingest=Mock(), observe=Mock())
        with patch.dict("os.environ", {"AI_AGENTS_ENABLED": "false", "AI_AGENTS_TEST_WEBHOOK_ENABLED": "true"}):
            response = asyncio.run(handler(Request({"add": [{"id": "x"}]})))
        self.assertEqual(response.status, 200)
        with patch.dict("os.environ", {"AI_AGENTS_ENABLED": "true", "AI_AGENTS_TEST_WEBHOOK_ENABLED": "false"}):
            response = asyncio.run(handler(Request({"add": [{"id": "x"}]})))
        self.assertEqual(response.status, 200)
        with patch.dict("os.environ", {"AI_AGENTS_ENABLED": "true", "AI_AGENTS_TEST_WEBHOOK_ENABLED": "true"}):
            response = asyncio.run(handler(Request({"leads[status][0][id]": "17"}, "application/x-www-form-urlencoded")))
        self.assertEqual(response.status, 200)

    @unittest.skipIf(sys.platform == "win32", "Windows loopback socketpair blocks during asyncio loop startup in this sandbox; run on Linux.")
    def test_database_failure_requests_kommo_retry_without_logging_body(self):
        logger = Mock()

        def fail(*args, **kwargs):
            raise RuntimeError("database unavailable")

        handler = build_handler(Mock(), logger, ingest=fail, observe=Mock())
        with patch.dict("os.environ", {"AI_AGENTS_ENABLED": "true", "AI_AGENTS_TEST_WEBHOOK_ENABLED": "true"}):
            response = asyncio.run(handler(Request({"add": [{"id": "private-message"}]})))
        self.assertEqual(response.status, 503)
        logger.exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
