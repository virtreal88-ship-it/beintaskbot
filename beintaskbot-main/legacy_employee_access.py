"""Legacy employee access policy and its durable cache."""

from __future__ import annotations

import logging
import threading
import time

from gh_storage import read_json, write_json
from legacy_runtime_config import ADMIN_CHAT_ID, HUSEYN_CHAT_ID, RASIM_CHAT_ID, RUFAT_CHAT_ID
from legacy_user_registry import (
    _KNOWN_EMPLOYEE_REGISTRATIONS,
    _RETIRED_EMPLOYEE_CHAT_IDS,
    load_users,
)

logger = logging.getLogger(__name__)

_EMPLOYEE_ACCESS_FILE = "employee_access.json"
_EMPLOYEE_PERMISSIONS = (
    "deals", "tasks", "hot_orders", "hot_orders_create", "finance", "passive_tasks",
    "waiting", "approvals_system", "approvals_news", "customers", "employees", "linear",
    "linear_create", "linear_edit",
)
_ALL_EMPLOYEE_PERMISSIONS = frozenset(_EMPLOYEE_PERMISSIONS)
_MASTER_PERMISSIONS = frozenset({"hot_orders", "hot_orders_create"})
_HOT_ORDER_SKILLS = ("all", "montaj", "temir", "catdirilma", "servis", "digər")
_employee_access_cache: dict[str, dict] | None = None
_employee_access_cache_at = 0.0
_employee_access_lock = threading.Lock()


def _normalize_employee_permissions(value, role: str = "") -> list[str]:
    values = {str(item) for item in value} if isinstance(value, (list, tuple, set)) else set()
    role_key = str(role or "").strip().casefold()
    if values & {"approvals_system", "approvals_news"}:
        values.add("waiting")
    if role_key in {"master", "usta"}:
        values &= _MASTER_PERMISSIONS
        if not values:
            values = {"hot_orders"}
        elif "hot_orders_create" in values:
            values.add("hot_orders")
    return [key for key in _EMPLOYEE_PERMISSIONS if key in values]


def _normalize_hot_order_skills(value) -> list[str]:
    values = {str(item).strip().casefold() for item in value} if isinstance(value, (list, tuple, set)) else set()
    return [key for key in _HOT_ORDER_SKILLS if key in values] or ["all"]


def _load_employee_access_records(force: bool = False) -> dict[str, dict]:
    global _employee_access_cache, _employee_access_cache_at
    now = time.time()
    with _employee_access_lock:
        if not force and _employee_access_cache is not None and now - _employee_access_cache_at < 20:
            return dict(_employee_access_cache)
        try:
            raw = read_json(_EMPLOYEE_ACCESS_FILE) or {}
        except Exception as exc:
            logger.warning("employee access storage load failed: %s", exc)
            raw = {}
        records = {
            str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)
        } if isinstance(raw, dict) else {}
        _employee_access_cache = records
        _employee_access_cache_at = now
        return dict(records)


def _save_employee_access_records(records: dict[str, dict]) -> None:
    global _employee_access_cache, _employee_access_cache_at
    clean = {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}
    write_json(_EMPLOYEE_ACCESS_FILE, clean)
    with _employee_access_lock:
        _employee_access_cache = clean
        _employee_access_cache_at = time.time()


def employee_access_profile(chat_id: int) -> dict:
    """Return the effective access record; legacy staff retain current access."""
    try:
        employee_id = int(chat_id)
    except (TypeError, ValueError):
        return {"active": False, "role": "Əməkdaş", "permissions": []}
    stored = _load_employee_access_records().get(str(employee_id))
    explicit_reactivation = isinstance(stored, dict) and (
        stored.get("reactivated_by_admin")
        or (bool(stored.get("active")) and bool(stored.get("updated_by")))
    )
    if employee_id in _RETIRED_EMPLOYEE_CHAT_IDS and not explicit_reactivation:
        return {"active": False, "role": "Əməkdaş", "permissions": [], "hot_order_skills": []}
    if isinstance(stored, dict):
        if stored.get("deleted"):
            return {"active": False, "role": "Əməkdaş", "permissions": [], "hot_order_skills": []}
        canonical_admin = employee_id == ADMIN_CHAT_ID
        stored_role = str(stored.get("role") or "").strip().casefold()
        role = "Admin" if canonical_admin or stored_role == "admin" else (
            "Usta" if stored_role in {"master", "usta"} else (
                "İstifadəçi" if stored_role in {"manager", "pipeline_user", "istifadəçi"} else "Əməkdaş"
            )
        )
        if not stored.get("approval_permissions_version"):
            values = set(stored.get("permissions") or [])
            if employee_id == int(HUSEYN_CHAT_ID):
                values.update({"waiting", "approvals_news"})
                values.discard("approvals_system")
            elif role == "Admin" and "waiting" in values:
                values.update({"approvals_system", "approvals_news"})
            stored = {**stored, "permissions": list(values), "approval_permissions_version": 1}
            records = _load_employee_access_records()
            records[str(employee_id)] = stored
            _save_employee_access_records(records)
        permissions = _normalize_employee_permissions(stored.get("permissions"), role)
        if not stored.get("linear_create_permission_version") and role != "Usta":
            defaults = {int(RUFAT_CHAT_ID), int(HUSEYN_CHAT_ID), int(RASIM_CHAT_ID)}
            records = _load_employee_access_records()
            cursor = int(RUFAT_CHAT_ID)
            seen = set()
            while cursor not in seen:
                seen.add(cursor)
                target = (records.get(str(cursor)) or {}).get("migrated_to")
                if not target:
                    break
                try:
                    cursor = int(target)
                except (TypeError, ValueError):
                    break
                defaults.add(cursor)
            if employee_id in defaults and "linear" in permissions:
                permissions.append("linear_create")
        if canonical_admin and stored.get("permissions_version") != 2:
            permissions = [key for key in permissions if key != "hot_orders"]
        if canonical_admin:
            enabled = set(permissions) | {"linear", "linear_edit"}
            permissions = [key for key in _EMPLOYEE_PERMISSIONS if key in enabled]
        return {
            "active": True if canonical_admin else bool(stored.get("active", True)),
            "role": role,
            "permissions": permissions,
            "ai_enabled": bool(stored.get("ai_enabled", employee_id in {ADMIN_CHAT_ID, RUFAT_CHAT_ID})),
            "hot_order_skills": _normalize_hot_order_skills(stored.get("hot_order_skills")),
            "completion_requires_admin": bool(stored.get("completion_requires_admin", False)),
            "pipeline_id": int(stored.get("pipeline_id") or 0),
            "notifications": stored.get("notifications") if isinstance(stored.get("notifications"), dict) else {},
        }
    users = load_users()
    known = employee_id in _KNOWN_EMPLOYEE_REGISTRATIONS or str(employee_id) in users
    if not known:
        return {"active": False, "role": "Əməkdaş", "permissions": [], "hot_order_skills": []}
    role = "Admin" if employee_id == ADMIN_CHAT_ID else "Əməkdaş"
    return {
        "active": True,
        "role": role,
        "permissions": [
            key for key in _EMPLOYEE_PERMISSIONS
            if key not in {"approvals_system", "approvals_news"}
            or role == "Admin" or (employee_id == int(HUSEYN_CHAT_ID) and key == "approvals_news")
        ],
        "ai_enabled": employee_id in {ADMIN_CHAT_ID, RUFAT_CHAT_ID},
        "hot_order_skills": ["all"],
        "completion_requires_admin": False,
        "pipeline_id": 0,
        "notifications": {},
    }


def is_admin(chat_id: int) -> bool:
    try:
        profile = employee_access_profile(int(chat_id))
    except (TypeError, ValueError):
        return False
    return bool(profile.get("active")) and str(profile.get("role") or "").casefold() == "admin"


def employee_requires_completion_approval(chat_id: int) -> bool:
    return False if is_admin(chat_id) else bool(employee_access_profile(chat_id).get("completion_requires_admin"))


def get_rufat_compat_chat_ids() -> set[int]:
    result = {int(RUFAT_CHAT_ID)}
    try:
        records = _load_employee_access_records()
        pending = [int(RUFAT_CHAT_ID)]
        while pending:
            source_id = pending.pop()
            record = records.get(str(source_id))
            target = record.get("migrated_to") if isinstance(record, dict) else None
            try:
                target_id = int(target)
            except (TypeError, ValueError):
                continue
            if target_id in result or not employee_access_profile(target_id).get("active"):
                continue
            result.add(target_id)
            pending.append(target_id)
    except Exception as exc:
        logger.warning("Could not resolve Rüfət Telegram aliases: %s", exc)
    return result


def employee_has_permission(chat_id: int, permission: str) -> bool:
    profile = employee_access_profile(chat_id)
    permissions = set(profile.get("permissions") or [])
    if permission in {"approvals_system", "approvals_news"} and "waiting" not in permissions:
        return False
    if permission == "waiting" and not permissions.intersection({"approvals_system", "approvals_news"}):
        return False
    return bool(profile.get("active")) and permission in permissions


def can_receive_staff_notification(chat_id) -> bool:
    try:
        employee_id = int(chat_id)
    except (TypeError, ValueError):
        return False
    return employee_id == int(ADMIN_CHAT_ID) or bool(employee_access_profile(employee_id).get("active"))


def can_use_deal_ai(chat_id: int) -> bool:
    try:
        profile = employee_access_profile(int(chat_id))
    except (TypeError, ValueError):
        return False
    return bool(profile.get("active")) and bool(profile.get("ai_enabled"))
