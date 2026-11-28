"""Personal dialogue presentation preferences, not authorization or customer data."""
import re
from tenant_platform_core import _connect, _json
from tenant_operator_access import access
from tenant_linear_policy import TenantLinearError
from tenant_operator_dialogs import projection, runtime_flag, _scoped_ai_documents, ensure_schema
from tenant_connect_transport import _schema as ensure_connect_schema

DEFAULTS = {'channel': '', 'agent': '', 'state': '', 'lifecycle': 'all'}
STATES = {'', 'automatic', 'manual', 'needs_assignment', 'attention', 'no_agent'}
LIFECYCLES = {'all', 'active', 'working', 'completed'}
_AGENT = re.compile(r'(?:unassigned|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z')


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise TenantLinearError('Некорректные фильтры диалогов.', 400)
    if any(not isinstance(v, str) for v in value.values()):
        raise TenantLinearError('Некорректные фильтры диалогов.', 400)
    channel = value['channel']
    if (len(channel) > 250 or any(ord(c) < 32 for c in channel)
            or (channel and not channel.startswith(('connect:', 'kommo:')))
            or (value['agent'] and not _AGENT.fullmatch(value['agent']))
            or value['state'] not in STATES or value['lifecycle'] not in LIFECYCLES):
        raise TenantLinearError('Некорректные фильтры диалогов.', 400)
    return dict(value)


def _available(cur, tenant, policy, value):
    """Revalidate saved selections without listing/enriching or marking dialogs read."""
    result = dict(value)
    if result['channel']:
        # Exact list source/route visibility, but no history, enrichment or locks
        # on individual conversations. A preference never bypasses list guards.
        sql, scopes = projection(cur, policy)
        cur.execute(sql + ''' SELECT EXISTS(SELECT 1 FROM visible
          WHERE provider||':'||channel_id=%s) AS allowed''',
          (tenant, tenant, *scopes, runtime_flag('AI_AGENTS_ENABLED'), result['channel']))
        if not cur.fetchone()['allowed']:
            result['channel'] = ''
    if _scoped_ai_documents(policy):
        result['agent'] = result['state'] = ''
    elif result['agent'] not in {'', 'unassigned'}:
        cur.execute('''SELECT EXISTS(SELECT 1 FROM saas_ai_agents
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL) AS allowed''',
          (tenant, result['agent']))
        if not cur.fetchone()['allowed']:
            result['agent'] = ''
    return result


def preferences(profile, value=None):
    if value is not None:
        value = validate(value)
    with _connect() as conn:
        # Preferences restore before the first list request; use its cached
        # initialization protocol so fresh deployments have the same prerequisites.
        ensure_schema(conn)
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy = access(cur, profile, 'dialogs')
            saved = policy.member_settings.get('dialog_view')
            if value is None:
                try:
                    value = validate(saved)
                except TenantLinearError:
                    value = dict(DEFAULTS)
                return _available(cur, tenant, policy, value)
            value = _available(cur, tenant, policy, value)
            _save_member_preference(cur, tenant, actor, 'dialog_view', value)
        conn.commit()
    return value


def _save_member_preference(cur, tenant, actor, key, value):
    actor_key = str(actor)
    encoded = _json(value)
    # Atomic update to this member's single presentation subkey only.
    cur.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
        VALUES(%s::uuid,'members',jsonb_build_object(%s::text,jsonb_build_object(%s::text,%s::jsonb)),%s)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=jsonb_set(
            jsonb_set(saas_tenant_workflow_policies.value,ARRAY[%s::text]::text[],
                COALESCE(saas_tenant_workflow_policies.value->(%s::text),'{}'::jsonb),TRUE),
            ARRAY[%s::text,%s::text]::text[],%s::jsonb,TRUE),
            updated_by=%s,updated_at=now()''',
        (tenant, actor_key, key, encoded, actor, actor_key, actor_key, actor_key, key, encoded, actor))


def company_panel_preferences(profile, value=None):
    if value is not None and (not isinstance(value, dict) or set(value) != {'open'}
                              or type(value['open']) is not bool):
        raise TenantLinearError('Некорректное состояние панели компании.', 400)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy = access(cur, profile, 'dialogs')
            if value is None:
                return {'open': policy.member_settings.get('dialog_company_panel_open') is True}
            _save_member_preference(cur, tenant, actor, 'dialog_company_panel_open', value['open'])
        conn.commit()
    return value
