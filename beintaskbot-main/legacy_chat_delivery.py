"""Channel-neutral Kommo Talk delivery for the legacy chat UI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SendMessage = Callable[..., tuple[bool, str, int]]


@dataclass(slots=True)
class KommoDelivery:
    ok: bool
    error: str
    sent_text: str


def deliver_kommo_message(
    *,
    channel: str,
    talk_id: int,
    chat_id: str,
    text: str,
    upload_name: str,
    is_voice: bool,
    attachment: dict[str, Any] | None,
    reply_ids: list[str],
    reply_preview: str,
    voice_caption: str,
    send_message: SendMessage,
    quiet_quote: Callable[[str, str], str],
    visible_quote: Callable[[str, str], str],
) -> KommoDelivery:
    message_text = text
    if attachment and not message_text and not is_voice:
        message_text = upload_name or "Fayl"
    if channel == "whatsapp":
        if is_voice:
            ok, error, _status = send_message(talk_id, "", attachment)
            if ok:
                if text:
                    send_message(talk_id, text)
                return KommoDelivery(True, "", voice_caption)
            return KommoDelivery(False, error or "", message_text)
        if reply_preview:
            message_text = quiet_quote(message_text, reply_preview)
        ok, error, _status = send_message(talk_id, message_text, attachment)
        return KommoDelivery(ok, "" if ok else (error or ""), message_text)

    first_error = ""
    for reply_id in reply_ids[:2]:
        ok, error, _status = send_message(
            talk_id,
            message_text,
            attachment,
            reply_to=reply_id,
            chat_id=chat_id,
            reply_text=reply_preview,
            plain_fallback=False,
        )
        if ok:
            return KommoDelivery(True, "", message_text)
        if error and not first_error:
            first_error = error

    fallback_text = message_text
    if channel in {"tiktok", "telegram", "instagram", "facebook"} and reply_preview:
        fallback_text = visible_quote(message_text, reply_preview)
    ok, error, _status = send_message(talk_id, fallback_text, attachment)
    if ok:
        return KommoDelivery(True, "", fallback_text)
    return KommoDelivery(False, first_error or error or "", fallback_text)
