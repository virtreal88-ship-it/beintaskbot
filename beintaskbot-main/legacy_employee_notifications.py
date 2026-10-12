"""Per-employee notification policy for the legacy web application.

The legacy application predates tenants and historically sent Telegram and
push messages directly from many handlers.  This module gives those handlers
one policy boundary without changing the delivery transport.
"""

from __future__ import annotations

from legacy_employee_access import employee_access_profile


NOTIFICATION_EVENTS = (
    {
        "key": "task_assigned", "label": "Yeni tapşırıq təyin edildi",
        "permissions": ("tasks",), "defaults": {"telegram": True, "push": True},
    },
    {
        "key": "task_due", "label": "Tapşırığın vaxtı yaxınlaşır",
        "permissions": ("tasks",), "defaults": {"telegram": False, "push": True},
    },
    {
        "key": "task_overdue", "label": "Tapşırığın vaxtı keçib",
        "permissions": ("tasks",), "defaults": {"telegram": True, "push": True},
    },
    {
        "key": "customer_message", "label": "Müştəridən yeni mesaj",
        "permissions": ("customers",), "defaults": {"telegram": False, "push": True},
    },
    {
        "key": "deal_stage", "label": "Yeni sövdələşmə və mərhələ",
        "permissions": ("deals", "customers"), "defaults": {"telegram": False, "push": True},
    },
    {
        "key": "hot_order", "label": "İsti sifariş",
        "permissions": ("hot_orders",), "defaults": {"telegram": True, "push": True},
    },
    {
        "key": "linear_status", "label": "Linear statusu dəyişdi",
        "permissions": ("linear",), "defaults": {"telegram": True, "push": True},
    },
    {
        "key": "finance", "label": "Maliyyə əməliyyatı",
        "permissions": ("finance",), "defaults": {"telegram": True, "push": True},
    },
    {
        "key": "approval", "label": "Təsdiq tələb olunur",
        "permissions": ("waiting",), "defaults": {"telegram": True, "push": True},
    },
)

_EVENTS_BY_KEY = {row["key"]: row for row in NOTIFICATION_EVENTS}
_CHANNELS = ("telegram", "push")


def _event_applies(event: dict, profile: dict) -> bool:
    if not profile.get("active"):
        return False
    if str(profile.get("role") or "").casefold() == "admin":
        return True
    permissions = set(profile.get("permissions") or [])
    return bool(permissions.intersection(event.get("permissions") or ()))


def notification_event_catalog(profile: dict | None = None) -> list[dict]:
    """Return serializable notification choices, optionally role-filtered."""
    rows = NOTIFICATION_EVENTS
    if profile is not None:
        rows = tuple(row for row in rows if _event_applies(row, profile))
    return [
        {
            "key": row["key"],
            "label": row["label"],
            "permissions": list(row["permissions"]),
            "defaults": dict(row["defaults"]),
        }
        for row in rows
    ]


def normalize_employee_notifications(value, profile: dict) -> dict[str, dict[str, bool]]:
    """Keep only applicable known events and explicit channel booleans."""
    source = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, bool]] = {}
    for event in NOTIFICATION_EVENTS:
        if not _event_applies(event, profile):
            continue
        raw = source.get(event["key"])
        if not isinstance(raw, dict):
            raw = {}
        result[event["key"]] = {
            channel: bool(raw.get(channel, event["defaults"][channel]))
            for channel in _CHANNELS
        }
    return result


def effective_employee_notifications(profile: dict) -> dict[str, dict[str, bool]]:
    return normalize_employee_notifications(profile.get("notifications"), profile)


def notification_channel_enabled(chat_id, event_key: str, channel: str) -> bool:
    """Check one delivery channel, preserving historical defaults."""
    event = _EVENTS_BY_KEY.get(str(event_key or ""))
    channel_key = str(channel or "").casefold()
    if event is None or channel_key not in _CHANNELS:
        return False
    try:
        profile = employee_access_profile(int(chat_id))
    except (TypeError, ValueError):
        return False
    if not _event_applies(event, profile):
        return False
    stored = profile.get("notifications")
    event_settings = stored.get(event["key"]) if isinstance(stored, dict) else None
    if isinstance(event_settings, dict) and channel_key in event_settings:
        return bool(event_settings[channel_key])
    return bool(event["defaults"][channel_key])
