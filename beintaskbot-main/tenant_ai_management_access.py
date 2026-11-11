"""Fail-closed boundary for AI diagnostics not yet partitioned by source.

This is not an AI-management grant. Callers must still apply their existing
management authorization. Unconverted aggregate readers must not turn a
narrow dialog grant into tenant-wide channel/customer metadata access.
"""
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy
from tenant_scoped_access import scoped_access_enabled
from tenant_scoped_access_store import read_access_profile


def require_unrestricted_dialog_read(cur, profile):
    if not scoped_access_enabled():
        return
    current = read_access_profile(cur, profile.get('tenant_id'), profile.get('telegram_id'), lock=True)
    if not current:
        raise TenantLinearError('İcazə yoxdur.', 403)
    policy = TenantPolicy(current)
    if policy.action_scope('support','view') not in {None,'all'}:
        raise TenantLinearError('ai_diagnostics_require_unrestricted_support_read',403)
    scope = policy.action_scope('dialogs', 'view')
    if scope not in {None, 'all'}:
        raise TenantLinearError('ai_diagnostics_require_unrestricted_dialog_read', 403)
