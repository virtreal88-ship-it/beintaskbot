"""Task-specific ownership rules. responsible_id remains the executor identity."""
from tenant_policy import TenantPolicy
from tenant_scoped_access import scoped_access_enabled
from tenant_scoped_access_store import resource_scope_sql


def _same(left, right):
    return left is not None and right is not None and str(left) == str(right)


def assigned(policy, row, actor_id):
    return bool(row and policy.profile.get('active') is not False
        and _same(policy.profile.get('telegram_id'), actor_id)
        and _same(policy.profile.get('tenant_id'), row.get('tenant_id'))
        and _same(row.get('responsible_id'), actor_id))


def manager_scope(policy, deal):
    return any(str(deal.get('pipeline_id')) == str(item['pipeline_id'])
        and (not item['status_ids'] or str(deal.get('stage_id')) in
             {str(value) for value in item['status_ids']}) for item in policy.pipeline_scope())


def allows(policy, row, actor_id, operation='view'):
    """Independent CRUD. Assignment grants view/completion, never general U/D."""
    if not row or policy.profile.get('active') is False:
        return False
    if not _same(row.get('tenant_id'), policy.profile.get('tenant_id')):
        return False
    if scoped_access_enabled() and operation in {'view', 'complete'} and assigned(policy, row, actor_id):
        return True
    action = 'update' if operation in {'complete', 'reopen'} else operation
    if action not in {'view', 'create', 'update', 'delete'}:
        return False
    if policy.has_scoped_access('tasks', action):
        return policy.allows_resource('tasks', action, {**row,
            'responsible_member_id': row.get('access_responsible_id'),
            'responsible_department_id': row.get('access_responsible_department_id')})
    if not policy.allows_action('tasks', action):
        return False
    if policy.privileged:
        return True
    if int(actor_id) not in {int(row.get('responsible_id') or 0), int(row.get('created_by') or 0)}:
        return False
    if operation == 'delete' and not _same(row.get('created_by'), actor_id):
        return False
    return policy.profile.get('role') != 'manager' or manager_scope(policy, row)


def capabilities(policy, row, actor_id):
    return {name: allows(policy, row, actor_id, name)
            for name in ('view', 'update', 'complete', 'reopen', 'delete')}


def read_where(profile):
    """Tenant-bounded SQL shared by search, details and command-status reads."""
    policy = TenantPolicy(profile)
    actor = profile['telegram_id']
    tenant_clause = 't.tenant_id=%s::uuid AND t.archived_at IS NULL'
    if profile.get('active') is False:
        return tenant_clause+' AND FALSE', [profile['tenant_id']]
    if policy.has_scoped_access('tasks', 'view'):
        clause, args = resource_scope_sql(profile, 'tasks', 'view', alias='t',
                                         responsible_column='access_responsible_id')
        return tenant_clause+' AND (('+clause+') OR t.responsible_id=%s)', [profile['tenant_id'], *args, actor]
    if policy.privileged and policy.allows_action('tasks', 'view'):
        return tenant_clause, [profile['tenant_id']]
    clause, args = 'FALSE', []
    if policy.allows_action('tasks', 'view'):
        clause = '(t.responsible_id=%s OR t.created_by=%s)'
        args = [actor, actor]
        if profile.get('role') == 'manager':
            parts = []
            for item in policy.pipeline_scope():
                part = 'd.pipeline_id=%s'; args.append(str(item['pipeline_id']))
                if item['status_ids']:
                    part += ' AND d.stage_id=ANY(%s::text[])'
                    args.append([str(value) for value in item['status_ids']])
                parts.append('('+part+')')
            clause += ' AND ('+(' OR '.join(parts) or 'FALSE')+')'
    if scoped_access_enabled():
        return tenant_clause+' AND (('+clause+') OR t.responsible_id=%s)', [profile['tenant_id'], *args, actor]
    return tenant_clause+' AND ('+clause+')', [profile['tenant_id'], *args]
