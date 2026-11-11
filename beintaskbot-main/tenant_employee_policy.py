"""Independent employee-card eligibility and resource predicates."""
from tenant_policy import TenantPolicy, policy_values, ROLE_PERMISSIONS

def employee_action_allowed(current, action):
    """Admission only: target authorization is independently required."""
    if not current or current.get('active') is False:
        return False
    from tenant_scoped_access import ACTIVE_TENANT_STATUSES
    if current.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES:
        return False
    policy = TenantPolicy(current)
    scope = policy.action_scope('employees', action)
    if action == 'create' and scope == 'department' and not current.get('department_id'):
        return False
    return scope != 'none' if scope is not None else policy.legacy_allows_action('employees', 'manage')


def employee_target_allowed(current, target, action):
    if not target or not employee_action_allowed(current, action):
        return False
    if str(current.get('tenant_id')) != str(target.get('tenant_id')):
        return False
    scope = TenantPolicy(current).action_scope('employees', action)
    if scope in (None, 'all'):
        return True
    if scope == 'self':
        return int(current['telegram_id']) == int(target['telegram_id'])
    return bool(scope == 'department' and current.get('department_id')
                and str(current['department_id']) == str(target.get('department_id')))


def _unbounded_action(policy, module, action):
    scope = policy.action_scope(module, action)
    return scope == 'all' if scope is not None else policy.allows_action(module, action)


def _unbounded_module(policy, module):
    from tenant_scoped_access import SCOPE_ACTION_CATALOG
    return policy.legacy_allows(module) and all(
        policy.action_scope(module, action) in (None, 'all')
        for action in SCOPE_ACTION_CATALOG.get(module, ()))


def _source_policy(policy):
    # Matrix All is not itself a narrower source restriction. Compare the
    # actual legacy provider/pipeline rules without its coarse rollout guard,
    # keeping every other member/domain setting intact.
    workflow = dict(policy.profile.get('workflow') or {})
    policies = dict(policy_values(workflow))
    members = dict(policies.get('members') or {})
    key = str(policy.profile.get('telegram_id'))
    member = dict(members.get(key) or {})
    member.pop('access_scopes', None)
    members[key] = member
    policies['members'] = members
    workflow['policies'] = policies
    return TenantPolicy({**policy.profile, 'workflow': workflow})


def _task_scope_contains(container, scope):
    if scope.get('kind') == 'denied' or container.get('kind') == 'all':
        return True
    if container.get('kind') != scope.get('kind'):
        return False
    if scope.get('kind') == 'marker':
        return container.get('marker') == scope.get('marker')
    if scope.get('kind') == 'pipelines':
        available = {row['pipeline_id']: row.get('status_ids', []) for row in container.get('pipelines', [])}
        for row in scope.get('pipelines', []):
            statuses = available.get(row['pipeline_id'])
            if statuses is None or statuses and (not row.get('status_ids') or set(row['status_ids']) - set(statuses)):
                return False
        return True
    return container == scope


def _domain_expansions_allowed(actor, previous, proposed):
    """Domain policies include restrictions not represented by catalog CRUD."""
    from tenant_hot_order_policy import HotOrderPolicy
    modules = set(ROLE_PERMISSIONS['owner']) | {str(grant).split(':', 1)[0] for grant in proposed.profile.get('permissions', [])}
    if any(proposed.allows(module) and not previous.allows(module) and not _unbounded_module(actor, module) for module in modules):
        return False
    source_actor = _source_policy(actor)
    source_hot, old_hot, new_hot = map(HotOrderPolicy, (source_actor, previous, proposed))
    for action in ('create', 'claim'):
        if new_hot.allowed(action) and not old_hot.allowed(action) and not source_hot.allowed(action):
            return False
    hot_added = proposed.allows('hot_orders') and not previous.allows('hot_orders')
    if hot_added and source_hot.requires_completion_approval() and not new_hot.requires_completion_approval():
        return False
    old_tasks, new_tasks = previous.task_scope(), proposed.task_scope()
    if not _task_scope_contains(old_tasks, new_tasks) and not _task_scope_contains(source_actor.task_scope(), new_tasks):
        return False
    # Compare actual target pipeline/stage sets, never copy the creator's
    # assignments or treat two different personal markers as equivalent.
    def crm_scope(policy):
        return {'kind': 'pipelines', 'pipelines': policy.pipeline_scope()
            if policy.can_read_legacy_crm() else []}
    old_crm, new_crm = crm_scope(previous), crm_scope(proposed)
    if not _task_scope_contains(old_crm, new_crm) and not _task_scope_contains(crm_scope(source_actor), new_crm):
        return False
    task_added = any(proposed.allows_action('tasks', action) and not previous.allows_action('tasks', action)
                     for action in ('create', 'update'))
    if task_added:
        for completion in (False, True):
            if (actor.requires_task_approval(creator_id=0, executor_id=0, completion=completion)
                    and not proposed.requires_task_approval(creator_id=0, executor_id=0, completion=completion)):
                return False
    deal_added = any(proposed.allows_action('deals', action) and not previous.allows_action('deals', action)
                     for action in ('create', 'update'))
    if deal_added and actor.requires_deal_approval() and not proposed.requires_deal_approval():
        return False
    return True


def delegated_permissions_allowed(current, permissions, *, role=None, recipient_id=0):
    """Reject any broader effective authority, including aliases/inheritance.

    Creation of ordinary roles never copies the creator's member restrictions.
    Evaluate recipients without those settings, not merely equal module names.
    The option picker checks every ordinary role; writes may specify a role.
    """
    if current.get('role') == 'owner':
        return True
    if set(permissions) - set(current.get('permissions') or []):
        return False
    from tenant_scoped_access import scoped_access_enabled, SCOPE_ACTION_CATALOG
    if not scoped_access_enabled():
        return True
    policy = TenantPolicy(current)
    for grant in permissions:
        module, separator, action = str(grant).partition(':')
        if not separator and not _unbounded_module(policy, module):
            return False
        if separator:
            actions = SCOPE_ACTION_CATALOG.get(module, ('view', 'create', 'update', 'delete')) if action == 'manage' else (action,)
            if any(not _unbounded_action(policy, module, item) for item in actions):
                return False
    roles = (role,) if role else ('worker', 'operator', 'manager', 'master')
    # Preserve company policy/module settings, but never import the creator's
    # per-member scopes, native overrides or administrative flags.
    workflow = dict(current.get('workflow') or {})
    workflow['policies'] = {key: value for key, value in policy_values(workflow).items()
                            if key != 'members'}
    for recipient_role in roles:
        recipient = TenantPolicy({**current, 'telegram_id': recipient_id, 'role': recipient_role,
            'permissions': list(permissions), 'workflow': workflow})
        baseline = TenantPolicy({**current, 'telegram_id': recipient_id, 'role': recipient_role,
            'permissions': [], 'workflow': workflow})
        if not _domain_expansions_allowed(policy, baseline, recipient):
            return False
        for module, actions in SCOPE_ACTION_CATALOG.items():
            # In particular customers grants companies through its legacy
            # fallback; AI grants can feed dialogs/support/reports. Test the
            # resulting policy for every implemented domain instead of aliases.
            for action in actions:
                if recipient.allows_action(module, action) and not baseline.allows_action(module, action):
                    if not _unbounded_action(policy, module, action):
                        return False
            if recipient.allows(module) and not baseline.allows(module):
                if not _unbounded_module(policy, module):
                    return False
    return True


def creation_permission_allowed(current, grant):
    return delegated_permissions_allowed(current, [grant])


def member_permission_change_allowed(current, existing, role, permissions):
    """Attenuate added/expanded authority while preserving retained rights."""
    if current.get('role') == 'owner':
        return True
    from tenant_scoped_access import scoped_access_enabled, SCOPE_ACTION_CATALOG
    if not scoped_access_enabled():
        return True
    added = set(permissions) - set(existing.get('permissions') or [])
    if not delegated_permissions_allowed(current, added, role=role, recipient_id=existing['telegram_id']):
        return False
    policy = TenantPolicy(current)
    common = {**current, **existing, 'active': True,
              'workflow': current.get('workflow') or {}, 'modules': current.get('modules') or {}}
    previous = TenantPolicy(common)
    proposed = TenantPolicy({**common, 'role': role, 'permissions': list(permissions)})
    if not _domain_expansions_allowed(policy, previous, proposed):
        return False
    for module, actions in SCOPE_ACTION_CATALOG.items():
        for action in actions:
            if proposed.allows_action(module, action) and not previous.allows_action(module, action):
                if not _unbounded_action(policy, module, action):
                    return False
        if proposed.allows(module) and not previous.allows(module):
            if not _unbounded_module(policy, module):
                return False
    return True
