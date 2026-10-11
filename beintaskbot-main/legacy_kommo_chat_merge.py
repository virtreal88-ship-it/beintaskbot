"""Merge and de-duplicate normalized Kommo chat timeline items."""

from __future__ import annotations

from collections.abc import Callable


_split_quote: Callable[[str], tuple[str, str]] = lambda text: ("", str(text or ""))


def configure_chat_merge(*, split_quote: Callable[[str], tuple[str, str]]) -> None:
    global _split_quote
    _split_quote = split_quote


def _chat_item_key(item: dict) -> tuple:
    return (
        str(item.get("id") or ""),
        str(item.get("text") or ""),
        int(item.get("created_at") or 0),
        str(item.get("file_uuid") or ""),
    )


def _clean_body_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    _quote, body = _split_quote(raw)
    return body.strip() if body else raw


def _delivery_rank(status: str) -> int:
    text = str(status or "").strip().lower()
    if text in {"read", "seen", "viewed", "3"}:
        return 5
    if text in {"delivered", "1", "2"}:
        return 4
    if text in {"sent", "0"}:
        return 3
    if text in {"sending", "pending"}:
        return 2
    if text in {"error", "failed", "undelivered", "4", "-1"}:
        return 1
    return 0


def _copy_richer_item(existing: dict, incoming: dict) -> None:
    existing_id = str(existing.get("id") or "").strip()
    incoming_id = str(incoming.get("id") or "").strip()
    existing_stub = existing_id.startswith(("sent-", "local-", "note-", "event-", "hook-"))
    incoming_stub = incoming_id.startswith(("sent-", "local-", "note-", "event-", "hook-"))
    if existing_stub and not incoming_stub:
        existing["id"] = incoming_id
        for field in ("external_id", "talk_id", "created_at", "text"):
            if incoming.get(field):
                existing[field] = incoming[field]
    for field in ("file_uuid", "media_url", "cards"):
        if incoming.get(field) and not existing.get(field):
            existing[field] = incoming[field]
    if incoming.get("message_type") and (
        not existing.get("message_type") or existing.get("message_type") == "text"
    ):
        existing["message_type"] = incoming["message_type"]
    if _delivery_rank(incoming.get("delivery_status")) > _delivery_rank(existing.get("delivery_status")):
        existing["delivery_status"] = incoming.get("delivery_status")


def _same_chat_item(left: dict, right: dict, *, include_id: bool = False) -> bool:
    if bool(left.get("incoming")) != bool(right.get("incoming")):
        return False
    left_id = str(left.get("id") or "").strip()
    right_id = str(right.get("id") or "").strip()
    if include_id and left_id and right_id and left_id == right_id:
        return True
    left_external = str(left.get("external_id") or "").strip()
    right_external = str(right.get("external_id") or "").strip()
    if left_external and right_external and left_external == right_external:
        return True
    left_uuid = str(left.get("file_uuid") or "").strip()
    right_uuid = str(right.get("file_uuid") or "").strip()
    if left_uuid and right_uuid and left_uuid == right_uuid:
        return True
    left_media = str(left.get("media_url") or "").strip()
    right_media = str(right.get("media_url") or "").strip()
    if left_media and right_media and left_media == right_media:
        return True
    left_text = str(left.get("text") or "").strip()
    right_text = str(right.get("text") or "").strip()
    left_clean = _clean_body_text(left_text)
    right_clean = _clean_body_text(right_text)
    left_ts = int(left.get("created_at") or 0)
    right_ts = int(right.get("created_at") or 0)
    return bool(
        (left_text and left_text == right_text and abs(left_ts - right_ts) <= 300)
        or (left_clean and left_clean == right_clean and abs(left_ts - right_ts) <= 300)
    )


def _merge_chat_item(chat: list[dict], seen: set[tuple], item: dict | None) -> None:
    if not item:
        return
    key = _chat_item_key(item)
    if key in seen:
        return
    for existing in chat:
        if _same_chat_item(existing, item):
            _copy_richer_item(existing, item)
            return
    seen.add(key)
    chat.append(item)


def _deduplicate_chat(chat: list[dict]) -> list[dict]:
    clean: list[dict] = []
    for candidate in chat:
        duplicate = False
        for kept in clean:
            if _same_chat_item(kept, candidate, include_id=True):
                _copy_richer_item(kept, candidate)
                duplicate = True
                break
        if not duplicate:
            clean.append(candidate)
    return clean
