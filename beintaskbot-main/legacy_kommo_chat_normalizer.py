"""Normalize raw Kommo channel messages into the legacy web-chat contract."""

from __future__ import annotations

from legacy_kommo_chat_message_meta import _chat_author_name, _chat_delivery_status


_helpers: dict[str, object] = {}


def configure_chat_message_normalizer(**helpers) -> None:
    global _helpers
    _helpers = dict(helpers)


def _call(name: str, *args):
    return _helpers[name](*args)


def _format_chat_message(message: dict, origin: str = "") -> dict | None:
    if not isinstance(message, dict):
        return None
    nested = message.get("message") if isinstance(message.get("message"), dict) else {}
    author = message.get("author") if isinstance(message.get("author"), dict) else {}
    if not author:
        author = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    attachment = message.get("attachment") if isinstance(message.get("attachment"), dict) else {}
    created = int(message.get("created_at") or message.get("timestamp") or nested.get("timestamp") or 0)
    media = _call("extract_media_url", attachment) or _call("extract_media_url", nested)
    if not media:
        media = _call("absolute_media_url", str(message.get("media") or message.get("link") or ""))
    file_uuid = (
        _call("extract_file_uuid", attachment)
        or _call("extract_file_uuid", nested)
        or _call("extract_file_uuid", message)
    )
    file_name = str(
        attachment.get("file_name") or nested.get("file_name") or message.get("file_name") or ""
    ).strip()
    message_type = str(
        nested.get("type") or message.get("message_type") or message.get("type") or "text"
    )
    if message_type in {"incoming", "outgoing"}:
        message_type = str(nested.get("type") or "text")
    text = str(nested.get("text") or message.get("text") or "").strip()
    folded = text.casefold()
    if "агенты ии остановлены" in folded or "ai agents have been stopped" in folded:
        return None
    if _call("generic_placeholder", text) and not media and not file_uuid:
        return None
    if message_type in {"call", "call_in", "call_out"} or "call" in message_type:
        missed = any(
            _call("call_is_missed", value, text)
            for value in (nested, message, attachment)
        )
        if missed:
            text = "Buraxılmış zəng"
            message_type = "call_missed"
        else:
            message_type = "audio" if media or file_uuid else message_type
    media_notice = _call("media_notice", text)
    audio_hint = message_type in {"voice", "audio", "ptt"} or _call("looks_audio", file_name)
    if media_notice and audio_hint:
        message_type, text = "audio", "Səs mesajı"
    elif media_notice:
        message_type = "picture"
        text = (file_name or "") if media or file_uuid else "📷 Şəkil"
    elif not text:
        if message_type in {"voice", "audio"} or _call("looks_audio", file_name):
            text = "Səs mesajı"
            message_type = "audio" if message_type not in {"voice", "audio"} else message_type
        elif message_type == "picture" or _call("looks_image", file_name):
            text, message_type = file_name or "Şəkil", "picture"
        elif message_type in {"file", "video", "sticker"}:
            text = file_name or message_type
    elif _call("looks_image", file_name) and message_type in {"text", ""}:
        message_type = "picture"
    direction = str(message.get("type") or message.get("direction") or "")
    incoming = direction == "incoming" or str(author.get("type") or "") == "external"
    if direction not in {"incoming", "outgoing"} and str(author.get("client_id") or ""):
        incoming = True
    author_avatar = _call(
        "absolute_media_url",
        str(author.get("avatar") or author.get("photo") or author.get("icon") or ""),
    )
    if incoming and _call("looks_profile_photo", file_name, message_type):
        message_type = "avatar"
        if text.casefold() in {"şəkil", "sekil", "picture", "sticker", "avatar"}:
            text = ""
        author_avatar = author_avatar or media
    author_type = str(author.get("type") or "").strip().lower()
    raw_author_name = str(author.get("name") or "").strip()
    is_bot = False
    bot_name = ""
    if author_type == "bot" or "salesbot" in raw_author_name.casefold() or "gpt" in raw_author_name.casefold():
        transport_tokens = (
            "whatsapp", "waba", "telegram", "viber", "instagram",
            "facebook", "messenger", "tiktok",
        )
        if not any(token in raw_author_name.casefold() for token in transport_tokens):
            is_bot = True
            bot_name = raw_author_name or "Salesbot"
    if not text and not media and not file_uuid and not author_avatar:
        reaction = nested.get("reaction") or message.get("reaction") or {}
        if isinstance(reaction, dict) and reaction.get("emoji"):
            emoji = str(reaction.get("emoji") or "").strip()
            text = f"💬 Reaksiya: {emoji}" if emoji else "💬 Müştəri reaksiya bildirdi"
            message_type = "reaction"
        elif incoming:
            text, message_type = "💬 Müştəri reaksiya bildirdi", "reaction"
        else:
            return None
    reply_src = nested.get("reply_to") or nested.get("replied_message") or message.get("reply_to")
    reply_id = reply_text = reply_author = ""
    if isinstance(reply_src, dict):
        reply_message = reply_src.get("message") if isinstance(reply_src.get("message"), dict) else reply_src
        reply_id = str(
            reply_message.get("id") or reply_message.get("msgid")
            or reply_src.get("message_id") or reply_src.get("id") or ""
        ).strip()
        reply_text = str(reply_message.get("text") or reply_src.get("text") or "").strip()[:200]
        sender = (
            reply_message.get("author") or reply_message.get("sender")
            or reply_src.get("author") or reply_src.get("sender") or {}
        )
        if isinstance(sender, dict):
            reply_author = str(sender.get("name") or "").strip()[:80]
    elif reply_src:
        reply_id = str(reply_src).strip()
    inline_quote, inline_body = _call("split_quote", text)
    if inline_quote:
        text = inline_body
        reply_text = reply_text or inline_quote
    message_id, msgid, external_id = _call("message_ids", message, nested)
    return {
        "id": message_id or msgid,
        "msgid": msgid,
        "external_id": external_id,
        "direction": "incoming" if incoming else "outgoing",
        "incoming": incoming,
        "author": bot_name if is_bot else _chat_author_name(author, message, incoming),
        "is_bot": is_bot,
        "bot_name": bot_name,
        "text": text,
        "message_type": message_type,
        "created_at": created,
        "created": _call("format_timestamp", created),
        "origin": origin or str(message.get("origin") or ""),
        "channel": _call("origin_channel", origin) or str(origin or "").strip().lower(),
        "reply_to_message_id": reply_id,
        "reply_to_text": reply_text,
        "reply_to_author": reply_author,
        "media_url": "" if message_type == "avatar" else (media if _call("allowed_media", media) else ""),
        "file_uuid": "" if message_type == "avatar" else file_uuid,
        "file_name": file_name,
        "author_avatar": author_avatar,
        "delivery_status": _chat_delivery_status(message, nested, incoming),
    }
