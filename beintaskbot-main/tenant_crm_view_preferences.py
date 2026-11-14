"""Shared personal CRM source storage. No grants or resource access are changed."""
from tenant_platform_core import _json, TenantPlatformError


def validate_source(value, message='Tapşırıq mənbəyi düzgün deyil.'):
    if not isinstance(value, str) or value not in ('native', 'kommo'):
        raise TenantPlatformError(message)
    return value


def source_preferences(*, kind, tenant_id, actor_id, policy, allowed, connect, source=None,
                       invalid_message='Tapşırıq mənbəyi düzgün deyil.',
                       forbidden_message='Tapşırıq mənbəyi üçün icazə yoxdur.'):
    # Static allowlist: callers cannot choose an arbitrary member policy subkey.
    key = {'tasks': 'task_view', 'deals': 'deal_view'}[kind]
    if not allowed:
        raise TenantPlatformError(forbidden_message)
    existing = policy.member_settings.get(key)
    saved = existing.get('source') if isinstance(existing, dict) else None
    if not isinstance(saved, str) or saved not in ('native', 'kommo'):
        saved = 'kommo' if kind == 'deals' else 'native'
    if source is not None:
        saved = validate_source(source, invalid_message)
        if saved not in allowed:
            raise TenantPlatformError(forbidden_message)
        actor_key = str(int(actor_id))
        value = _json({'source': saved})
        with connect() as conn:
            with conn.cursor() as cur:
                # Atomic personal subkey update, preserving every grant and user.
                cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
                    VALUES(%s::uuid,'members',jsonb_build_object(%s::text,jsonb_build_object(%s::text,%s::jsonb)),%s)
                    ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=jsonb_set(
                        jsonb_set(saas_tenant_workflow_policies.value,ARRAY[%s::text]::text[],
                            COALESCE(saas_tenant_workflow_policies.value->(%s::text),'{}'::jsonb),TRUE),
                        ARRAY[%s::text,%s::text]::text[],%s::jsonb,TRUE),
                        updated_by=%s,updated_at=now()''',
                    (tenant_id, actor_key, key, value, int(actor_id), actor_key, actor_key, actor_key, key, value, int(actor_id)))
            conn.commit()
    return {'source': saved}
