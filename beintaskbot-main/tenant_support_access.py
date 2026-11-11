"""Independent support-inbox actions; never authorizes a general dialog read."""
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access
from tenant_scoped_access_store import resource_scope_sql, owned_resource_metadata


def policy_access(cur, profile, action='view'):
    return access(cur, profile, 'support', action)


def predicate(policy, action='view', alias='t'):
    # Sparse cells preserve the old support permission (not generic CRUD rules).
    if policy.action_scope('support', action) is None:
        return ('TRUE', []) if policy.legacy_allows('support') else ('FALSE', [])
    return resource_scope_sql(policy.profile, 'support', action, alias)


def require(cur, profile, ticket_id, action='view', *, lock=False):
    tenant, actor, policy = policy_access(cur, profile, action)
    # Only scoped requests need another resource query. Legacy callers retain
    # their original missing-row and role/source checks exactly.
    if policy.action_scope('support', action) is not None:
        cur.execute('SELECT * FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid'
                    + (' FOR UPDATE' if lock else ''), (tenant, identifier(ticket_id)))
        row = cur.fetchone()
        if not row or not policy.allows_resource('support', action,
                owned_resource_metadata(cur, tenant, row, lock=True)):
            raise TenantLinearError('Обращение недоступно.', 404)
    return tenant, actor, policy


def require_create(cur, profile):
    tenant, actor, policy = policy_access(cur, profile, 'create')
    if policy.action_scope('support', 'create') is not None:
        resource = {'tenant_id': tenant, 'responsible_member_id': actor,
                    'responsible_department_id': policy.profile.get('department_id')}
        if not policy.allows_resource('support', 'create', resource):
            raise TenantLinearError('Нет доступа к созданию обращения.', 403)
    return tenant, actor, policy


def mutation_result(profile, ticket_id):
    # Independent writes may succeed without read permission. Never commit a
    # mutation then throw from detail(), nor include protected document content.
    from tenant_support_inbox_store import _connect, detail
    from tenant_scoped_access import scoped_access_enabled
    if not scoped_access_enabled():
        return detail(profile,ticket_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            try:
                require(cur, profile, ticket_id)
            except TenantLinearError as error:
                if error.status not in (403, 404): raise
                return {'ticket': {'id': str(ticket_id)}, 'readable': False}
    from tenant_support_inbox_store import detail
    return detail(profile, ticket_id)


def sql_filter(policy, alias):
    """Scoped-only filter for legacy SQL projections with fixed parameter order.

    Caller aliases are validated; identity literals are UUID/int conversions.
    An absent cell preserves the projection's deliberate legacy context rules.
    """
    import re
    import uuid
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', alias):
        raise ValueError('invalid_scope_sql_identifier')
    scope = policy.action_scope('support','view')
    if scope is None: return 'TRUE'
    if scope == 'none': return 'FALSE'
    tenant = str(uuid.UUID(str(policy.profile['tenant_id'])))
    actor = int(policy.profile['telegram_id'])
    boundary = f"{alias}.tenant_id='{tenant}'::uuid"
    if scope == 'all': return boundary
    if scope == 'self': return f'({boundary} AND {alias}.access_responsible_id={actor})'
    return f'''({boundary} AND EXISTS(SELECT 1 FROM saas_tenant_members sr
      JOIN saas_tenant_members me ON me.tenant_id=sr.tenant_id
        AND me.telegram_id={actor} AND me.active=TRUE
      WHERE sr.tenant_id={alias}.tenant_id AND sr.telegram_id={alias}.access_responsible_id
        AND sr.department_id IS NOT NULL AND sr.department_id=me.department_id))'''
