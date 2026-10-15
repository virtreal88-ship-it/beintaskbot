"""Lifecycle eligibility for the existing tenant AI Agents module."""

SETUP_TENANT_STATUSES = frozenset({'onboarding', 'ready_for_integration'})


def tenant_status_allows_ai(status: object, provider: object = 'kommo') -> bool:
    """Permit setup-stage Connect tenants; archived/suspended remain closed."""
    value = str(status or '').casefold()
    if value == 'active':
        return True
    return str(provider or '').casefold() == 'connect' and value in SETUP_TENANT_STATUSES


def tenant_status_allows_management(status: object) -> bool:
    """Owners can configure AI before onboarding activation, but not archived tenants."""
    value = str(status or '').casefold()
    return value == 'active' or value in SETUP_TENANT_STATUSES
