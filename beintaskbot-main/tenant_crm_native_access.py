"""Current, transaction-bound authorization for native CRM stores.

The optional actor is absent only for trusted internal integrations. HTTP
boundaries always pass the authenticated actor; their request snapshot alone
never authorizes a database read or mutation.
"""
from tenant_policy import TenantPolicy


class CRMAccessError(ValueError):
    def __init__(self, code='forbidden', status=403):
        super().__init__(code)
        self.code, self.status = code, status


def current_access(cur, tenant, actor, module, action):
    if actor is None:
        return None
    from tenant_scoped_access_store import read_access_profile
    profile = read_access_profile(cur, tenant, actor, lock=True)
    if (not profile or profile.get('active') is not True
            or str(profile.get('tenant_id')) != str(tenant)
            or not TenantPolicy(profile).allows_action(module, action)):
        raise CRMAccessError()
    return profile


def scoped_sql(profile, module, action, alias):
    if profile is None:
        return 'TRUE', []
    if not TenantPolicy(profile).allows_action(module, action):
        return 'FALSE', []
    from tenant_scoped_access_store import resource_scope_sql
    return resource_scope_sql(profile, module, action, alias=alias,
                              responsible_column='access_responsible_id')



def related_scope_sql(profile, module, alias):
    """Retain legacy embedded-list behavior until that section is scoped."""
    if profile is None or not TenantPolicy(profile).has_scoped_access(module):
        return 'TRUE', []
    return scoped_sql(profile, module, 'view', alias)



def require_defined_relation(profile, *modules):
    """Relationship semantics are a rollout prerequisite, not an implicit grant."""
    if profile is not None and any(TenantPolicy(profile).has_scoped_access(module) for module in modules):
        raise CRMAccessError('scoped_relation_not_configured', 409)


def authorize_create(cur, profile, module, tenant, actor):
    if profile is None:
        return
    predicate, params = scoped_sql(profile, module, 'create', 'r')
    cur.execute(f'''SELECT 1 FROM (SELECT %s::uuid AS tenant_id,
      %s::bigint AS access_responsible_id) r WHERE {predicate}''',
      [tenant, actor, *params])
    if not cur.fetchone():
        raise CRMAccessError()


def locked_resource(cur, profile, module, action, table, tenant, ident):
    # All identifiers are constant literals supplied by native store code.
    predicate, params = scoped_sql(profile, module, action, 'r')
    cur.execute(f'''SELECT r.* FROM {table} r WHERE r.tenant_id=%s::uuid
      AND r.id=%s::uuid AND r.archived_at IS NULL AND ({predicate}) FOR UPDATE OF r''',
      [tenant, ident, *params])
    row = cur.fetchone()
    if row and profile is not None and TenantPolicy(profile).action_scope(module, action) == 'department':
        from tenant_scoped_access_store import owned_resource_metadata
        resource = owned_resource_metadata(cur, tenant, row, lock=True)
        if not TenantPolicy(profile).allows_resource(module, action, resource):
            return None
    return row


def action_columns(profile, module, alias):
    if profile is None:
        return '', []
    columns, params = [], []
    for action in ('view', 'update', 'delete'):
        predicate, values = scoped_sql(profile, module, action, alias)
        columns.append(f',({predicate}) AS _allowed_{action}')
        params.extend(values)
    return ''.join(columns), params


def public_row(row):
    if not row:
        return row
    row = dict(row)
    row.pop('access_responsible_id', None)
    actions = {}
    for action, public in (('view', 'view'), ('update', 'update'), ('delete', 'archive')):
        key = '_allowed_' + action
        if key in row:
            actions[public] = bool(row.pop(key))
    if actions:
        row['actions'] = actions
    return row


def require_local_relation(cur, tenant, entity_type):
    """No scoped relation may trigger an ambiguous full-record provider export.

    The current outbound queue has no field-scoped relationship/unlink event.
    Hold the existing sync setting stable for the local transaction and deny
    an active outbound configuration until that separate contract is defined.
    """
    cur.execute("""SELECT sync_direction,allowed_fields FROM saas_crm_provider_settings
        WHERE tenant_id=%s::uuid AND entity_type=%s FOR SHARE""", (tenant, entity_type))
    settings = cur.fetchone() or {}
    if settings.get('sync_direction') in {'to_external','bidirectional'} and settings.get('allowed_fields'):
        raise CRMAccessError('scoped_relation_sync_not_configured', 409)
