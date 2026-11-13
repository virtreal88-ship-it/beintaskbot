"""Fail-closed workspace and feature gates for the private Operator provider."""
from __future__ import annotations

import os
import uuid


WORKSPACE_ENV = "BEINSYSTEMS_OPERATOR_TENANT_ID"
API_BASE_URL_ENV = "BEINSYSTEMS_OPERATOR_API_BASE_URL"
PROVIDER = "beinsystems_operator"


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
    """Read the Operator host only from server configuration, never a request."""
    from urllib.parse import urlsplit

    value = str(os.environ.get(API_BASE_URL_ENV) or "").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            return ""
        if parsed.port not in (None, 443):
            return ""
        if any(ord(char) < 32 for char in value):
            return ""
        return value
    except (TypeError, ValueError):
        return ""
