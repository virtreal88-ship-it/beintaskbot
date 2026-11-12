"""Shared tenant-scoped access rules, enabled only by explicit rollout.

Callers must obtain current authenticated membership and tenant-bound resource
metadata from the database, and revalidate within the mutation transaction.
"""

SCOPES = frozenset(('none', 'self', 'department', 'all'))


def _id(value):
    """Compare DB identity values without equating missing identities."""
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value or None


def _same(left, right):
    left, right = _id(left), _id(right)
    return left is not None and right is not None and left == right


def _same_active_tenant(actor, resource):
    return (actor.get('active') is True
            and _same(actor.get('tenant_id'), resource.get('tenant_id'))
            and _id(actor.get('member_id')) is not None)


def allows_owned_resource(actor, resource, scope):
    """Evaluate one independent action's scope against its responsible member.

    The scope is supplied for the particular action by a trusted policy layer.
    A department comparison requires both member and responsible member to have
    a known department. The resource's department must be resolved from the
    responsible member's current membership within the same tenant. This helper
    intentionally does not define creation, assignment, or migration semantics.
    """
    if scope not in SCOPES or not _same_active_tenant(actor, resource):
        return False
    if scope == 'all':
        return True
    if scope == 'self':
        return _same(actor.get('member_id'), resource.get('responsible_member_id'))
    if scope == 'department':
        return (_id(resource.get('responsible_member_id')) is not None
                and _same(actor.get('department_id'), resource.get('responsible_department_id')))
    return False


def allows_assigned_task_exception(actor, task, operation):
    """Allow only reading or completion of an explicitly assigned task.

    This does not authorize a general task update. A completion endpoint must
    separately restrict its payload and validate the state transition.
    """
    return (operation in ('read', 'complete')
            and _same_active_tenant(actor, task)
            and _same(actor.get('member_id'), task.get('assignee_member_id')))

# A rollout gate keeps additive schema changes from changing existing authority.
# It is read for every evaluation, including long-lived worker processes.
def scoped_access_enabled():
    import os
    return os.environ.get('TENANT_SCOPED_ACCESS_ENABLED', '').strip().lower() in {'1', 'true', 'yes'}


CRUD_ACTIONS = ('view', 'create', 'update', 'delete')
# Only sections with resource-aware API implementations belong in this catalog.
# Other sections retain their existing domain-specific checks.
SCOPE_ACTION_CATALOG = {key: CRUD_ACTIONS for key in
                        ('tasks', 'deals', 'customers', 'companies')}
SCOPE_ACTION_CATALOG['dialogs'] = ('view', 'update')
SCOPE_ACTION_CATALOG['finance'] = ('view',)
# Read-only report rows; source and ticket responsibility are checked separately.
SCOPE_ACTION_CATALOG['night_report'] = ('view',)
# Agent D archives only unused/unbound rows; transfer/permanent deletion remain owner-only.
SCOPE_ACTION_CATALOG['ai_agents'] = ('view', 'create', 'update', 'delete')
# Shared connection settings are owner-responsible; status is a separate public read.
SCOPE_ACTION_CATALOG['integrations'] = CRUD_ACTIONS
SCOPE_ACTION_CATALOG['employees'] = ('view', 'create', 'update')
SCOPE_ACTION_CATALOG['support'] = ('view', 'create', 'update')
SCOPE_ACTION_CATALOG['news'] = ('view',)
SCOPE_ACTION_CATALOG['instructions'] = ('view',)
ACTIVE_TENANT_STATUSES = frozenset({'active', 'onboarding', 'ready_for_integration'})
MODULE_ALIASES = {'contacts': 'customers'}


def canonical_module(module):
    return MODULE_ALIASES.get(module, module)


def applicable_scope_levels(module, action):
    if module in {'news', 'instructions'}:
        return ('none', 'all')
    return ('none', 'department', 'all') if (module, action) == ('employees', 'create') else ('none', 'self', 'department', 'all')


def validate_access_scopes(value):
    if not isinstance(value, dict) or set(value) - set(SCOPE_ACTION_CATALOG):
        raise ValueError('member_access_scopes_invalid')
    for module, actions in value.items():
        if (not isinstance(actions, dict) or not actions
                or set(actions) - set(SCOPE_ACTION_CATALOG[module])
                or any(not isinstance(scope, str) or scope not in applicable_scope_levels(module, action) for action, scope in actions.items())):
            raise ValueError('member_access_scopes_invalid')


def configured_action_scope(policy, module, action):
    """Return None only for an absent override; malformed configured data denies.

    A legacy grant is deliberately not converted into `all`: legacy providers
    may have more restrictive pipeline/marker or role checks.
    """
    from tenant_employee_defaults import default_deny
    denied_default = default_deny(policy)
    if not scoped_access_enabled() and not denied_default:
        return None
    absent = 'none' if denied_default else None
    module = canonical_module(module)
    if module not in SCOPE_ACTION_CATALOG or action not in SCOPE_ACTION_CATALOG[module]:
        return None
    settings = policy.member_settings
    if 'access_scopes' not in settings:
        return absent
    scopes = settings['access_scopes']
    if not isinstance(scopes, dict):
        return 'none'
    if module not in scopes:
        return absent
    actions = scopes[module]
    if not isinstance(actions, dict):
        return 'none'
    if action not in actions:
        return absent
    scope = actions[action]
    return scope if isinstance(scope, str) and scope in applicable_scope_levels(module, action) else 'none'


def profile_actor(profile):
    return {'tenant_id': profile.get('tenant_id'),
            'member_id': profile.get('telegram_id'),
            'department_id': profile.get('department_id'),
            'active': (profile.get('active') is not False
                       and profile.get('tenant_status', 'active') in ACTIVE_TENANT_STATUSES)}


def resource_metadata(resource):
    return {**resource,
            'responsible_member_id': resource.get('responsible_member_id', resource.get('access_responsible_id'))}


def preserve_workflow_access_scopes(previous, incoming):
    """Old workflow forms cannot reset or bypass versioned employee rights.

    Echoing the exact setting is accepted; omitting it retains it. Only the
    employee-card writer can actually change a matrix, including its preflight.
    """
    previous = previous if isinstance(previous, dict) else {}
    if not isinstance(incoming, dict):
        raise ValueError('member_access_requires_employee_editor')
    result = {key: dict(value) if isinstance(value, dict) else value for key, value in incoming.items()}
    absent = object()
    for key in set(previous) | set(result):
        old = previous.get(key, {})
        new = result.get(key, {})
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ValueError('member_access_requires_employee_editor')
        old_scope = old.get('access_scopes', absent)
        new_scope = new.get('access_scopes', absent)
        if new_scope is not absent and new_scope != old_scope:
            raise ValueError('member_access_requires_employee_editor')
        if old_scope is not absent:
            result.setdefault(key, {})['access_scopes'] = old_scope
    return result
