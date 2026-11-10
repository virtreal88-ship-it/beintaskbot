"""Separate navigation preferences from native CRM operation permissions.

This module is pure. Callers must authorize the editor and lock/revalidate the
current tenant membership before persisting its result. Hiding a page is never
an API authorization decision. Native granular grants intentionally retain the
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
    'settings': 'settings', 'linear': 'linear', 'news': 'linear',
    'instructions': None, 'profile': None,
}


def _policy(profile):
    # Local import avoids a cycle when public_capabilities calls this helper.
    from tenant_policy import TenantPolicy
    return TenantPolicy(profile)


def validate_page_visibility(value, *, sparse=False):
    """Validate stored visibility, or a patch whose null means inherit."""
    if not isinstance(value, dict):
        raise ValueError('page_visibility_invalid')
    for page, enabled in value.items():
        if page not in PAGE_MODULES or page == 'profile':
            raise ValueError('page_visibility_unknown_page')
        if type(enabled) is not bool and not (sparse and enabled is None):
            raise ValueError('page_visibility_invalid')
    return dict(value)


def _visibility(policy):
    value = policy.member_settings.get('page_visibility', {})
    # Malformed historic data must not grant access. Only explicit false can
    # restrict a page; explicit true still cannot bypass its prerequisites.
    return {key: item for key, item in value.items()
            if key in PAGE_MODULES and key != 'profile' and type(item) is bool
            } if isinstance(value, dict) else {}


def page_visibility(profile):
    """Expose only this member's navigation preferences, never workflow data."""
    return {} if profile.get('role') == 'owner' else _visibility(_policy(profile))


def page_capabilities(profile, *, ignore_visibility=False):
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
    pages['employees'] = policy.allows_action('employees', 'manage')
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
    # News additionally requires the integration's can_review_news binding.
    # That existing source-specific check remains in newsAvailable/API policy.
    visibility = {} if ignore_visibility else _visibility(policy)
    return {page: True if page == 'profile' else bool(active and (
        (role == 'owner' and (page != 'instructions' or allowed))
        or (allowed and visibility.get(page, True))))
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
    visibility = _visibility(policy)
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
        'page_visibility': visibility,
        'native_crm_actions': _native_actions(policy),
    }
    version = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode()).hexdigest()
    return {'version': version, 'page_catalog': [
                {'id': page, 'module': module, 'editable': page != 'profile'}
                for page, module in PAGE_MODULES.items()],
            'action_catalog': {module: list(CRUD_ACTIONS) for module in ACTION_MODULES},
            'action_scope': 'native_crm', 'page_visibility': visibility,
            'native_crm_actions': _native_actions(policy),
            'page_prerequisites': page_capabilities(profile, ignore_visibility=True),
            'pages': page_capabilities(profile), 'actions': action_capabilities(profile)}


def validate_access_patch(patch):
    if (not isinstance(patch, dict) or not patch or set(patch) - {'pages', 'actions'}):
        raise ValueError('member_access_invalid')
    if 'pages' in patch:
        validate_page_visibility(patch['pages'], sparse=True)
    if 'actions' in patch:
        validate_native_crm_actions(patch['actions'])
    if not patch.get('pages') and not patch.get('actions'):
        raise ValueError('member_access_invalid')


def apply_access_patch(profile, patch):
    """Return independent membership grants and workflow visibility values.

    Version and editor authorization are the persistence caller's responsibility.
    Membership permissions, assign/manage, and external-provider access are never
    rewritten. Explicit native CRUD overrides are independent of these grants.
    """
    validate_access_patch(patch)
    if profile.get('role') == 'owner':
        raise ValueError('member_access_owner_protected')
    policy = _policy(profile)
    visibility = _visibility(policy)
    native_actions = _native_actions(policy)
    permissions = list(profile.get('permissions') or [])
    for page, enabled in patch.get('pages', {}).items():
        if enabled is None:
            visibility.pop(page, None)
        else:
            visibility[page] = enabled
    for module, changes in patch.get('actions', {}).items():
        native_actions.setdefault(module, {}).update(changes)
    return {'permissions': permissions, 'page_visibility': visibility,
            'native_crm_actions': native_actions}
