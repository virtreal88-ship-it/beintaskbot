"""HTTP input parsing for the legacy deal-chat send endpoint."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 2000


class ChatSendInputError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(slots=True)
class ChatSendInput:
    lead_id: int
    text: str
    channel: str
    is_voice: bool
    reply_to_message_id: str
    reply_external_id: str
    reply_preview: str
    reply_author: str
    talk_id: int
    sender_phone: str
    upload_raw: bytes
    upload_name: str
    upload_type: str


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


async def parse_chat_send_input(request: Any) -> ChatSendInput:
    upload_raw = b""
    upload_name = ""
    upload_type = ""
    if str(request.content_type or "").startswith("multipart/"):
        payload = await request.post()
        uploaded = payload.get("file")
        if uploaded is not None and getattr(uploaded, "file", None):
            upload_raw = uploaded.file.read()
            upload_name = str(getattr(uploaded, "filename", None) or "file")
            upload_type = str(
                getattr(uploaded, "content_type", None) or "application/octet-stream"
            )
            if len(upload_raw) > MAX_UPLOAD_BYTES:
                raise ChatSendInputError("Fayl 15MB-dan böyükdür")
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

    result = ChatSendInput(
        lead_id=_as_int(payload.get("lead_id")),
        text=str(payload.get("text") or "").strip(),
        channel=str(payload.get("channel") or "whatsapp").strip().lower(),
        is_voice=_as_bool(payload.get("is_voice")),
        reply_to_message_id=str(payload.get("reply_to_message_id") or "").strip(),
        reply_external_id=str(payload.get("reply_external_id") or "").strip(),
        reply_preview=str(payload.get("reply_to_text") or "").strip()[:200],
        reply_author=str(payload.get("reply_to_author") or "").strip()[:80],
        talk_id=_as_int(payload.get("talk_id")),
        sender_phone=str(payload.get("sender_phone") or ""),
        upload_raw=upload_raw,
        upload_name=upload_name,
        upload_type=upload_type,
    )
    if not result.lead_id:
        raise ChatSendInputError("lead_id required")
    if not result.text and not result.upload_raw:
        raise ChatSendInputError("Mesaj boş ola bilməz")
    if len(result.text) > MAX_TEXT_CHARS:
        raise ChatSendInputError("Mesaj çox uzundur")
    return result
