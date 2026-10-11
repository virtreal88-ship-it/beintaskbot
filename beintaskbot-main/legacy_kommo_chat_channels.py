"""Classify and rank legacy Kommo Talk channels without network access."""

from __future__ import annotations

import json
import re


CHAT_CHANNEL_LABELS = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "telegram": "Telegram",
}
_origin_channel_key = lambda _value: ""
_channel_labels: dict[str, str] = dict(CHAT_CHANNEL_LABELS)
_display_number = lambda _value: ""


def configure_chat_channels(*, origin_channel_key, channel_labels: dict, display_number) -> None:
    global _origin_channel_key, _channel_labels, _display_number
    _origin_channel_key = origin_channel_key
    _channel_labels = dict(channel_labels or {})
    _display_number = display_number


def _talk_is_open(talk: dict) -> bool:
    if not isinstance(talk, dict):
        return False
    if talk.get("is_closed") in {True, 1, "1", "true", "True"} or talk.get("closed_at"):
        return False
    return str(talk.get("status") or "").lower() not in {"2", "closed"}


def _talk_blob(talk: dict) -> str:
    try:
        return json.dumps(talk, ensure_ascii=False).lower()
    except Exception:
        return str(talk or "").lower()


def _talk_is_whatsapp_business(talk: dict) -> bool:
    """Identify retired WABA talks that must never receive customer replies."""
    blob = _talk_blob(talk)
    markers = (
        "whatsapp business", "whatsapp_business", "whatsapp-business",
        "whatsapp cloud", "whatsapp_cloud", "cloud api", "cloud_api", '"waba"',
    )
    return bool(blob) and any(marker in blob for marker in markers)


def _talk_has_digits(talk: dict, digits: str) -> bool:
    wanted = re.sub(r"\D", "", str(digits or ""))
    if len(wanted) < 8:
        return False
    blob = re.sub(r"\D", "", _talk_blob(talk))
    return wanted in blob or wanted[-9:] in blob


def _talk_channel_key(talk: dict) -> str:
    chat = talk.get("chat") if isinstance(talk.get("chat"), dict) else {}
    origin = " ".join(str(part or "") for part in (
        talk.get("origin"), talk.get("source"), chat.get("type"),
        chat.get("origin"), talk.get("entity_type"),
    )).strip().lower()
    key = _origin_channel_key(origin) or _origin_channel_key(_talk_blob(talk))
    if key:
        return key
    return "whatsapp" if origin in {"", "chat", "capi", "wa", "im"} or not origin else "other"


def _note_is_incoming(note_type: str) -> bool:
    text = str(note_type or "").lower()
    if "outgoing" in text or text in {"sms_out", "call_out"}:
        return False
    return "incoming" in text or text in {"sms_in", "call_in"}


def _note_channel_key(note: dict | None) -> str:
    data = note if isinstance(note, dict) else {}
    note_type = str(data.get("note_type") or data.get("type") or "").lower()
    if note_type in {"sms_in", "sms_out", "amomail_message"}:
        return ""
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    blob = " ".join(str(part or "").strip() for part in (
        note_type, params.get("service"), params.get("origin"), params.get("source"),
        params.get("messenger"), params.get("type"), params.get("provider"), params.get("waba"),
    ) if str(part or "").strip()).lower()
    key = _origin_channel_key(blob)
    if key:
        return key
    return "whatsapp" if note_type in {"incoming_chat_message", "outgoing_chat_message"} else ""


def _talk_reply_rank(talk: dict) -> tuple:
    channel = _talk_channel_key(talk)
    try:
        updated = int(talk.get("updated_at") or talk.get("created_at") or 0)
    except (TypeError, ValueError):
        updated = 0
    return (1 if _talk_is_open(talk) else 0, 1 if channel == "whatsapp" else 0, updated)


def _talk_chat_id(talk: dict) -> str:
    if not isinstance(talk, dict):
        return ""
    for key in ("chat_id", "conversation_id"):
        value = str(talk.get(key) or "").strip()
        if value:
            return value
    for container_name in ("origin", "chat"):
        container = talk.get(container_name)
        if not isinstance(container, dict):
            continue
        value = str(container.get("chat_id") or container.get("id") or "").strip()
        if value:
            return value
    return ""


def _talk_id_of(talk: dict) -> int:
    try:
        return int(talk.get("talk_id") or talk.get("id") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _channels_from_talks(talks: list[dict], sender_digits: str = "") -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for talk in talks or []:
        key = _talk_channel_key(talk)
        if key not in _channel_labels:
            continue
        if key == "whatsapp" and _talk_is_whatsapp_business(talk):
            continue
        grouped.setdefault(key, []).append(talk)
    rows = []
    order = ["whatsapp", "instagram", "facebook", "tiktok", "telegram"]
    for key in order:
        group = grouped.get(key) or []
        if not group:
            continue
        if key == "whatsapp" and sender_digits:
            matched = [talk for talk in group if _talk_has_digits(talk, sender_digits)]
            if matched:
                group = matched
        group.sort(key=_talk_reply_rank, reverse=True)
        talk = group[0]
        rows.append({
            "key": key,
            "label": _channel_labels[key],
            "talk_id": _talk_id_of(talk),
            "chat_id": _talk_chat_id(talk),
            "open": _talk_is_open(talk),
            "sender_phone": _display_number(sender_digits) if key == "whatsapp" else "",
        })
    return rows


def _ranked_reply_talk_ids(talks: list[dict]) -> list[int]:
    ranked = []
    seen: set[int] = set()
    for talk in talks or []:
        talk_id = _talk_id_of(talk)
        if not talk_id or talk_id in seen:
            continue
        seen.add(talk_id)
        ranked.append((_talk_reply_rank(talk), talk_id))
    ranked.sort(reverse=True)
    return [item[1] for item in ranked]
