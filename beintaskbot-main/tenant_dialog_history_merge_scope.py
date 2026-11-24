"""Fail-closed deployment scope for the optional dialogue history join."""
import os
import uuid

FLAG = 'DIALOG_HISTORY_MERGE_ENABLED'
SCOPE = 'DIALOG_HISTORY_MERGE_SCOPE'
MAX_ACTIVE_PAIRS = 100


def configured():
    """An exact canonical tenant UUID and exact source UUID, or no pilot."""
    if os.environ.get(FLAG) != '1':
        return None
    raw = os.environ.get(SCOPE, '')
    if len(raw) != 73:
        return None
    try:
        tenant, source = raw.split(':')
        if tuple(map(str, (uuid.UUID(tenant), uuid.UUID(source)))) == (tenant, source):
            return tenant, source
    except ValueError:
        pass
    return None


def source_for(tenant):
    scope = configured()
    return scope[1] if scope and str(tenant) == scope[0] else None


def allows(tenant, source):
    scoped = source_for(tenant)
    return bool(scoped and str(source) == scoped)
