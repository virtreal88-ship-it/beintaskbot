"""Resolve an authenticated legacy webapp identity to one SaaS tenant."""


def member_from_request(request, tenants_for_user, member_lookup, logger=None,
                        account_tenants_for_user=None):
    """Return one active member, scoped to the legacy app's Kommo account.

    ``account_tenants_for_user`` is used by the legacy ``/webapp`` bridge. It
    must return memberships joined to a connected Kommo integration whose
    account domain exactly matches the configured legacy account. Once that
    resolver is supplied, an unscoped membership is never used as fallback.
    """
    try:
        telegram_id = int(request.get("authenticated_chat_id") or 0)
    except (TypeError, ValueError):
        return None
    if telegram_id <= 0:
        return None
    try:
        candidates = (account_tenants_for_user(telegram_id)
                      if account_tenants_for_user is not None
                      else tenants_for_user(telegram_id))
        active = [item for item in candidates
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
