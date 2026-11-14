"""Personal Kommo board presentation, never an access grant."""
import re

from tenant_platform_core import _connect, _json, TenantPlatformError
from tenant_member_store import member
from tenant_policy import TenantPolicy

_KEY = re.compile(r'[1-9][0-9]{0,18}:[1-9][0-9]{0,18}\Z')
_MAX_HIDDEN = 1000


def validate_hidden(value, allowed):
    if not isinstance(value, list) or len(value) > _MAX_HIDDEN:
        raise TenantPlatformError('Sütun seçimi düzgün deyil.')
    if any(not isinstance(key, str) or not _KEY.fullmatch(key) or key not in allowed for key in value):
        raise TenantPlatformError('Sütun seçimi düzgün deyil.')
    return list(dict.fromkeys(value))


def preferences(*, tenant_id: str, actor_id: int, hidden=None) -> dict:
    current = member(tenant_id, int(actor_id))
    if not current or not TenantPolicy(current).kommo_access():
        raise TenantPlatformError('Kommo üçün icazə yoxdur.')
    policy = TenantPolicy(current)
    catalog = policy.deal_board()
    allowed = {f"{pipeline['pipeline_id']}:{stage['stage_id']}"
               for pipeline in catalog for stage in pipeline.get('stages', [])}
    existing = policy.member_settings.get('kommo_deal_board') or {}
    saved = existing.get('hidden_stage_keys', []) if isinstance(existing, dict) else []
    saved = [key for key in saved if isinstance(key, str) and key in allowed] if isinstance(saved, list) else []
    if hidden is not None:
        saved = validate_hidden(hidden, allowed)
        actor_key = str(int(actor_id))
        value = _json({'hidden_stage_keys': saved})
        with _connect() as conn:
            with conn.cursor() as cur:
                # Only this actor's presentation subkey changes. The UPSERT takes
                # the policy row lock and preserves unrelated users and grants.
                cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
                    VALUES(%s::uuid,'members',jsonb_build_object(%s::text,jsonb_build_object('kommo_deal_board',%s::jsonb)),%s)
                    ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=jsonb_set(
                        jsonb_set(saas_tenant_workflow_policies.value,ARRAY[%s::text]::text[],
                            COALESCE(saas_tenant_workflow_policies.value->(%s::text),'{}'::jsonb),TRUE),
                        ARRAY[%s::text,'kommo_deal_board']::text[],%s::jsonb,TRUE),
                        updated_by=%s,updated_at=now()''',
                    (tenant_id, actor_key, value, int(actor_id), actor_key, actor_key, actor_key, value, int(actor_id)))
            conn.commit()
    return {'catalog': catalog, 'hidden_stage_keys': saved}
