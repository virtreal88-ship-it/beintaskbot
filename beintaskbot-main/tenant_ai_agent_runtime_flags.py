"""Fail-closed runtime switches shared by AI Agent side-effect paths."""
import os


_TRUE = {"1", "true", "yes", "on"}
_STAGING_FORBIDDEN = {"AI_AGENTS_AUTO_SEND_ENABLED", "AI_AGENTS_CRM_ENABLED"}


def staging_mode() -> bool:
    return str(os.environ.get("STAGING_MODE") or "").strip().casefold() in _TRUE


def enabled(name: str) -> bool:
    """Read a feature flag, forcing external AI side effects off in staging."""
    if staging_mode() and name in _STAGING_FORBIDDEN:
        return False
    return str(os.environ.get(name) or "").strip().casefold() in _TRUE
