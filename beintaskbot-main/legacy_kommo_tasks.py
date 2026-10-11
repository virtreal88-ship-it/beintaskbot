"""Legacy Kommo task reads and mutations with explicit runtime hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from legacy_runtime_config import KOMMO_BASE_URL

logger = logging.getLogger(__name__)
_http = None
_headers: dict[str, str] = {}
_on_create_started: Callable[[int], object] = lambda _entity_id: None
_on_created: Callable[[dict, str], object] = lambda _result, _creator: None
_on_updated: Callable[[int], object] = lambda _task_id: None
_on_completed: Callable[[int], object] = lambda _task_id: None


def configure_task_service(
    *,
    http,
    headers: dict[str, str],
    on_create_started: Callable[[int], object],
    on_created: Callable[[dict, str], object],
    on_updated: Callable[[int], object],
    on_completed: Callable[[int], object],
    app_logger=None,
) -> None:
    global _http, _headers, _on_create_started, _on_created
    global _on_updated, _on_completed, logger
    _http = http
    _headers = dict(headers)
    _on_create_started = on_create_started
    _on_created = on_created
    _on_updated = on_updated
    _on_completed = on_completed
    if app_logger is not None:
        logger = app_logger


def get_entity_tasks(entity_id: int, entity_type: str) -> list:
    params = {
        "filter[entity_id]": entity_id,
        "filter[entity_type]": entity_type,
        "filter[is_completed]": 0,
    }
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/tasks",
            headers=_headers,
            params=params,
            timeout=8,
        )
        if response.status_code == 200:
            return response.json().get("_embedded", {}).get("tasks", [])
    except Exception:
        pass
    return []


def get_tasks(start: datetime, end: datetime, responsible_id: int = None) -> list:
    params = {
        "filter[is_completed]": 0,
        "filter[complete_till][from]": int(start.timestamp()),
        "filter[complete_till][to]": int(end.timestamp()),
        "limit": 50,
    }
    if responsible_id:
        params["filter[responsible_user_id]"] = responsible_id
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/tasks",
            headers=_headers,
            params=params,
            timeout=8,
        )
        if response.status_code == 200:
            return response.json().get("_embedded", {}).get("tasks", [])
    except Exception:
        pass
    return []


def get_all_incomplete_tasks() -> list:
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/tasks",
            headers=_headers,
            params={"filter[is_completed]": 0, "limit": 250},
            timeout=8,
        )
        if response.status_code == 200:
            return response.json().get("_embedded", {}).get("tasks", [])
    except Exception:
        pass
    return []


def create_task(
    entity_id: int,
    text: str,
    complete_till: int,
    responsible_user_id: int = None,
    entity_type: str = "contacts",
    task_type_id: int = 1,
    creator_name: str = "",
) -> dict | None:
    task = {
        "text": text,
        "complete_till": complete_till,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "responsible_user_id": responsible_user_id or 10932455,
    }
    if task_type_id:
        task["task_type_id"] = task_type_id
    logger.info(
        "create_task: entity_id=%s, text=%s, resp_user=%s, type_id=%s",
        entity_id, text[:50], responsible_user_id, task_type_id,
    )
    _on_create_started(entity_id)
    try:
        response = _http.post(
            f"{KOMMO_BASE_URL}/api/v4/tasks",
            headers=_headers,
            json=[task],
            timeout=8,
        )
        if response.status_code not in (200, 201):
            logger.error(
                "create_task FAILED: status=%s, body=%s",
                response.status_code, response.text[:300],
            )
            return None
        result = response.json()
        try:
            _on_created(result, creator_name)
        except Exception as exc:
            logger.warning("Task created hook failed: %s", exc)
        return result
    except Exception as exc:
        logger.error("Create task error: %s", exc)
        return None


def _created_task_id(result) -> int:
    if not isinstance(result, dict):
        return 0
    tasks = (result.get("_embedded") or {}).get("tasks") or []
    if tasks and isinstance(tasks[0], dict):
        try:
            return int(tasks[0].get("id") or 0)
        except (TypeError, ValueError):
            pass
    return 0


def update_task_kommo(task_id, data: dict) -> dict | None:
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return None
    _on_updated(task_id)
    if data.get("is_completed"):
        _on_completed(task_id)
    try:
        response = _http.patch(
            f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}",
            headers=_headers,
            json=data,
            timeout=8,
        )
        logger.info("update_task_kommo %s: status=%s", task_id, response.status_code)
        if response.status_code == 200:
            return response.json()
        logger.error(
            "update_task_kommo failed: %s %s",
            response.status_code, response.text[:200],
        )
    except Exception as exc:
        logger.error("Update task error: %s", exc)
    return None


def get_kommo_task(task_id) -> dict | None:
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return None
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=_headers, timeout=8,
        )
        if response.status_code == 200:
            data = response.json() or {}
            if isinstance(data, dict) and data.get("id"):
                return data
            tasks = (data.get("_embedded") or {}).get("tasks") or []
            if tasks:
                return tasks[0]
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/tasks",
            headers=_headers,
            params={"filter[id][]": task_id, "limit": 1},
            timeout=8,
        )
        if response.status_code == 200:
            tasks = (response.json() or {}).get("_embedded", {}).get("tasks") or []
            if tasks:
                return tasks[0]
        logger.warning("get_kommo_task %s HTTP %s", task_id, getattr(response, "status_code", "?"))
    except Exception as exc:
        logger.error("get_kommo_task error: %s", exc)
    return None
