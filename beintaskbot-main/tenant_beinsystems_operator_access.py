"""Fail-closed workspace and feature gates for the private Operator provider."""
from __future__ import annotations

import os
import uuid


WORKSPACE_ENV = "BEINSYSTEMS_OPERATOR_TENANT_ID"
API_BASE_URL_ENV = "BEINSYSTEMS_OPERATOR_API_BASE_URL"
PROVIDER = "beinsystems_operator"
# Confirmed by the owner's Operator browser trace and provider documentation.
OPERATOR_API_BASE_URL = "https://operator-api.beinsystems.az/v1"


def configured_workspace_id() -> str | None:
    value = str(os.environ.get(WORKSPACE_ENV) or "").strip()
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def is_operator_workspace(tenant_id: object) -> bool:
    allowed = configured_workspace_id()
    if not allowed:
        return False
    try:
        return str(uuid.UUID(str(tenant_id))) == allowed
    except (ValueError, TypeError, AttributeError):
        return False


def api_base_url() -> str:
    """Return the exact HTTPS API base confirmed by BeinSystems."""
    return OPERATOR_API_BASE_URL


def dialogue_chooser_enabled(tenant_id: object) -> bool:
    return (os.environ.get('BEINSYSTEMS_DIALOG_OPERATOR_CHOOSER_ENABLED','').lower()=='true'
            and is_operator_workspace(tenant_id))


def widget_allowed(profile, action="view", *, assigned=False):
    from tenant_policy import TenantPolicy

    if (not is_operator_workspace(profile.get("tenant_id")) or profile.get("active") is not True
            or profile.get("tenant_status") != "active"
            or (action == "update" and not operator_api_allowed(profile))):
        return False
    policy = TenantPolicy(profile)
    # Only callers proving exact local widget assignment can honor a self scope.
    return (policy.allows_action("operator_data", "view")
            and policy.action_scope("operator_data", "view") in (None, "all")
            and (policy.action_scope("dialogs", action) in (None, "all")
                 or assigned and policy.action_scope("dialogs", action) == "self")
            and policy.allows_action("dialogs", action))


def operator_api_allowed(profile):
    return bool(profile.get("active") and is_operator_workspace(profile.get("tenant_id"))
                and (profile.get("role") == "owner" or "operator_api" in (profile.get("permissions") or [])))
