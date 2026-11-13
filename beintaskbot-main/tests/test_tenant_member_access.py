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
    updated['workflow']['policies']['members']['20']['native_crm_actions'] = result['native_crm_actions']
    return updated


def test_granular_native_view_is_navigable_without_enabling_external_module():
    person = profile(permissions=['tasks:view', 'tasks:update'], modules={})
    capabilities = TenantPolicy(person).public_capabilities()
    assert capabilities['pages']['tasks'] is True
    assert capabilities['modules']['tasks'] is False
    assert capabilities['actions']['tasks'] == {'view': True, 'create': False, 'update': True, 'delete': False}
    assert page_capabilities(profile(permissions=['tasks:create']))['tasks'] is False


def test_retired_page_preferences_do_not_hide_allowed_pages_or_change_grants():
    person = profile(permissions=['tasks:view', 'tasks:update', 'tasks:assign', 'custom:retained'])
    before = deepcopy(person)
    person['workflow']['policies']['members']['20']['page_visibility'] = {'tasks': False, 'finance': True}
    assert page_capabilities(person)['tasks'] is True
    assert page_capabilities(person)['finance'] is False
    assert action_capabilities(person) == action_capabilities(before)
    assert access_snapshot(person)['version'] == access_snapshot(before)['version']
    assert 'page_visibility' not in access_snapshot(person)
    assert 'page_visibility' not in TenantPolicy(person).public_capabilities()

def test_retired_preferences_never_exposed_or_used_as_permission():
    person = profile(permissions=[])
    person['workflow']['policies']['members']['20'].update({
        'page_visibility': {'tasks': True, 'profile': False, 'employees': True},
        'private_other_setting': 'not-public'})
    caps = TenantPolicy(person).public_capabilities()
    assert 'page_visibility' not in caps and 'workflow' not in caps
    assert not caps['pages']['tasks'] and not caps['pages']['employees']
    assert caps['pages']['profile']

def test_instructions_catalog_visibility_requires_member_not_ai_management():
    person = profile(permissions=[], modules={})
    person['workflow']['policies']['members']['20']['page_visibility'] = {'instructions': False}
    assert page_capabilities(person)['instructions']
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
    person['workflow']['policies']['members']['20']['page_visibility'] = {page: True}
    assert not page_capabilities(person)[page]


def test_owner_and_profile_protection_and_inactive_membership():
    owner = profile(role='owner', permissions=[], modules={})
    owner['workflow']['policies']['members']['20']['page_visibility'] = {'tasks': False}
    owner['workflow']['policies']['members']['20']['native_crm_actions'] = {'tasks': {'view': False}}
    assert all(page_capabilities(owner).values())
    assert all(all(values.values()) for values in action_capabilities(owner).values())
    with pytest.raises(ValueError, match='owner_protected'):
        apply_access_patch(owner, {'actions': {'tasks': {'view': False}}})
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
def test_native_view_disabled_preserves_external_authority_without_menu_overrides(module):
    person = profile(role='manager', permissions=[module], modules={module: True})
    person['workflow']['pipelines'] = [{'pipeline_id': 10, 'owner_telegram_id': 20, 'active': True}]
    changed = with_result(person, apply_access_patch(person, {'actions': {module: {'view': False}}}))
    assert TenantPolicy(changed).allows(module)
    assert not TenantPolicy(changed).allows_action(module, 'view')
    assert page_capabilities(changed)[module] is (module != 'deals')
    if module == 'customers':
        assert not page_capabilities(changed)['companies']
    changed['workflow']['policies']['members']['20']['page_visibility'] = {module: False}
    assert page_capabilities(changed)[module] is (module != 'deals')
    assert TenantPolicy(changed).allows(module)


def test_deals_only_admin_with_scope_sees_kommo_contacts_without_native_rights():
    person = profile(role='admin', permissions=['deals'], modules={'deals': True, 'customers': True})
    person['workflow']['pipelines'] = [{'pipeline_id': 10, 'active': True}]
    policy = TenantPolicy(person)
    assert policy.pipeline_scope()
    assert page_capabilities(person)['customers']
    assert not page_capabilities(person)['companies']
    assert not any(action_capabilities(person)['customers'].values())
    assert not policy.allows('customers')
    person['workflow']['policies']['members']['20']['page_visibility'] = {'customers': False}
    assert page_capabilities(person)['customers']
    assert TenantPolicy(person).allows('deals')
    assert person['permissions'] == ['deals']


def test_deals_only_contacts_prerequisite_requires_pipeline_scope():
    person = profile(role='admin', permissions=['deals'], modules={'deals': True})
    assert not page_capabilities(person)['customers']
    version = access_snapshot(person)['version']
    person['workflow']['pipelines'] = [{'pipeline_id': 10, 'active': True}]
    assert page_capabilities(person)['customers']
    assert access_snapshot(person)['version'] != version
    person['workflow']['pipelines'][0]['active'] = False
    assert not page_capabilities(person)['customers']


def test_customers_only_legacy_grant_can_open_shared_kommo_deals_feed():
    person = profile(role='manager', permissions=['customers'], modules={'customers': True})
    assert not page_capabilities(person)['deals']
    person['workflow']['pipelines'] = [{'pipeline_id': 10, 'owner_telegram_id': 20, 'active': True}]
    person['workflow']['policies']['members']['20']['pipeline_ids'] = ['10']
    assert page_capabilities(person)['deals']
    assert not any(action_capabilities(person)['deals'].values())
    assert not TenantPolicy(person).allows('deals')


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
    assert access_snapshot(changed)['version'] == version
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
    assert set(snapshot['pages']) == set(PAGE_MODULES)
    assert 'page_catalog' not in snapshot and 'page_prerequisites' not in snapshot
    assert {'linear', 'news', 'night_report', 'companies', 'ai_administrator'} <= set(PAGE_MODULES)
    assert snapshot['pages']['profile'] is True


@pytest.mark.parametrize('patch', [None, {}, [], {'permissions': []},
    {'pages': {}}, {'pages': {'tasks': True}}, {'pages': {'tasks': False}},
    {'pages': {'tasks': None}}, {'pages': []}, {'pages': {'profile': False}},
    {'pages': {'invented': True}}, {'pages': {'tasks': 1}},
    {'actions': {}}, {'actions': []}, {'actions': {'finance': {'delete': True}}},
    {'actions': {'tasks': {}}}, {'actions': {'tasks': {'manage': True}}},
    {'actions': {'tasks': {'view': None}}}, {'actions': {'tasks': {'view': 1}}},
])
def test_invalid_patches_fail_closed(patch):
    with pytest.raises(ValueError):
        validate_access_patch(patch)


def test_workflow_validation_checks_actions_and_preserves_ignored_legacy_values():
    validate_workflow_patch(None, None, {'members': {'20': {'page_visibility': {'tasks': None}}}})
    validate_workflow_patch(None, None, {'members': {'20': {'native_crm_actions': {'tasks': {'delete': False}}}}})
    with pytest.raises(ValueError):
        validate_workflow_patch(None, None, {'members': {'20': {'native_crm_actions': {'finance': {'delete': False}}}}})

def test_member_page_visibility_never_grants_other_company_deal_access():
    person = profile(role='manager', permissions=['deals'], modules={'deals': True})
    person['workflow']['policies']['members']['20']['page_visibility'] = {'deals': True}
    policy = TenantPolicy(person)
    assert not policy.can_access_deal({'tenant_id': 'company-b', 'pipeline_id': 10, 'status_id': 100})


def test_kommo_pipeline_grant_survives_native_deal_denial(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    person = profile(permissions=[], modules={})
    person['workflow']['pipelines'] = [{'pipeline_id': '10', 'active': True},
                                       {'pipeline_id': '11', 'active': True}]
    settings = person['workflow']['policies']['members']['20']
    settings.update(pipeline_ids=['10'], access_scopes={'deals': {
        action: 'none' for action in ('view', 'create', 'update', 'delete')}})
    caps = TenantPolicy(person).public_capabilities()
    assert caps['kommo_deals'] is True
    assert caps['pages']['deals'] is True
    assert caps['modules']['deals'] is False
    assert not any(caps['actions']['deals'].values())
    assert caps['kommo_pipeline_scope'] == [{'pipeline_id': 10, 'status_ids': []}]
    assert not TenantPolicy(person).can_access_kommo_deal(
        {'tenant_id': 'company-a', 'pipeline_id': 11})
    settings['pipeline_ids'] = []
    caps = TenantPolicy(person).public_capabilities()
    assert caps['kommo_deals'] is False
    assert caps['pages']['deals'] is False


def test_unassigned_admin_default_deny_does_not_bootstrap_kommo(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    person = profile(role='admin', permissions=[], modules={})
    person['workflow']['policies']['employee_access_defaults'] = {'deny_unset': True}
    caps = TenantPolicy(person).public_capabilities()
    assert caps['pages']['deals'] is False
    assert caps['kommo_deals'] is False
