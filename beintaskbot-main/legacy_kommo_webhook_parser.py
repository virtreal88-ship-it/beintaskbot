"""Parse and de-duplicate legacy Kommo webhook payloads."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable


_media_notice: Callable[[str], bool] = lambda _text: False
_seen_incoming_ids: dict[str, float] = {}
_seen_incoming_lock = threading.Lock()


def configure_webhook_parser(*, media_notice: Callable[[str], bool]) -> None:
    global _media_notice
    _media_notice = media_notice


def _kommo_form_message_rows(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        return []
    message = data.get("message")
    if isinstance(message, dict) and isinstance(message.get("add"), list):
        return [row for row in message["add"] if isinstance(row, dict)]
    grouped: dict[int, dict] = {}
    for key, value in data.items():
        match = re.match(r"^message\[add\]\[(\d+)\]\[(.+)\]$", str(key))
        if not match:
            continue
        grouped.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return [grouped[index] for index in sorted(grouped)]


def _incoming_message_preview(text: str, message_type: str) -> str:
    raw = str(text or "").strip()
    if _media_notice(raw):
        return "📷 Şəkil"
    clean = " ".join(raw.split())[:140]
    if clean:
        return clean
    kind = str(message_type or "").strip().lower()
    if kind in {"picture", "image", "sticker"}:
        return "Şəkil"
    if kind in {"voice", "audio", "ptt"}:
        return "Səs mesajı"
    if kind == "video":
        return "Video"
    if kind in {"file", "document"}:
        return "Fayl"
    return "Yeni mesaj"


def _incoming_message_seen(message_id: str) -> bool:
    """Mark one external message for 24 hours and detect webhook retries."""
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    now = time.time()
    with _seen_incoming_lock:
        previous = _seen_incoming_ids.get(message_id)
        if previous and now - previous < 86400:
            return True
        _seen_incoming_ids[message_id] = now
        if len(_seen_incoming_ids) > 4000:
            cutoff = now - 86400
            for old in [key for key, saved_at in _seen_incoming_ids.items() if saved_at < cutoff]:
                _seen_incoming_ids.pop(old, None)
        return False


def _lead_webhook_events(data: dict) -> list[dict]:
    """Return normalized lead add/update/status rows from form payloads."""
    if not isinstance(data, dict):
        return []
    events: list[dict] = []
    for key, value in data.items():
        match = re.match(r"^leads\[(status|update|add)\]\[(\d+)\]\[id\]$", str(key))
        if not match:
            continue
        event_type, index = match.groups()
        try:
            lead_id = int(value or 0)
        except (TypeError, ValueError):
            continue
        if not lead_id:
            continue

        def integer(field: str) -> int:
            try:
                return int(data.get(f"leads[{event_type}][{index}][{field}]") or 0)
            except (TypeError, ValueError):
                return 0

        events.append({
            "type": event_type,
            "index": int(index),
            "lead_id": lead_id,
            "pipeline_id": integer("pipeline_id"),
            "old_pipeline_id": integer("old_pipeline_id"),
            "status_id": integer("status_id"),
            "old_status_id": integer("old_status_id"),
        })
    return events


def clear_webhook_dedup() -> None:
    with _seen_incoming_lock:
        _seen_incoming_ids.clear()
