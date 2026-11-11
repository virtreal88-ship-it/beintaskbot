"""Derive navigation and native CRM actions from effective employee rights.

This module is pure. Callers must authorize the editor and lock/revalidate the
current tenant membership before persisting its result. Native granular grants retain the
existing native-only contract, independent of Kommo module availability.
"""
import hashlib
import json


CRUD_ACTIONS = ('view', 'create', 'update', 'delete')
ACTION_MODULES = ('tasks', 'deals', 'customers')
PAGE_MODULES = {
    'tasks': 'tasks', 'customers': 'customers', 'companies': 'customers',
    'deals': 'deals', 'employees': 'employees', 'dialogs': 'dialogs',
    'support': 'support', 'night_report': 'night_report',
    'ai_agents': 'ai_agents', 'ai_dashboard': 'ai_agents',
    'ai_administrator': 'ai_agents', 'ai_teams': 'ai_agents',
    'knowledge_center': 'ai_agents', 'support_cases': 'ai_agents',
    'ai_testing': 'ai_agents', 'hot_orders': 'hot_orders',
    'approvals': 'tasks', 'finance': 'finance', 'integrations': 'integrations',
    'settings': 'settings', 'linear': 'linear', 'news': None,
    'instructions': None, 'profile': None,
}


def _policy(profile):
    # Local import avoids a cycle when public_capabilities calls this helper.
    from tenant_policy import TenantPolicy
    return TenantPolicy(profile)


def page_capabilities(profile):
    policy = _policy(profile)
    active = profile.get('active') is not False
    role = profile.get('role')
    modules = {key: policy.allows(key) for key in set(PAGE_MODULES.values()) if key}
    pages = {page: bool(modules.get(module)) for page, module in PAGE_MODULES.items()}
    # These APIs already accept native granular grants. Do not change coarse
    # modules: those also enable Kommo actions and assignee admission.
    for module in ACTION_MODULES:
        pages[module] = bool(modules[module] or policy.allows_action(module, 'view'))
    # Kommo's contact directory accepts either CRM grant, but only with an
    # actual pipeline scope. Its shared deal feed likewise accepts either
    # grant; native CRUD authority remains entirely source-specific.
    external_crm = bool(modules['deals'] or modules['customers'])
    pages['customers'] = bool(policy.allows_action('customers', 'view')
                              or (external_crm and policy.pipeline_scope()))
    pages['deals'] = bool(policy.allows_action('deals', 'view') or external_crm)
    pages['companies'] = policy.allows_action('customers', 'view')
    from tenant_employee_policy import employee_action_allowed
    pages['employees'] = employee_action_allowed(profile, 'view') or employee_action_allowed(profile, 'create')
    pages['approvals'] = role in {'owner', 'admin'} and any(
        modules.get(key) for key in ('tasks', 'hot_orders', 'deals'))
    pages['ai_administrator'] = role == 'owner' and modules['ai_agents']
    for page in ('ai_dashboard', 'support_cases'):
        pages[page] = role in {'owner', 'admin'} and modules['ai_agents']
    # Published catalog reads admit any active tenant member, including one
    # without AI management rights. The API rechecks membership and publication;
    # public/shared guide access and editor authority are separate and unchanged.
    from tenant_policy import positive_id
    pages['instructions'] = bool(profile.get('tenant_id') and positive_id(profile.get('telegram_id'))
                                 and profile.get('tenant_status', 'active') == 'active')
    # Only published news is shared. Review and publication still enforce
    # their existing Linear reviewer binding through tenant_news.access().
    pages['news'] = pages['instructions']
    for page, module in {'tasks': 'tasks', 'customers': 'customers', 'companies': 'companies',
                         'deals': 'deals', 'dialogs': 'dialogs', 'support': 'support'}.items():
        if policy.has_scoped_access(module):
            pages[page] = any(policy.allows_action(module, action) for action in
                (('view','create','update') if module=='support' else CRUD_ACTIONS))
            if page == 'tasks':
                # The assigned-task exception needs an entry point even when
                # every generic task scope is none. The API limits that feed
                # to assignments and allows completion only, not generic edits.
                pages[page] = True
    from tenant_ai_scoped_access import capabilities as ai_document_capabilities
    ai_documents = ai_document_capabilities(policy)
    if ai_documents is not None:
        pages['ai_agents'] = any(ai_documents[action] for action in ('view', 'create', 'update'))
    report_scope = policy.action_scope('night_report', 'view')
    if report_scope is not None:
        pages['night_report'] = report_scope != 'none' and role != 'master'
    elif any(policy.action_scope(module, 'view') not in {None, 'all'} for module in ('dialogs', 'support')):
        pages['night_report'] = False
    finance_scope = policy.action_scope('finance', 'view')
    if finance_scope is not None:
        pages['finance'] = finance_scope != 'none'
    return {page: True if page == 'profile' else bool(active and (
        (role == 'owner' and (page not in {'instructions', 'news'} or allowed))
        or allowed))
        for page, allowed in pages.items()}


def action_capabilities(profile):
    policy = _policy(profile)
    return {module: {action: policy.allows_action(module, action)
                     for action in CRUD_ACTIONS} for module in ACTION_MODULES}


def validate_native_crm_actions(value):
    if not isinstance(value, dict) or set(value) - set(ACTION_MODULES):
        raise ValueError('member_access_actions_invalid')
    for values in value.values():
        if (not isinstance(values, dict) or not values or set(values) - set(CRUD_ACTIONS)
                or any(type(item) is not bool for item in values.values())):
            raise ValueError('member_access_actions_invalid')


def _native_actions(policy):
    value = policy.member_settings.get('native_crm_actions', {})
    if not isinstance(value, dict):
        return {}
    return {module: {action: enabled for action, enabled in values.items()
                     if action in CRUD_ACTIONS and type(enabled) is bool}
            for module, values in value.items()
            if module in ACTION_MODULES and isinstance(values, dict)}


def access_snapshot(profile):
    policy = _policy(profile)
    permissions = profile.get('permissions')
    permissions = list(permissions) if isinstance(permissions, list) else []
    material = {
        'tenant_id': str(profile.get('tenant_id') or ''),
        'telegram_id': str(profile.get('telegram_id') or ''),
        'role': profile.get('role'), 'active': profile.get('active'),
        'tenant_status': profile.get('tenant_status', 'active'),
        'permissions': sorted(str(item) for item in permissions),
        'modules': profile.get('modules') or {},
        'policy_modules': policy.policies.get('modules'),
        'pipeline_scope': policy.pipeline_scope(),
        'ai_agents_manage': policy.member_settings.get('ai_agents_manage'),
        'native_crm_actions': _native_actions(policy),
        'access_scopes': policy.member_settings.get('access_scopes', {}),
        'department_id': str(profile.get('department_id') or ''),
    }
    version = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode()).hexdigest()
    from tenant_scoped_access import SCOPE_ACTION_CATALOG, scoped_access_enabled, applicable_scope_levels
    enabled = scoped_access_enabled()
    return {'version': version,
            'scoped_access_enabled': enabled,
            'scope_catalog': {k: list(v) for k, v in SCOPE_ACTION_CATALOG.items()} if enabled else {},
            'scope_levels': ['none', 'self', 'department', 'all'],
            'action_scope_levels': {k: {a: list(applicable_scope_levels(k, a)) for a in v} for k, v in SCOPE_ACTION_CATALOG.items()} if enabled else {},
            'department_id': str(profile.get('department_id') or '') or None,
            'access_scopes': policy.member_settings.get('access_scopes', {}),
            'scoped_actions': {k: {a: policy.action_scope(k, a) for a in v}
                               for k, v in SCOPE_ACTION_CATALOG.items()} if enabled else {},
            'legacy_actions': {k: {a: policy.legacy_allows(k) if k in {'support','night_report'} else policy.legacy_allows_action('customers' if k == 'companies' else k, a)
                                  for a in v} for k, v in SCOPE_ACTION_CATALOG.items()} if enabled else {},
            'action_catalog': {module: list(CRUD_ACTIONS) for module in ACTION_MODULES},
            'action_scope': 'native_crm',
            'native_crm_actions': _native_actions(policy),
            'pages': page_capabilities(profile), 'actions': action_capabilities(profile)}


def validate_access_patch(patch):
    if not isinstance(patch, dict) or set(patch) not in ({'actions'}, {'scopes'}):
        raise ValueError('member_access_invalid')
    if 'scopes' in patch:
        from tenant_scoped_access import validate_access_scopes, scoped_access_enabled
        if not scoped_access_enabled():
            raise ValueError('member_access_scopes_not_enabled')
        validate_access_scopes(patch['scopes'])
        if not patch['scopes']:
            raise ValueError('member_access_invalid')
    else:
        validate_native_crm_actions(patch['actions'])
        if not patch['actions']:
            raise ValueError('member_access_invalid')


def apply_access_patch(profile, patch):
    """Return independent membership grants and native operation overrides.

    Version and editor authorization are the persistence caller's responsibility.
    Membership permissions, assign/manage, and external-provider access are never
    rewritten. Explicit native CRUD overrides are independent of these grants.
    """
    validate_access_patch(patch)
    if profile.get('role') == 'owner':
        raise ValueError('member_access_owner_protected')
    policy = _policy(profile)
    native_actions = _native_actions(policy)
    permissions = list(profile.get('permissions') or [])
    for module, changes in patch.get('actions', {}).items():
        if any(policy.has_scoped_access(module, action) for action in changes):
            raise ValueError('member_access_scope_conflict')
        native_actions.setdefault(module, {}).update(changes)
    raw = policy.member_settings.get('access_scopes', {})
    if not isinstance(raw, dict):
        raise ValueError('member_access_scopes_invalid')
    # Retain unedited cells and all unrelated settings. Never synthesize scoped
    # grants from legacy booleans, role defaults or module visibility.
    access_scopes = {key: dict(value) if isinstance(value, dict) else value for key, value in raw.items()}
    for module, changes in patch.get('scopes', {}).items():
        if module in access_scopes and not isinstance(access_scopes[module], dict):
            raise ValueError('member_access_scopes_invalid')
        access_scopes.setdefault(module, {}).update(changes)
    return {'permissions': permissions, 'native_crm_actions': native_actions,
            'access_scopes': access_scopes}
