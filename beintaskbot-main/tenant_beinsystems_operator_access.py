"""Fail-closed workspace and feature gates for the private Operator provider."""
from __future__ import annotations

import os
import uuid


WORKSPACE_ENV = "BEINSYSTEMS_OPERATOR_TENANT_ID"
API_BASE_URL_ENV = "BEINSYSTEMS_OPERATOR_API_BASE_URL"
PROVIDER = "beinsystems_operator"
OPERATOR_API_BASE_URL = "https://operator-api.beinsystems.az/v1"
OPERATOR_WEB_ORIGIN = "https://operator.beinsystems.az"


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
    """Return the exact Operator API origin confirmed by the vendor."""
    return OPERATOR_API_BASE_URL
