"""One-time cleanup for retired legacy employee identities."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from gh_storage import read_json, write_json
from legacy_employee_access import _load_employee_access_records, _save_employee_access_records
from legacy_runtime_config import ADMIN_CHAT_ID, BAKU_TZ
from legacy_user_registry import (
    _ACTIVE_TEAM_CHAT_IDS,
    _KNOWN_EMPLOYEE_REGISTRATIONS,
    _RETIRED_EMPLOYEE_CHAT_IDS,
    _ROSTER_CLEANUP_FILE,
    load_users,
    save_users,
)

logger = logging.getLogger(__name__)


def remove_retired_employee_access(remove_push_subscription: Callable[[str], object]) -> None:
    retired_keys = {str(chat_id) for chat_id in _RETIRED_EMPLOYEE_CHAT_IDS}
    records = _load_employee_access_records(force=True)
    stored_records = dict(records)
    users = load_users()
    stored_users = dict(users)

    for old_id, record in list(records.items()):
        target_id = str(record.get("migrated_to") or "") if isinstance(record, dict) else ""
        if not target_id or target_id not in retired_keys:
            continue
        target_record = records.get(target_id)
        if isinstance(target_record, dict) and target_record.get("active") and (
            target_record.get("reactivated_by_admin") or target_record.get("updated_by")
        ):
            continue
        restored = {key: value for key, value in record.items() if key != "migrated_to"}
        restored.update({
            "active": True,
            "reactivated_by_admin": record.get("updated_by") or ADMIN_CHAT_ID,
        })
        records[target_id] = restored
        source_user = users.get(str(old_id)) if isinstance(users.get(str(old_id)), dict) else {}
        if source_user or target_id not in users:
            try:
                source_known = _KNOWN_EMPLOYEE_REGISTRATIONS.get(int(old_id), ("Əməkdaş", 0))
            except (TypeError, ValueError):
                source_known = ("Əməkdaş", 0)
            users[target_id] = {
                **source_user,
                "name": str(source_user.get("name") or source_known[0] or "Əməkdaş"),
                "role": str(source_user.get("role") or restored.get("role") or "Əməkdaş"),
                "telegram_id": int(target_id),
            }
            if not users[target_id].get("kommo_user_id") and source_known[1]:
                users[target_id]["kommo_user_id"] = source_known[1]

    reactivated = {
        str(key) for key, value in records.items()
        if str(key) in retired_keys and isinstance(value, dict) and (
            value.get("reactivated_by_admin")
            or (bool(value.get("active")) and bool(value.get("updated_by")))
        )
    }
    cleaned_records = {
        key: value for key, value in records.items() if key not in retired_keys or key in reactivated
    }
    cleaned_users = {
        key: value for key, value in users.items() if key not in retired_keys or key in reactivated
    }
    try:
        roster_marker = read_json(_ROSTER_CLEANUP_FILE) or {}
    except Exception:
        roster_marker = {}
    should_mark_cleanup = not bool(roster_marker.get("completed"))
    if should_mark_cleanup:
        known_by_name = {
            str(name).strip().casefold(): int(chat_id)
            for chat_id, (name, _kommo_user_id) in _KNOWN_EMPLOYEE_REGISTRATIONS.items()
        }

        def roster_chat_id(stored_id, value) -> int:
            if not isinstance(value, dict):
                try:
                    return int(stored_id)
                except (TypeError, ValueError):
                    return 0
            try:
                return int(
                    value.get("telegram_id")
                    or known_by_name.get(str(value.get("name") or "").strip().casefold())
                    or stored_id
                )
            except (TypeError, ValueError):
                return 0

        cleaned_users = {
            str(key): value for key, value in cleaned_users.items()
            if roster_chat_id(key, value) in _ACTIVE_TEAM_CHAT_IDS
        }
        cleaned_records = {
            str(key): value for key, value in cleaned_records.items()
            if roster_chat_id(key, users.get(str(key), {})) in _ACTIVE_TEAM_CHAT_IDS
        }
    if cleaned_records != stored_records:
        _save_employee_access_records(cleaned_records)
    if cleaned_users != stored_users:
        save_users(cleaned_users)
    if should_mark_cleanup:
        write_json(_ROSTER_CLEANUP_FILE, {
            "completed": True,
            "completed_at": datetime.now(tz=BAKU_TZ).isoformat(),
        })

    for chat_id in _RETIRED_EMPLOYEE_CHAT_IDS - {int(value) for value in reactivated}:
        try:
            remove_push_subscription(str(chat_id))
        except Exception as exc:
            logger.warning("Retired push cleanup failed for %s: %s", chat_id, exc)
