"""Normalize legacy Kommo notes for the CRM conversation timeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from legacy_kommo_chat_channels import _note_channel_key, _note_is_incoming
from legacy_kommo_media_parser import (
    _extract_file_uuid,
    _extract_media_url,
    _extract_nested_text,
    _is_media_notice_text,
    _looks_audio_name,
    _looks_image_name,
)
from legacy_runtime_config import BAKU_TZ


_users: dict[int, str] = {}
_first_msgid: Callable[..., str] = lambda *_values: ""


def configure_note_formatter(*, users: dict, first_msgid: Callable[..., str]) -> None:
    global _users, _first_msgid
    _users = dict(users or {})
    _first_msgid = first_msgid


def _deal_fmt_ts(ts) -> str:
    try:
        value = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M")


def _call_is_missed(blob: dict | None, text: str = "") -> bool:
    params = blob if isinstance(blob, dict) else {}
    status = " ".join(
        str(params.get(key) or "")
        for key in ("call_status", "status", "result", "state", "outcome")
    ).lower()
    folded = f"{status} {text}".lower()
    if any(token in folded for token in (
        "missed", "no_answer", "noanswer", "unanswered", "cancel", "busy",
        "пропущ", "buraxılmış", "cavabsız",
    )):
        return True
    if "duration" not in params and "duration_sec" not in params:
        return False
    raw = params.get("duration", params.get("duration_sec"))
    try:
        seconds = int(raw or 0)
    except (TypeError, ValueError):
        seconds = 0
    return seconds <= 0


def _is_chat_note_type(note_type: str, file_name: str = "", message_type: str = "") -> bool:
    ntype = str(note_type or "").casefold()
    combined = f"{ntype} {file_name} {message_type}".casefold()
    if ntype in {
        "sms_in", "sms_out", "amomail_message", "facebook_message",
        "instagram_business", "chat", "whatsapp", "telegram", "viber", "waba",
        "call_in", "call_out", "call_missed",
    }:
        return True
    if ntype in {"attachment", "file"}:
        return (
            _looks_audio_name(file_name)
            or _looks_image_name(file_name)
            or message_type in {"voice", "audio", "picture", "video"}
        )
    if message_type in {"voice", "audio", "picture", "video", "file", "sticker"}:
        return True
    if _looks_audio_name(file_name) or _looks_audio_name(combined) or _looks_image_name(file_name):
        return True
    return "message" in ntype or ntype.startswith("sms")


def _format_deal_note(note: dict, entity_type: str = "leads") -> dict | None:
    if not isinstance(note, dict):
        return None
    ntype = str(note.get("note_type") or "common")
    params = note.get("params") if isinstance(note.get("params"), dict) else {}
    created = int(note.get("created_at") or 0)
    file_name = str(params.get("file_name") or params.get("original_name") or "").strip()
    file_uuid = _extract_file_uuid(params)
    media = _extract_media_url(params)
    text = ""
    message_type = "text"
    if ntype == "common":
        text = str(params.get("text") or "").strip()
    elif ntype in {"call_in", "call_out"}:
        label = "Gələn zəng" if ntype == "call_in" else "Gedən zəng"
        phone = str(params.get("phone") or params.get("uniq") or "").strip()
        duration = params.get("duration")
        text = f"{label} {phone}".strip()
        if duration:
            text += f" ({duration}s)"
        if _call_is_missed(params, text):
            text = "Buraxılmış zəng"
            message_type = "call_missed"
        else:
            message_type = "audio"
    elif ntype in {"attachment", "file"}:
        text = _extract_nested_text(params) or file_name or "Fayl"
        if _looks_audio_name(file_name):
            message_type = "audio"
        elif _looks_image_name(file_name) or _looks_image_name(text):
            message_type = "picture"
        else:
            message_type = "file"
    else:
        text = _extract_nested_text(params)
        media = media or _extract_media_url(params)
    if not text and not media and not file_uuid:
        return None
    if _looks_audio_name(file_name) and not text:
        text = "Səs mesajı"
        message_type = "audio"
    elif _looks_image_name(file_name) and not text:
        text = file_name or "Şəkil"
        message_type = "picture"
    is_media_notice = _is_media_notice_text(text)
    audio_hint = message_type in {"audio", "voice", "ptt"} or _looks_audio_name(file_name)
    if is_media_notice and audio_hint:
        message_type = "audio"
        text = "Səs mesajı"
    elif is_media_notice:
        message_type = "picture"
        text = (file_name or "Şəkil") if (media or file_uuid) else "📷 Şəkil"
    is_chat = _is_chat_note_type(ntype, file_name, message_type)
    if ntype == "common" and ("səs yazısı" in text.casefold() or "🎤" in text or is_media_notice):
        is_chat = True
        if not is_media_notice and message_type == "text":
            message_type = "audio"
    return {
        "id": note.get("id"),
        "entity_id": note.get("entity_id") or None,
        "entity_type": "contacts" if str(entity_type or "").startswith("contact") else "leads",
        "type": ntype,
        "text": text,
        "created_at": created,
        "created": _deal_fmt_ts(created),
        "media_url": media,
        "file_uuid": file_uuid,
        "file_name": file_name,
        "message_type": message_type,
        "is_chat": is_chat,
        "incoming": _note_is_incoming(ntype),
        "channel": _note_channel_key(note) or "",
        "author": _users.get(note.get("created_by") or note.get("responsible_user_id"), "") or "",
        "msgid": _first_msgid(params, note),
    }


def _note_recording_src(note: dict) -> tuple[str, str]:
    params = note.get("params") if isinstance(note.get("params"), dict) else {}
    return _extract_media_url(params), _extract_file_uuid(params)
