"""Navigation is not authorization; native CRUD is not a blanket module grant."""
from copy import deepcopy

import pytest

from tenant_member_access import (
    ACTION_MODULES, PAGE_MODULES, access_snapshot, action_capabilities,
    apply_access_patch, page_capabilities, validate_access_patch,
)
from tenant_policy import TenantPolicy, validate_workflow_patch


def profile(**overrides):
    result = {'tenant_id': 'company-a', 'telegram_id': 20, 'role': 'operator',
              'active': True, 'permissions': ['tasks'], 'modules': {'tasks': True},
              'workflow': {'policies': {'members': {'20': {}}}}}
    result.update(overrides)
    return result


def with_result(person, result):
    updated = deepcopy(person)
    updated['permissions'] = result['permissions']
    updated['workflow']['policies']['members']['20']['page_visibility'] = result['page_visibility']
    updated['workflow']['policies']['members']['20']['native_crm_actions'] = result['native_crm_actions']
    return updated


def test_granular_native_view_is_navigable_without_enabling_external_module():
    person = profile(permissions=['tasks:view', 'tasks:update'], modules={})
    capabilities = TenantPolicy(person).public_capabilities()
    assert capabilities['pages']['tasks'] is True
    assert capabilities['modules']['tasks'] is False
    assert capabilities['actions']['tasks'] == {'view': True, 'create': False, 'update': True, 'delete': False}
    assert page_capabilities(profile(permissions=['tasks:create']))['tasks'] is False


def test_hidden_page_does_not_change_permissions_or_action_authority():
    person = profile(permissions=['tasks:view', 'tasks:update', 'tasks:assign', 'custom:retained'])
    before = deepcopy(person)
    result = apply_access_patch(person, {'pages': {'tasks': False}})
    changed = with_result(person, result)
    assert result['permissions'] == person['permissions']
    assert action_capabilities(changed) == action_capabilities(person)
    assert page_capabilities(changed)['tasks'] is False
    assert access_snapshot(changed)['page_prerequisites']['tasks'] is True
    assert person == before
    inherited = apply_access_patch(changed, {'pages': {'tasks': None}})
    assert inherited['page_visibility'] == {}
    assert page_capabilities(with_result(changed, inherited))['tasks'] is True


def test_instructions_catalog_visibility_requires_member_not_ai_management():
    person = profile(permissions=[], modules={})
    assert page_capabilities(person)['instructions']
    result = apply_access_patch(person, {'pages': {'instructions': False}})
    assert not page_capabilities(with_result(person, result))['instructions']
    assert result['permissions'] == []
    assert access_snapshot(with_result(person, result))['page_prerequisites']['instructions']
    for denied in (profile(active=False), profile(tenant_status='suspended'),
                   profile(tenant_id=''), profile(telegram_id=0)):
        assert not page_capabilities(denied)['instructions']


@pytest.mark.parametrize('person,page', [
    (profile(permissions=[]), 'tasks'),
    (profile(modules={'tasks': False}), 'tasks'),
    (profile(workflow={'policies': {'modules': {}, 'members': {'20': {}}}}), 'tasks'),
    (profile(permissions=['deals'], modules={'deals': True}), 'deals'),
    (profile(role='master', permissions=['tasks']), 'tasks'),
    (profile(role='manager', permissions=['ai_agents']), 'ai_agents'),
    (profile(role='admin', permissions=['ai_agents']), 'ai_administrator'),
])
def test_explicit_show_never_grants_access(person, page):
    result = apply_access_patch(person, {'pages': {page: True}})
    assert not page_capabilities(with_result(person, result))[page]


def test_owner_and_profile_protection_and_inactive_membership():
    owner = profile(role='owner', permissions=[], modules={})
    owner['workflow']['policies']['members']['20']['page_visibility'] = {'tasks': False}
    owner['workflow']['policies']['members']['20']['native_crm_actions'] = {'tasks': {'view': False}}
    assert all(page_capabilities(owner).values())
    assert all(all(values.values()) for values in action_capabilities(owner).values())
    with pytest.raises(ValueError, match='owner_protected'):
        apply_access_patch(owner, {'pages': {'tasks': False}})
    inactive = page_capabilities(profile(active=False, permissions=['tasks:manage']))
    assert inactive['profile'] is True
    assert not any(value for page, value in inactive.items() if page != 'profile')
    assert not any(action_capabilities(profile(active=False))['tasks'].values())
    assert not action_capabilities({**owner, 'active': False})['tasks']['view']


def test_granular_native_contract_survives_role_and_company_external_restrictions():
    # Existing native APIs use granular grants without provider role/module gates.
    person = profile(permissions=['customers:view'], modules={})
    assert not TenantPolicy(person).allows('customers')
    assert page_capabilities(person)['customers']
    # Companies is native-only; its API accepts the same explicit view grant.
    assert page_capabilities(person)['companies']
    person['role'] = 'manager'
    assert page_capabilities(person)['companies']


def test_sparse_crud_change_preserves_legacy_rights_and_external_scope():
    person = profile(permissions=['tasks', 'linear', 'custom:retained'])
    result = apply_access_patch(person, {'actions': {'tasks': {'delete': False}}})
    assert result['permissions'] == person['permissions']
    assert result['native_crm_actions'] == {'tasks': {'delete': False}}
    changed = with_result(person, result)
    assert action_capabilities(changed)['tasks'] == {
        'view': True, 'create': True, 'update': True, 'delete': False}
    assert TenantPolicy(changed).allows('tasks')
    assert page_capabilities(changed)['tasks']
    # The existing assignee-admission prerequisites are not lost by delete off.
    assert TenantPolicy(changed).allows_action('tasks', 'view')
    assert TenantPolicy(changed).allows_action('tasks', 'update')
    assert TenantPolicy(changed).allows_action('tasks', 'assign')


def test_manage_is_preserved_but_cannot_override_native_false():
    person = profile(permissions=['tasks:manage', 'deals:manage', 'custom:retained'])
    unchanged = apply_access_patch(person, {'pages': {'tasks': False}})
    assert unchanged['permissions'] == person['permissions']
    noop = apply_access_patch(person, {'actions': {'tasks': {'create': True}}})
    assert noop['permissions'] == person['permissions']
    result = apply_access_patch(person, {'actions': {'tasks': {'create': False}}})
    assert result['permissions'] == person['permissions']
    assert TenantPolicy(with_result(person, result)).allows_action('tasks', 'assign')
    assert TenantPolicy(with_result(person, result)).allows_action('tasks', 'manage')
    assert 'deals:manage' in result['permissions']
    assert not action_capabilities(with_result(person, result))['tasks']['create']


def test_sparse_disabled_legacy_module_only_grants_explicit_native_action():
    person = profile(modules={'tasks': False})
    result = apply_access_patch(person, {'actions': {'tasks': {'view': True}}})
    assert result['permissions'] == ['tasks']
    assert not TenantPolicy(with_result(person, result)).allows('tasks')
    assert action_capabilities(with_result(person, result))['tasks'] == {
        'view': True, 'create': False, 'update': False, 'delete': False}


def test_explicit_native_view_without_any_grants_is_navigable_but_not_external():
    person = profile(permissions=[])
    changed = with_result(person, apply_access_patch(person, {'actions': {'tasks': {'view': True}}}))
    assert page_capabilities(changed)['tasks']
    assert not TenantPolicy(changed).allows('tasks')
    assert action_capabilities(changed)['tasks'] == {
        'view': True, 'create': False, 'update': False, 'delete': False}
    assert changed['permissions'] == []
    assert not TenantPolicy({**changed, 'active': False}).allows_action('tasks', 'view')


@pytest.mark.parametrize('module', ['tasks', 'deals', 'customers'])
def test_native_view_disabled_preserves_external_page_until_explicitly_hidden(module):
    person = profile(role='manager', permissions=[module], modules={module: True})
    changed = with_result(person, apply_access_patch(person, {'actions': {module: {'view': False}}}))
    assert TenantPolicy(changed).allows(module)
    assert not TenantPolicy(changed).allows_action(module, 'view')
    assert page_capabilities(changed)[module]
    if module == 'customers':
        assert not page_capabilities(changed)['companies']
    hidden = with_result(changed, apply_access_patch(changed, {'pages': {module: False}}))
    assert not page_capabilities(hidden)[module]
    assert TenantPolicy(hidden).allows(module)


def test_native_overrides_are_member_specific_and_sparse():
    person = profile()
    person['workflow']['policies']['members']['20']['native_crm_actions'] = {
        'tasks': {'delete': False, 'update': False}, 'deals': {'view': True}}
    person['workflow']['policies']['members']['21'] = {'native_crm_actions': {'tasks': {'view': False}}}
    result = apply_access_patch(person, {'actions': {'tasks': {'update': True}}})
    assert result['native_crm_actions'] == {
        'tasks': {'delete': False, 'update': True}, 'deals': {'view': True}}
    assert TenantPolicy(with_result(person, result)).allows_action('tasks', 'view')
    assert not TenantPolicy({**person, 'telegram_id': 21}).allows_action('tasks', 'view')


def test_version_is_access_specific_and_tenant_and_actor_scoped():
    person = profile()
    version = access_snapshot(person)['version']
    unrelated = deepcopy(person)
    unrelated.update(updated_at='new', email='other@example.test', display_name='Changed')
    unrelated['workflow']['policies']['members']['20']['notifications'] = {'task_assigned': {'push': True}}
    assert access_snapshot(unrelated)['version'] == version
    for key, value in [('tenant_id', 'company-b'), ('telegram_id', 21),
                       ('permissions', []), ('role', 'manager'), ('active', False), ('modules', {})]:
        changed = deepcopy(person)
        changed[key] = value
        assert access_snapshot(changed)['version'] != version
    changed = deepcopy(person)
    changed['workflow']['policies']['members']['20']['page_visibility'] = {'tasks': False}
    assert access_snapshot(changed)['version'] != version
    changed = deepcopy(person)
    changed['workflow']['policies']['members']['20']['native_crm_actions'] = {'tasks': {'delete': False}}
    assert access_snapshot(changed)['version'] != version
    # Another member's page settings do not change this member's snapshot.
    other = deepcopy(person)
    other['workflow']['policies']['members']['21'] = {'page_visibility': {'tasks': False}}
    assert access_snapshot(other)['version'] == version


def test_catalog_only_offers_supported_native_crud_and_real_routes():
    snapshot = access_snapshot(profile())
    assert set(snapshot['action_catalog']) == set(ACTION_MODULES)
    assert snapshot['action_scope'] == 'native_crm'
    assert {row['id'] for row in snapshot['page_catalog']} == set(PAGE_MODULES)
    assert {'linear', 'news', 'night_report', 'companies', 'ai_administrator'} <= set(PAGE_MODULES)
    assert next(row for row in snapshot['page_catalog'] if row['id'] == 'profile')['editable'] is False


@pytest.mark.parametrize('patch', [None, {}, [], {'permissions': []},
    {'pages': {}}, {'pages': []}, {'pages': {'profile': False}},
    {'pages': {'invented': True}}, {'pages': {'tasks': 1}},
    {'actions': []}, {'actions': {'finance': {'delete': True}}},
    {'actions': {'tasks': {}}}, {'actions': {'tasks': {'manage': True}}},
    {'actions': {'tasks': {'view': None}}}, {'actions': {'tasks': {'view': 1}}},
])
def test_invalid_patches_fail_closed(patch):
    with pytest.raises(ValueError):
        validate_access_patch(patch)


def test_workflow_validation_checks_page_visibility_but_not_unrelated_settings():
    validate_workflow_patch(None, None, {'members': {'20': {'page_visibility': {'tasks': False}}}})
    with pytest.raises(ValueError):
        validate_workflow_patch(None, None, {'members': {'20': {'page_visibility': {'tasks': None}}}})
    validate_workflow_patch(None, None, {'members': {'20': {'native_crm_actions': {'tasks': {'delete': False}}}}})
    with pytest.raises(ValueError):
        validate_workflow_patch(None, None, {'members': {'20': {'native_crm_actions': {'finance': {'delete': False}}}}})


def test_member_page_visibility_never_grants_other_company_deal_access():
    person = profile(role='manager', permissions=['deals'], modules={'deals': True})
    result = apply_access_patch(person, {'pages': {'deals': True}})
    policy = TenantPolicy(with_result(person, result))
    assert not policy.can_access_deal({'tenant_id': 'company-b', 'pipeline_id': 10, 'status_id': 100})
