"""Durable legacy Telegram-to-Kommo employee registry."""

from __future__ import annotations

import json
import logging
import os

from gh_storage import read_json, write_json
from legacy_runtime_config import (
    ADMIN_CHAT_ID,
    ADMIN_KOMMO_USER_ID,
    RUFAT_CHAT_ID,
)

logger = logging.getLogger(__name__)

_USER_DB_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")
_KNOWN_EMPLOYEE_REGISTRATIONS = {
    1628569350: ("Nizami Qasımov", 10932455),
    RUFAT_CHAT_ID: ("Rüfət Həsənzadə", 15532668),
    7329891614: ("Hüseyn Səfərov", 15532668),
    7920785774: ("Rasim Əsgərov", 15532668),
    1289510272: ("Sərmayə Əhmədsoy", 15532668),
    6596538872: ("Asya Agayeva", 15532668),
    1142054888: ("Nuranə Şirinova", 15532668),
}
_ACTIVE_STAFF_CHAT_IDS = {
    RUFAT_CHAT_ID, 7329891614, 7920785774, 1289510272, 6596538872, 1142054888,
}
_SALARY_CHAT_IDS = {RUFAT_CHAT_ID, 7329891614, 1289510272, 6596538872, 1142054888}
_RETIRED_EMPLOYEE_CHAT_IDS = {7262243946, 7962757442, 8835096199}
_ACTIVE_TEAM_CHAT_IDS = _ACTIVE_STAFF_CHAT_IDS | {ADMIN_CHAT_ID}
_ROSTER_CLEANUP_FILE = "employee_roster_cleanup_v1.json"


def load_users() -> dict:
    """Load registrations from durable storage, with a local cache fallback."""
    local_users = {}
    if os.path.exists(_USER_DB_LOCAL):
        try:
            with open(_USER_DB_LOCAL, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
                if isinstance(loaded, dict):
                    local_users = loaded
        except Exception:
            pass

    durable_users = {}
    try:
        loaded = read_json("users.json") or {}
        if isinstance(loaded, dict):
            durable_users = loaded
    except Exception as exc:
        logger.warning("users.json durable load failed: %s", exc)

    users = dict(durable_users)
    users.update(local_users)
    if users and users != local_users:
        try:
            with open(_USER_DB_LOCAL, "w", encoding="utf-8") as handle:
                json.dump(users, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return users


def save_users(data: dict) -> None:
    try:
        with open(_USER_DB_LOCAL, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass
    try:
        write_json("users.json", data)
    except Exception as exc:
        logger.error("save_users gh error: %s", exc)


def ensure_known_employee_registrations() -> None:
    """Seed fixed employee accounts once, so /start survives deploys."""
    users = load_users()
    changed = False
    for chat_id, (name, kommo_user_id) in _KNOWN_EMPLOYEE_REGISTRATIONS.items():
        key = str(chat_id)
        current = users.get(key)
        if not isinstance(current, dict):
            current = {}
        expected = {
            "role": "Admin" if chat_id == ADMIN_CHAT_ID else "Əməkdaş",
            "name": name,
            "kommo_user_id": kommo_user_id,
        }
        if any(current.get(field) != value for field, value in expected.items()):
            current.update(expected)
            users[key] = current
            changed = True
    if changed:
        save_users(users)
        logger.info("Known employee registrations restored for startup")


def get_chat_id_for_kommo_user(kommo_user_id: int) -> int | None:
    if int(kommo_user_id or 0) == ADMIN_KOMMO_USER_ID:
        return ADMIN_CHAT_ID
    for chat_id, info in load_users().items():
        if isinstance(info, dict) and info.get("kommo_user_id") == kommo_user_id:
            return int(chat_id)
    return None


def get_kommo_user_id_for_chat(chat_id: int) -> int | None:
    info = load_users().get(str(chat_id))
    if isinstance(info, dict) and info.get("kommo_user_id"):
        return info["kommo_user_id"]
    try:
        employee_id = int(chat_id)
    except (TypeError, ValueError):
        return None
    if employee_id == 1628569350:
        return 10932455
    if employee_id in _SALARY_CHAT_IDS:
        return 15532668
    return None
