"""Personal task-source presentation, never an access grant."""
from tenant_platform_core import _connect, _json, TenantPlatformError
from tenant_member_store import member
from tenant_policy import TenantPolicy
from tenant_scoped_access import ACTIVE_TENANT_STATUSES
from tenant_task_flow import _sources


def validate_source(value):
    if not isinstance(value, str) or value not in ('native', 'kommo'):
        raise TenantPlatformError('Tapşırıq mənbəyi düzgün deyil.')
    return value


def preferences(*, tenant_id: str, actor_id: int, source=None) -> dict:
    current = member(tenant_id, int(actor_id))
    if (not current or current.get('active') is not True
            or current.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES):
        raise TenantPlatformError('Tapşırıqlar üçün icazə yoxdur.')
    policy = TenantPolicy(current)
    allowed = {item['source'] for item in _sources(policy) if item['view']}
    if not allowed:
        raise TenantPlatformError('Tapşırıqlar üçün icazə yoxdur.')
    existing = policy.member_settings.get('task_view')
    saved = existing.get('source') if isinstance(existing, dict) else None
    if not isinstance(saved, str) or saved not in ('native', 'kommo'):
        saved = 'native'
    if source is not None:
        saved = validate_source(source)
        if saved not in allowed:
            raise TenantPlatformError('Tapşırıq mənbəyi üçün icazə yoxdur.')
        actor_key = str(int(actor_id))
        value = _json({'source': saved})
        with _connect() as conn:
            with conn.cursor() as cur:
                # Atomic personal subkey update, preserving every grant and user.
                cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
                    VALUES(%s::uuid,'members',jsonb_build_object(%s::text,jsonb_build_object('task_view',%s::jsonb)),%s)
                    ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=jsonb_set(
                        jsonb_set(saas_tenant_workflow_policies.value,ARRAY[%s::text]::text[],
                            COALESCE(saas_tenant_workflow_policies.value->(%s::text),'{}'::jsonb),TRUE),
                        ARRAY[%s::text,'task_view']::text[],%s::jsonb,TRUE),
                        updated_by=%s,updated_at=now()''',
                    (tenant_id, actor_key, value, int(actor_id), actor_key, actor_key, actor_key, value, int(actor_id)))
            conn.commit()
    return {'source': saved}
