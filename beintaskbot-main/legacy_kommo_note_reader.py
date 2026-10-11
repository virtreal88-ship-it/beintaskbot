"""Read and cache legacy Kommo notes without owning presentation logic."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from legacy_runtime_config import KOMMO_BASE_URL


logger = logging.getLogger(__name__)
_http = None
_headers: dict[str, str] = {}
_formatter: Callable[[dict, str], dict | None] = lambda _note, _entity_type: None
_lead_contact_ids: Callable[[dict], list[int]] = lambda _lead: []
_entity_notes_cache: dict[tuple[str, int, int], tuple[float, list[dict]]] = {}
_entity_notes_lock = threading.Lock()
_ENTITY_NOTES_TTL = 90.0


def configure_note_reader(
    *, http, headers: dict[str, str], formatter: Callable[[dict, str], dict | None],
    lead_contact_ids: Callable[[dict], list[int]], app_logger=None,
) -> None:
    global _http, _headers, _formatter, _lead_contact_ids, logger
    _http = http
    _headers = dict(headers)
    _formatter = formatter
    _lead_contact_ids = lead_contact_ids
    if app_logger is not None:
        logger = app_logger


def _fetch_raw_notes(entity_type: str, entity_id: int, pages: int = 3) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, pages + 1):
        try:
            response = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{int(entity_id)}/notes",
                headers=_headers,
                params={"limit": 250, "page": page, "order[created_at]": "desc"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Deal notes %s/%s failed: %s", entity_type, entity_id, exc)
            break
        if response.status_code == 204:
            break
        if response.status_code != 200:
            logger.warning("Deal notes %s/%s status %s", entity_type, entity_id, response.status_code)
            break
        notes = response.json().get("_embedded", {}).get("notes", []) or []
        if not notes:
            break
        rows.extend(note for note in notes if isinstance(note, dict))
        if len(notes) < 250:
            break
    return rows


def _find_raw_note(
    note_id: int, lead: dict | None = None, entity_type: str = "", entity_id: int = 0,
) -> dict | None:
    wanted = int(note_id or 0)
    if not wanted:
        return None
    targets: list[tuple[str, int]] = []
    if entity_type and entity_id:
        targets.append((str(entity_type), int(entity_id)))
    if lead:
        try:
            targets.append(("leads", int(lead.get("id") or 0)))
        except (TypeError, ValueError):
            pass
        for contact_id in _lead_contact_ids(lead):
            targets.append(("contacts", int(contact_id)))
    seen: set[tuple[str, int]] = set()
    for target_type, target_id in targets:
        if not target_id or (target_type, target_id) in seen:
            continue
        seen.add((target_type, target_id))
        for note in _fetch_raw_notes(target_type, target_id):
            try:
                if int(note.get("id") or 0) == wanted:
                    return note
            except (TypeError, ValueError):
                continue
    return None


def _fetch_entity_notes(entity_type: str, entity_id: int, pages: int = 1) -> list[dict]:
    entity_id = int(entity_id or 0)
    entity_type = str(entity_type or "leads")
    if not entity_id:
        return []
    pages = max(1, min(int(pages or 1), 3))
    cache_key = (entity_type, entity_id, pages)
    now = time.monotonic()
    with _entity_notes_lock:
        cached = _entity_notes_cache.get(cache_key)
        if cached and now - cached[0] < _ENTITY_NOTES_TTL:
            return list(cached[1])
    rows: list[dict] = []
    for page in range(1, pages + 1):
        try:
            response = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes",
                headers=_headers,
                params={"limit": 50, "page": page, "order[created_at]": "desc"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Deal notes %s/%s failed: %s", entity_type, entity_id, exc)
            break
        if response.status_code == 204:
            break
        if response.status_code != 200:
            logger.warning("Deal notes %s/%s status %s", entity_type, entity_id, response.status_code)
            break
        notes = response.json().get("_embedded", {}).get("notes", []) or []
        if not notes:
            break
        for note in notes:
            formatted = _formatter(note, entity_type)
            if formatted:
                rows.append(formatted)
        if len(notes) < 50:
            break
    with _entity_notes_lock:
        _entity_notes_cache[cache_key] = (time.monotonic(), rows)
    return list(rows)


def clear_entity_notes_cache() -> None:
    with _entity_notes_lock:
        _entity_notes_cache.clear()
