"""Pure direction-adjacent metadata rules for legacy Kommo chat messages."""

from __future__ import annotations


_users: dict = {}


def configure_chat_message_meta(*, users: dict) -> None:
    global _users
    _users = dict(users or {})


def _chat_delivery_status(message: dict, nested: dict, incoming: bool) -> str:
    if incoming:
        return ""
    raw = (
        nested.get("status")
        or nested.get("delivery_status")
        or nested.get("state")
        or nested.get("msgid_status")
        or message.get("status")
        or message.get("delivery_status")
        or message.get("state")
        or message.get("msgid_status")
    )
    if isinstance(raw, dict):
        raw = raw.get("type") or raw.get("name") or raw.get("code") or raw.get("id")
    text = str(raw or "").strip().lower()
    mapping = {
        "0": "sent", "sent": "sent", "sending": "sent",
        "1": "delivered", "delivered": "delivered", "deliver": "delivered",
        "2": "delivered", "3": "read", "read": "read", "seen": "read",
        "viewed": "read", "-1": "error", "4": "error", "error": "error",
        "failed": "error", "undelivered": "error",
    }
    if text in mapping:
        return mapping[text]
    if nested.get("read") or message.get("read") or nested.get("seen") or message.get("seen"):
        return "read"
    if nested.get("delivered") or message.get("delivered"):
        return "delivered"
    if "read" in text or "seen" in text:
        return "read"
    if "undeliver" in text or "error" in text or "fail" in text:
        return "error"
    if "deliver" in text:
        return "delivered"
    return "sent"


def _is_generic_chat_author(name: str) -> bool:
    folded = str(name or "").strip().casefold()
    if not folded:
        return True
    tokens = (
        "whatsapp", "waba", "instagram", "facebook", "messenger", "tiktok",
        "telegram", "viber", "amojo", "kommo", "menecer", "manager", "müştəri", "musteri",
    )
    return any(token in folded for token in tokens) or folded in {
        "client", "bot", "capi", "im", "wa", "fb",
    }


def _chat_author_name(author: dict, message: dict, incoming: bool) -> str:
    name = str((author or {}).get("name") or "").strip()
    generic = _is_generic_chat_author(name)
    if incoming:
        return "" if generic else name
    if not generic:
        return name
    user_id = (author or {}).get("id") or message.get("created_by") or message.get("created_by_id")
    mapped = _users.get(user_id, "") if user_id not in (None, "") else ""
    return str(mapped or "").strip()
