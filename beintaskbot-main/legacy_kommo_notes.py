"""Legacy Kommo note reads and mutations."""

from __future__ import annotations

import logging
from collections.abc import Callable

from legacy_runtime_config import KOMMO_BASE_URL

logger = logging.getLogger(__name__)
_http = None
_headers: dict[str, str] = {}
_on_deleted: Callable[[int], object] = lambda _note_id: None
_NOTE_DELETED_MARK = "⟦silindi⟧"


def configure_note_service(
    *, http, headers: dict[str, str], on_deleted: Callable[[int], object], app_logger=None,
) -> None:
    global _http, _headers, _on_deleted, logger
    _http = http
    _headers = dict(headers)
    _on_deleted = on_deleted
    if app_logger is not None:
        logger = app_logger


def get_contact_notes(contact_id: int) -> list:
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}/notes",
            headers=_headers,
            params={"limit": 20, "order[updated_at]": "desc"},
            timeout=8,
        )
        if response.status_code == 200:
            return response.json().get("_embedded", {}).get("notes", [])
    except Exception:
        pass
    return []


def add_note(entity_id: int, text: str, entity_type: str = "contacts") -> dict | None:
    try:
        response = _http.post(
            f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes",
            headers=_headers,
            json=[{"note_type": "common", "params": {"text": text}}],
            timeout=8,
        )
        if response.status_code in (200, 201):
            return response.json()
    except Exception as exc:
        logger.error("Add note error: %s", exc)
    return None


def _note_is_deleted(item: dict | None) -> bool:
    text = str((item or {}).get("text") or "").strip()
    return (not text) or text == _NOTE_DELETED_MARK


def _note_entity_kind(entity_type: str) -> str:
    raw = str(entity_type or "").strip().lower()
    if raw.startswith("contact") or raw in {"1", "contacts"}:
        return "contacts"
    return "leads"


def update_note(
    note_id: int, text: str, entity_type: str = "leads", entity_id: int = 0,
) -> tuple[bool, str]:
    try:
        note_id = int(note_id)
    except (TypeError, ValueError):
        return False, "Qeyd tapılmadı."
    try:
        entity_id = int(entity_id or 0)
    except (TypeError, ValueError):
        entity_id = 0
    primary = _note_entity_kind(entity_type)
    kinds = [primary] + [kind for kind in ("leads", "contacts") if kind != primary]
    body = {"id": note_id, "params": {"text": str(text or "")}}
    if entity_id:
        body["entity_id"] = entity_id
    last_error = "Qeyd yenilənmədi."
    for kind in kinds:
        payloads = [[{**body, "note_type": "common"}], [body]]
        urls = [f"{KOMMO_BASE_URL}/api/v4/{kind}/notes"]
        if entity_id:
            urls.insert(0, f"{KOMMO_BASE_URL}/api/v4/{kind}/{entity_id}/notes")
        for url in urls:
            for payload in payloads:
                try:
                    response = _http.patch(url, headers=_headers, json=payload, timeout=10)
                except Exception as exc:
                    logger.error("Update note error: %s", exc)
                    last_error = str(exc)
                    continue
                if response.status_code in {200, 202, 204}:
                    return True, ""
                last_error = (response.text or "")[:240] or f"HTTP {response.status_code}"
                logger.warning("Update note %s %s: %s", response.status_code, url, last_error)
    return False, last_error


def delete_note(
    note_id: int,
    entity_type: str = "leads",
    entity_id: int = 0,
    extra_ids: list[int] | None = None,
    note_type: str = "",
) -> tuple[bool, str]:
    del extra_ids, note_type
    try:
        note_id = int(note_id)
    except (TypeError, ValueError):
        return False, "Qeyd tapılmadı."
    ok, error = update_note(note_id, " ", _note_entity_kind(entity_type), entity_id)
    if ok:
        _on_deleted(note_id)
        return True, ""
    return False, error or "Qeyd silinmədi."


def _first_note_id(result) -> int:
    if not isinstance(result, dict):
        return 0
    notes = (result.get("_embedded") or {}).get("notes") or []
    if notes and isinstance(notes[0], dict):
        try:
            return int(notes[0].get("id") or 0)
        except (TypeError, ValueError):
            pass
    return 0
