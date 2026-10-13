"""Resolve an authenticated legacy webapp identity to one SaaS tenant."""


def member_from_request(request, tenants_for_user, member_lookup, logger=None):
    """Return a member only when the session maps to exactly one active tenant."""
    try:
        telegram_id = int(request.get("authenticated_chat_id") or 0)
    except (TypeError, ValueError):
        return None
    if telegram_id <= 0:
        return None
    try:
        active = [item for item in tenants_for_user(telegram_id)
                  if str(item.get("status") or "") == "active"]
        if len(active) != 1:
            return None
        member = member_lookup(str(active[0].get("id") or ""), telegram_id)
    except Exception:
        if logger:
            logger.error("Legacy webapp AI Agents tenant lookup failed")
        return None
    if not member or str(member.get("tenant_status") or "") != "active":
        return None
    return member
