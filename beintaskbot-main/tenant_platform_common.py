"""Pure normalization helpers and platform defaults."""
import re
import secrets
from urllib.parse import unquote

from tenant_policy import ROLE_PERMISSIONS


_ROLE_PERMISSIONS = ROLE_PERMISSIONS
_DEFAULT_MODULES = {"deals": True, "tasks": True, "customers": True, "hot_orders": False, "finance": False}
_DEFAULT_NOTIFICATIONS = {"new_lead": True, "incoming_message": True, "task_assigned": True, "task_overdue": True}

def _invite_token(value: object) -> str:
    """Normalize a token copied from Telegram/WhatsApp/browser URL.

    Mobile clients sometimes URL-decode the path or append punctuation when a
    link is copied from a message.  The stored digest is calculated from the
    original URL-safe token, so normalizing here prevents a valid invite from
    being rejected just because of that transport formatting.
    """
    token = unquote(str(value or "")).strip()
    token = token.rstrip(".,;:!?)]}>\"'")
    return token

def _role(value: str, *, owner: bool = False) -> str:
    if owner:
        return "owner"
    return value if value in {"admin", "manager", "operator", "worker", "master"} else "operator"


def _permissions(value, role: str) -> list[str]:
    # Roles provide sensible defaults. The owner may still grant a smaller
    # custom set of ordinary workspace modules to a manager or worker.
    if role == "owner":
        return list(_ROLE_PERMISSIONS["owner"])
    modules = {permission for values in _ROLE_PERMISSIONS.values() for permission in values}
    allowed = modules | {f'{module}:{action}' for module in modules
                         for action in ('view','create','update','delete','assign','manage')}
    if not isinstance(value, list):
        return list(_ROLE_PERMISSIONS.get(role, _ROLE_PERMISSIONS["operator"]))
    requested = [str(item) for item in value if str(item) in allowed]
    if role == "master":
        requested = [item for item in requested if item == "hot_orders" or item.startswith('hot_orders:')]
        return requested
    return list(dict.fromkeys(requested))


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-")[:42] or "company"
    return f"{base}-{secrets.token_hex(3)}"


def _tenant_payload(row: dict) -> dict:
    result = dict(row)
    result["id"] = str(result["id"])
    for key in ("created_at", "updated_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    for key in ("modules", "notification_rules", "onboarding"):
        if not isinstance(result.get(key), dict):
            result[key] = {}
    return result
