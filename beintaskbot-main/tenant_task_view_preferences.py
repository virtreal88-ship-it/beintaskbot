"""Personal task-source presentation, never an access grant."""
from tenant_platform_core import _connect, TenantPlatformError
from tenant_member_store import member
from tenant_policy import TenantPolicy
from tenant_scoped_access import ACTIVE_TENANT_STATUSES
from tenant_task_flow import _sources
from tenant_crm_view_preferences import source_preferences, validate_source


def preferences(*, tenant_id: str, actor_id: int, source=None) -> dict:
    current = member(tenant_id, int(actor_id))
    if (not current or current.get('active') is not True
            or current.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES):
        raise TenantPlatformError('Tapşırıqlar üçün icazə yoxdur.')
    policy = TenantPolicy(current)
    allowed = {item['source'] for item in _sources(policy) if item['view']}
    if not allowed:
        raise TenantPlatformError('Tapşırıqlar üçün icazə yoxdur.')
    return source_preferences(kind='tasks', tenant_id=tenant_id, actor_id=actor_id,
        policy=policy, allowed=allowed, connect=_connect, source=source)
