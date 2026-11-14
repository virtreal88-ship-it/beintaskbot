"""Personal deal-source selection using existing source-specific read authority."""
from tenant_platform_core import _connect, TenantPlatformError
from tenant_member_store import member
from tenant_policy import TenantPolicy
from tenant_scoped_access import ACTIVE_TENANT_STATUSES
from tenant_crm_request_context import native_policy_profile
from tenant_crm_view_preferences import source_preferences


def preferences(*, tenant_id: str, actor_id: int, source=None) -> dict:
    current = member(tenant_id, int(actor_id))
    if (not current or current.get('active') is not True
            or current.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES):
        raise TenantPlatformError('Sövdələşmələr üçün icazə yoxdur.')
    policy = TenantPolicy(current)
    native = TenantPolicy(native_policy_profile(current))
    # Match the native deal GET boundary, including explicit scoped view=none.
    # Kommo pipeline/identity access is independent of native CRM read grants.
    allowed = {name for name, permitted in (
        ('native', native.allows_action('deals', 'view')),
        ('kommo', policy.kommo_access())) if permitted}
    return source_preferences(kind='deals', tenant_id=tenant_id, actor_id=actor_id,
        policy=policy, allowed=allowed, connect=_connect, source=source,
        invalid_message='Sövdələşmə mənbəyi düzgün deyil.',
        forbidden_message='Sövdələşmə mənbəyi üçün icazə yoxdur.')
