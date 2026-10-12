"""Per-employee Linear identity for the legacy workspace."""

from __future__ import annotations

from legacy_employee_access import _load_employee_access_records, get_rufat_compat_chat_ids
from legacy_runtime_config import ADMIN_CHAT_ID, HUSEYN_CHAT_ID, RASIM_CHAT_ID, RUFAT_CHAT_ID


_LEGACY_DEFAULTS = {
    int(RUFAT_CHAT_ID): {
        "account": "rufet", "accounts": {"rufet", "rufat", "ryufat"},
        "operator": "rufet@beinsystems",
    },
    int(HUSEYN_CHAT_ID): {
        "account": "huseyn", "accounts": {"huseyn", "husein", "hussein"},
        "operator": "huseyn@beinsystems",
    },
    int(RASIM_CHAT_ID): {
        "account": "rasim", "accounts": {"rasim"},
        "operator": "rasim@beinsystems",
    },
    int(ADMIN_CHAT_ID): {
        "account": "nizam", "accounts": set(), "operator": "admin@nizam",
    },
}


def _record_for_employee(chat_id: int) -> tuple[int, dict]:
    employee_id = int(chat_id)
    records = _load_employee_access_records()
    record = records.get(str(employee_id))
    if isinstance(record, dict) and any(key in record for key in ("linear_account", "linear_operator")):
        return employee_id, record
    if employee_id in get_rufat_compat_chat_ids():
        canonical = int(RUFAT_CHAT_ID)
        canonical_record = records.get(str(canonical))
        return canonical, canonical_record if isinstance(canonical_record, dict) else {}
    return employee_id, record if isinstance(record, dict) else {}


def employee_linear_identity(chat_id: int) -> dict:
    """Return saved Linear scope, with current installation defaults as fallback."""
    employee_id, record = _record_for_employee(chat_id)
    fallback = _LEGACY_DEFAULTS.get(employee_id, {})
    account = str(record.get("linear_account") or fallback.get("account") or "").strip()
    operator = str(record.get("linear_operator") or fallback.get("operator") or "").strip()
    aliases = {
        str(value).strip() for value in (record.get("linear_accounts") or [])
        if str(value).strip()
    }
    if not aliases:
        aliases = set(fallback.get("accounts") or set())
    if account:
        aliases.add(account)
    return {"account": account, "accounts": aliases, "operator": operator}
