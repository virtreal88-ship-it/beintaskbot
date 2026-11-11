from copy import deepcopy
import uuid

import pytest

from tenant_member_access import access_snapshot, apply_access_patch
from tenant_policy import TenantPolicy
from tenant_scoped_access import SCOPE_ACTION_CATALOG, validate_access_scopes
from tenant_scoped_access_store import resource_scope_sql

A, B = str(uuid.UUID(int=6001)), str(uuid.UUID(int=6002))
SALES, SUPPORT = str(uuid.UUID(int=6101)), str(uuid.UUID(int=6102))


def person(scopes=None, **changes):
    value = {'tenant_id': A, 'telegram_id': 11, 'active': True, 'role': 'manager',
             'department_id': SALES, 'tenant_status': 'active',
             'permissions': ['tasks', 'deals', 'customers', 'dialogs'],
             'modules': {'tasks': True, 'deals': True, 'customers': True},
             'workflow': {'policies': {'members': {'11': {'access_scopes': scopes or {}}}}}}
    value.update(changes)
    return value


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')


@pytest.mark.parametrize('module', ['tasks', 'deals', 'customers', 'companies'])
@pytest.mark.parametrize('action', ['view', 'create', 'update', 'delete'])
@pytest.mark.parametrize('scope,expected', [
    ('none', [False, False, False, False]), ('self', [True, False, False, False]),
    ('department', [True, True, False, False]), ('all', [True, True, True, False])])
def test_matrix_is_independent_and_tenant_bound(module, action, scope, expected):
    policy = TenantPolicy(person({module: {action: scope}}))
    resources = [dict(tenant_id=t, access_responsible_id=u, responsible_department_id=d)
                 for t, u, d in [(A, 11, SALES), (A, 12, SALES), (A, 13, SUPPORT), (B, 11, SALES)]]
    assert [policy.allows_resource(module, action, row) for row in resources] == expected
    assert policy.action_scope(module, action) == scope
    assert not TenantPolicy({**policy.profile, 'active': False}).allows_resource(module, action, resources[0])


def test_gate_off_preserves_legacy_authority_and_disables_editing(monkeypatch):
    p = person({'tasks': {'view': 'none'}})
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    assert TenantPolicy(p).action_scope('tasks', 'view') is None
    assert TenantPolicy(p).allows_action('tasks', 'view')
    assert TenantPolicy(p).allows('tasks')
    assert access_snapshot(p)['scope_catalog'] == {}
    with pytest.raises(ValueError, match='not_enabled'):
        apply_access_patch(p, {'scopes': {'tasks': {'view': 'self'}}})


def test_absent_scope_retains_exact_legacy_rules_without_conversion():
    p = person({'tasks': {'delete': 'none'}})
    assert TenantPolicy(p).action_scope('tasks', 'view') is None
    assert TenantPolicy(p).allows_action('tasks', 'view')
    assert not TenantPolicy(p).allows_action('tasks', 'delete')
    # Coarse legacy routes cannot enforce a scoped override.
    assert not TenantPolicy(p).allows('tasks')
    snapshot = access_snapshot(p)
    assert snapshot['scoped_actions']['tasks']['view'] is None
    assert snapshot['legacy_actions']['tasks']['view'] is True
    assert snapshot['access_scopes'] == {'tasks': {'delete': 'none'}}


def test_sparse_edit_preserves_unrelated_workflow_permissions_and_legacy_grants():
    p = person({'tasks': {'view': 'department'}, 'deals': {'view': 'self'}})
    p['permissions'] += ['custom:kept']
    p['workflow']['policies']['members']['11'].update(
        native_crm_actions={'tasks': {'delete': False}}, ai_enabled=True,
        notifications={'task_assigned': {'push': False}}, pipeline_ids=['5'])
    before = deepcopy(p)
    result = apply_access_patch(p, {'scopes': {'tasks': {'update': 'self', 'delete': 'none'}}})
    assert p == before
    assert result['permissions'] == p['permissions']
    assert result['native_crm_actions'] == {'tasks': {'delete': False}}
    assert result['access_scopes'] == {'tasks': {'view': 'department', 'update': 'self', 'delete': 'none'},
                                       'deals': {'view': 'self'}}


def test_companies_and_contacts_are_independent_only_after_explicit_scope():
    p = person({'companies': {'view': 'none'}})
    policy = TenantPolicy(p)
    assert policy.allows_action('customers', 'view')
    assert policy.action_scope('customers', 'view') is None
    assert not policy.allows_action('companies', 'view')
    assert TenantPolicy(person({'customers': {'view': 'self'}})).action_scope('contacts', 'view') == 'self'


@pytest.mark.parametrize('gate', [False, True])
@pytest.mark.parametrize('scope', [None, 'none', 'self', 'department', 'all'])
@pytest.mark.parametrize('legacy_grant', [False, True])
def test_contacts_sql_alias_preserves_legacy_and_explicit_scopes(monkeypatch, gate, scope, legacy_grant):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true' if gate else 'false')
    scopes = {'customers': {'view': scope}} if scope is not None else {}
    p = person(scopes, role='admin', permissions=['customers:view'] if legacy_grant else [])
    predicate = resource_scope_sql(p, 'contacts', 'view')
    assert predicate == resource_scope_sql(p, 'customers', 'view')
    assert TenantPolicy(p).allows_action('contacts', 'view') == (predicate != ('FALSE', []))
    effective_scope = scope if gate and scope is not None else ('all' if legacy_grant else 'none')
    if effective_scope == 'none':
        assert predicate == ('FALSE', [])
    elif effective_scope == 'all':
        assert predicate == ('r.tenant_id=%s::uuid', [A])
    elif effective_scope == 'self':
        assert predicate == ('(r.tenant_id=%s::uuid AND r.access_responsible_id=%s)', [A, 11])
    else:
        assert 'scope_member.tenant_id=r.tenant_id' in predicate[0]
        assert predicate[1] == [A, SALES]


def test_edit_and_delete_do_not_inherit_read_scope():
    p = person({'deals': {'view': 'all', 'update': 'self', 'delete': 'none'}})
    row = dict(tenant_id=A, access_responsible_id=12, responsible_department_id=SALES)
    policy = TenantPolicy(p)
    assert policy.allows_resource('deals', 'view', row)
    assert not policy.allows_resource('deals', 'update', row)
    assert not policy.allows_resource('deals', 'delete', row)


@pytest.mark.parametrize('bad', [None, [], True, {'tasks': None}, {'tasks': {'view': True}},
                                 {'tasks': {'view': 'anything'}}])
def test_malformed_stored_matrix_fails_closed(bad):
    p = person()
    p['workflow']['policies']['members']['11']['access_scopes'] = bad
    assert TenantPolicy(p).action_scope('tasks', 'view') == 'none'


@pytest.mark.parametrize('bad', [[], None, {'invented': {'view': 'all'}},
    {'tasks': {}}, {'tasks': {'manage': 'all'}}, {'tasks': {'view': True}},
    {'dialogs': {'delete': 'all'}}, {'dialogs': {'create': 'all'}}])
def test_catalog_rejects_unimplemented_actions(bad):
    with pytest.raises(ValueError):
        validate_access_scopes(bad)
    assert SCOPE_ACTION_CATALOG['dialogs'] == ('view', 'update')


def test_snapshot_version_tracks_department_and_matrix_but_not_unrelated_work():
    p = person()
    v = access_snapshot(p)['version']
    p['department_id'] = SUPPORT
    assert access_snapshot(p)['version'] != v
    p['department_id'] = SALES
    p['workflow']['policies']['members']['11']['access_scopes']['tasks'] = {'view': 'self'}
    assert access_snapshot(p)['version'] != v


@pytest.mark.parametrize('status,allowed', [('active', True), ('onboarding', True),
    ('ready_for_integration', True), ('disabled', False), ('suspended', False), ('archived', False)])
def test_setup_status_is_not_mistaken_for_suspension(status, allowed):
    p = person({'tasks': {'view': 'all'}}, tenant_status=status)
    assert TenantPolicy(p).allows_resource('tasks', 'view', {'tenant_id': A}) is allowed


def test_sql_is_parameterized_and_rejects_dynamic_identifiers():
    p = person({'tasks': {'view': 'department'}})
    sql, params = resource_scope_sql(p, 'tasks', 'view')
    assert 'scope_member.tenant_id=r.tenant_id' in sql
    assert params == [A, SALES]
    assert A not in sql and SALES not in sql
    assert resource_scope_sql({**p, 'department_id': None}, 'tasks', 'view') == ('FALSE', [])
    with pytest.raises(ValueError):
        resource_scope_sql(p, 'tasks', 'view', alias='r; DROP TABLE dangerous')


def test_assignee_can_open_tasks_without_any_generic_crud_scope():
    from tenant_member_access import page_capabilities
    p = person({'tasks': {a: 'none' for a in ('view', 'create', 'update', 'delete')}})
    assert page_capabilities(p)['tasks']
    assert not any(TenantPolicy(p).allows_action('tasks', a) for a in ('view', 'create', 'update', 'delete'))
    assert not page_capabilities({**p, 'active': False})['tasks']


def test_legacy_native_patch_cannot_silently_edit_beneath_authoritative_scope():
    with pytest.raises(ValueError, match='scope_conflict'):
        apply_access_patch(person({'tasks': {'view': 'self'}}), {'actions': {'tasks': {'view': False}}})


def test_old_workflow_forms_retain_scopes_and_cannot_write_or_erase_them():
    from tenant_scoped_access import preserve_workflow_access_scopes
    old = {'11': {'access_scopes': {'tasks': {'view': 'department'}}, 'ai_enabled': True}}
    assert preserve_workflow_access_scopes(old, {'11': {'ai_enabled': False}}) == {
        '11': {'access_scopes': {'tasks': {'view': 'department'}}, 'ai_enabled': False}}
    assert preserve_workflow_access_scopes(old, {}) == {
        '11': {'access_scopes': {'tasks': {'view': 'department'}}}}
    assert preserve_workflow_access_scopes(old, old) == old
    for value in [{'11': {'access_scopes': {}}}, {'11': {'access_scopes': {'tasks': {'view': 'all'}}}},
                  {'12': {'access_scopes': {'tasks': {'view': 'all'}}}}]:
        with pytest.raises(ValueError, match='requires_employee_editor'):
            preserve_workflow_access_scopes(old, value)
    with pytest.raises(ValueError, match='requires_employee_editor'):
        preserve_workflow_access_scopes({}, old)


@pytest.mark.parametrize('scope,visible', [('none', False), ('self', True), ('department', True), ('all', True)])
def test_finance_read_capabilities_never_advertise_write_grants(scope, visible):
    from tenant_member_access import page_capabilities
    p = person({'finance': {'view': scope}}, permissions=[], role='operator')
    capabilities = TenantPolicy(p).public_capabilities()
    assert capabilities['modules']['finance'] is visible
    assert capabilities['pages']['finance'] is visible
    assert page_capabilities(p)['finance'] is visible
    assert capabilities['finance']['can_write'] is False
    assert capabilities['finance']['read_scope'] == scope
    assert access_snapshot(p)['scope_catalog']['finance'] == ['view']
    for action in ('create', 'update', 'delete'):
        with pytest.raises(ValueError):
            validate_access_scopes({'finance': {action: 'all'}})


def test_legacy_capabilities_do_not_gain_finance_metadata_when_gate_is_off(monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    assert 'finance' not in TenantPolicy(person()).public_capabilities()
