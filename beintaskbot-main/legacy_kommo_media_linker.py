"""Resolve missing Kommo chat media from notes and entity files."""

from __future__ import annotations

import re
from collections.abc import Callable


_fetch_notes: Callable[[str, int], list[dict]] = lambda _kind, _entity_id: []
_fetch_files: Callable[[str, int], list[dict]] = lambda _kind, _entity_id: []
_media_notice: Callable[[str], bool] = lambda _text: False
_looks_audio: Callable[[str], bool] = lambda _name: False
_looks_image: Callable[[str], bool] = lambda _name: False


def configure_media_linker(
    *, fetch_notes: Callable, fetch_files: Callable, media_notice: Callable,
    looks_audio: Callable, looks_image: Callable,
) -> None:
    global _fetch_notes, _fetch_files, _media_notice, _looks_audio, _looks_image
    _fetch_notes = fetch_notes
    _fetch_files = fetch_files
    _media_notice = media_notice
    _looks_audio = looks_audio
    _looks_image = looks_image


def _photo_placeholder_text(text: str) -> bool:
    raw = str(text or "").strip().casefold()
    return _media_notice(raw) or raw in {
        "şəkil", "📷 şəkil", "sekil", "picture", "image", "sticker",
    }


def _candidate_is_image(candidate: dict) -> bool:
    kind = str(candidate.get("message_type") or "").lower()
    return (
        kind in {"picture", "image", "sticker"}
        or str(candidate.get("content_type") or "").lower().startswith("image/")
        or _looks_image(str(candidate.get("file_name") or ""))
    )


def _candidate_is_audio(candidate: dict) -> bool:
    kind = str(candidate.get("message_type") or "").lower()
    if kind in {"audio", "voice", "ptt"}:
        return True
    return _looks_audio(f"{candidate.get('file_name') or ''} {candidate.get('media_url') or ''}")


def _candidate_is_video(candidate: dict) -> bool:
    kind = str(candidate.get("message_type") or "").lower()
    if kind == "video" or str(candidate.get("content_type") or "").lower().startswith("video/"):
        return True
    blob = f"{candidate.get('file_name') or ''} {candidate.get('media_url') or ''}"
    return bool(re.search(r"\.(mp4|mov|webm|3gp|mkv)(?:\?|$)", blob, re.IGNORECASE))


def _unix_seconds(value) -> int:
    try:
        stamp = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return stamp // 1000 if stamp > 10_000_000_000 else stamp


def _deal_media_candidates(lead_id: int, contact_ids: list[int]) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(row: dict) -> None:
        if not isinstance(row, dict):
            return
        if not (str(row.get("media_url") or "").strip() or str(row.get("file_uuid") or "").strip()):
            return
        key = str(row.get("file_uuid") or row.get("media_url") or row.get("id") or "")
        if key and key not in seen:
            seen.add(key)
            candidates.append(row)

    entities = [("leads", int(lead_id))] + [("contacts", int(value)) for value in contact_ids]
    for entity_type, entity_id in entities:
        for row in _fetch_notes(entity_type, entity_id):
            add(row)
        for row in _fetch_files(entity_type, entity_id):
            add(row)
    return candidates


def _find_click_media(
    lead_id: int, contact_ids: list[int], created_at: int, kind: str, message_id: str,
) -> dict | None:
    created_at = _unix_seconds(created_at)
    message_id = str(message_id or "").strip()
    if not created_at and not message_id:
        return None
    wanted_kind = str(kind or "").lower()
    want_audio = wanted_kind in {"audio", "voice", "ptt"}
    want_video = wanted_kind == "video"
    best = None
    best_rank = None
    for candidate in _deal_media_candidates(lead_id, contact_ids):
        is_audio = _candidate_is_audio(candidate)
        if want_audio and not is_audio:
            continue
        if want_video and not _candidate_is_video(candidate):
            continue
        if not want_audio and not want_video and is_audio:
            continue
        identity = " ".join(
            str(candidate.get(key) or "")
            for key in ("id", "msgid", "external_id", "file_uuid", "file_name", "text")
        )
        if message_id and message_id in identity:
            return candidate
        candidate_at = _unix_seconds(candidate.get("created_at"))
        if not created_at or not candidate_at:
            continue
        difference = abs(created_at - candidate_at)
        if difference > 86400:
            continue
        rank = (0 if (want_audio or want_video or _candidate_is_image(candidate)) else 1, difference)
        if best_rank is None or rank < best_rank:
            best_rank, best = rank, candidate
    return best


def _link_missing_chat_media(chat: list[dict], lead_id: int, contact_ids: list[int]) -> None:
    needed = [
        item for item in chat
        if not str(item.get("media_url") or "").strip()
        and not str(item.get("file_uuid") or "").strip()
        and (
            _media_notice(str(item.get("text") or ""))
            or _photo_placeholder_text(str(item.get("text") or ""))
            or str(item.get("message_type") or "").lower()
            in {"picture", "image", "sticker", "audio", "video"}
        )
    ]
    if not needed:
        return
    candidates = _deal_media_candidates(lead_id, contact_ids)
    for item in needed:
        text = str(item.get("text") or "")
        match = re.search(r"(?:ID(?:\s+сообщения)?|ID):\s*([A-Za-z0-9_-]+)", text, re.I)
        target_id = match.group(1).strip() if match else ""
        item_at = _unix_seconds(item.get("created_at"))
        want_photo = _photo_placeholder_text(text) or str(item.get("message_type") or "").lower() in {
            "picture", "image", "sticker",
        }
        best = None
        best_diff = 99999999
        for candidate in candidates:
            if want_photo and not _candidate_is_image(candidate):
                continue
            identity = " ".join(str(candidate.get(key) or "") for key in ("id", "msgid", "external_id", "text", "file_name"))
            if target_id and target_id in identity:
                best = candidate
                break
            candidate_at = _unix_seconds(candidate.get("created_at"))
            difference = abs(item_at - candidate_at) if item_at and candidate_at else 999999
            if difference <= 600 and difference < best_diff:
                best_diff, best = difference, candidate
        if best:
            item["media_url"] = best.get("media_url") or ""
            item["file_uuid"] = best.get("file_uuid") or ""
            item["file_name"] = best.get("file_name") or item.get("file_name") or ""
            item["message_type"] = "picture" if _candidate_is_image(best) else (best.get("message_type") or "picture")
            if _media_notice(text) or _photo_placeholder_text(text):
                item["text"] = ""
