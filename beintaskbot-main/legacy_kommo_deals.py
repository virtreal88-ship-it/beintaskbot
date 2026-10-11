"""Legacy Kommo deal reads and mutations with explicit side-effect hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable

from legacy_kommo_funnels import _lead_pipeline_id
from legacy_runtime_config import KOMMO_BASE_URL, PIPELINE_ID

logger = logging.getLogger(__name__)
_http = None
_headers: dict[str, str] = {}
_employee_funnel_user: Callable[[int], int | None] = lambda _pipeline_id: None
_pipeline_for_chat: Callable[[object], int] = lambda _chat_id: PIPELINE_ID
_on_stage_change: Callable[[int], object] = lambda _lead_id: None
_on_pipeline_move: Callable[[int, int], object] = lambda _lead_id, _pipeline_id: None


def configure_deal_service(
    *,
    http,
    headers: dict[str, str],
    employee_funnel_user: Callable[[int], int | None],
    pipeline_for_chat: Callable[[object], int],
    on_stage_change: Callable[[int], object],
    on_pipeline_move: Callable[[int, int], object],
    app_logger=None,
) -> None:
    global _http, _headers, _employee_funnel_user, _pipeline_for_chat
    global _on_stage_change, _on_pipeline_move, logger
    _http = http
    _headers = dict(headers)
    _employee_funnel_user = employee_funnel_user
    _pipeline_for_chat = pipeline_for_chat
    _on_stage_change = on_stage_change
    _on_pipeline_move = on_pipeline_move
    if app_logger is not None:
        logger = app_logger


def get_lead_details(lead_id: int) -> dict | None:
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}",
            headers=_headers,
            params={"with": "contacts"},
            timeout=8,
        )
        return response.json() if response.status_code == 200 else None
    except Exception as exc:
        logger.error("Lead details error: %s", exc)
        return None


def update_lead_kommo(lead_id: int, data: dict) -> dict | None:
    old_pipeline_id = None
    try:
        new_pipeline_id = int((data or {}).get("pipeline_id") or 0)
    except (TypeError, ValueError):
        new_pipeline_id = 0
    if new_pipeline_id and _employee_funnel_user(new_pipeline_id):
        details = get_lead_details(int(lead_id))
        if details:
            old_pipeline_id = _lead_pipeline_id(details)
    try:
        response = _http.patch(
            f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}",
            headers=_headers,
            json=data,
            timeout=8,
        )
        if response.status_code != 200:
            return None
        if "status_id" in data:
            _on_stage_change(int(lead_id))
        if new_pipeline_id and old_pipeline_id is not None and new_pipeline_id != old_pipeline_id:
            _on_pipeline_move(int(lead_id), new_pipeline_id)
        return response.json()
    except Exception as exc:
        logger.error("Update lead error: %s", exc)
        return None


def create_lead_for_contact(
    contact_id: int,
    contact_name: str,
    pipeline_id: int | None = None,
    status_id: int | None = None,
    *,
    tags: list | None = None,
) -> int | None:
    payload = {
        "name": contact_name or str(contact_id),
        "_embedded": {"contacts": [{"id": int(contact_id)}]},
        "pipeline_id": int(pipeline_id or PIPELINE_ID),
    }
    if status_id:
        payload["status_id"] = int(status_id)
    if tags:
        payload["_embedded"]["tags"] = tags
    try:
        response = _http.post(
            f"{KOMMO_BASE_URL}/api/v4/leads",
            headers=_headers,
            json=[payload],
            timeout=8,
        )
        if response.status_code in (200, 201):
            return response.json().get("_embedded", {}).get("leads", [{}])[0].get("id")
        logger.error(
            "Create lead for contact %s failed: status=%s body=%s",
            contact_id,
            response.status_code,
            response.text[:500],
        )
    except Exception as exc:
        logger.error("Create lead for contact %s error: %s", contact_id, exc)
    return None


def get_leads_by_status(status_id: int, chat_id: int = None) -> list:
    params = {
        "filter[statuses][0][pipeline_id]": _pipeline_for_chat(chat_id),
        "filter[statuses][0][status_id]": status_id,
        "limit": 50,
    }
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/leads",
            headers=_headers,
            params=params,
            timeout=8,
        )
        if response.status_code == 200:
            return response.json().get("_embedded", {}).get("leads", [])
    except Exception:
        pass
    return []
