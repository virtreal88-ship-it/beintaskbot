from io import BytesIO
import unittest

from legacy_chat_send_request import (
    ChatSendInputError,
    MAX_UPLOAD_BYTES,
    parse_chat_send_input,
)


class _Request:
    def __init__(self, payload, content_type="application/json"):
        self.payload = payload
        self.content_type = content_type

    async def json(self):
        return self.payload

    async def post(self):
        return self.payload


class _Upload:
    def __init__(self, content: bytes, name="photo.jpg", content_type="image/jpeg"):
        self.file = BytesIO(content)
        self.filename = name
        self.content_type = content_type


class LegacyChatSendRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_input_is_normalized(self):
        result = await parse_chat_send_input(_Request({
            "lead_id": "17",
            "text": "  Salam  ",
            "channel": " Instagram ",
            "talk_id": "8",
            "reply_to_text": "x" * 250,
        }))
        self.assertEqual(result.lead_id, 17)
        self.assertEqual(result.text, "Salam")
        self.assertEqual(result.channel, "instagram")
        self.assertEqual(result.talk_id, 8)
        self.assertEqual(len(result.reply_preview), 200)

    async def test_empty_message_and_missing_lead_are_rejected(self):
        with self.assertRaisesRegex(ChatSendInputError, "lead_id required"):
            await parse_chat_send_input(_Request({"text": "Salam"}))
        with self.assertRaisesRegex(ChatSendInputError, "Mesaj boş"):
            await parse_chat_send_input(_Request({"lead_id": 1, "text": ""}))

    async def test_multipart_file_is_read_and_limited(self):
        result = await parse_chat_send_input(_Request(
            {"lead_id": "2", "file": _Upload(b"image")},
            content_type="multipart/form-data",
        ))
        self.assertEqual(result.upload_raw, b"image")
        self.assertEqual(result.upload_name, "photo.jpg")
        with self.assertRaisesRegex(ChatSendInputError, "15MB"):
            await parse_chat_send_input(_Request(
                {"lead_id": "2", "file": _Upload(b"x" * (MAX_UPLOAD_BYTES + 1))},
                content_type="multipart/form-data",
            ))


if __name__ == "__main__":
    unittest.main()
