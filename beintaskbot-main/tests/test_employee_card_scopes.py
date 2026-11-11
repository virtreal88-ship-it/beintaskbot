import pytest
from tenant_employee_policy import employee_action_allowed, employee_target_allowed
from tenant_member_store import _card_access, _employee_read_sql
from tenant_policy import TenantPolicy


def actor(view='department', update='self'):
    return {'tenant_id': 'a', 'telegram_id': 2, 'role': 'manager', 'active': True,
            'department_id': 'd1', 'permissions': ['employees'], 'workflow': {'policies': [
                {'policy_key': 'members', 'value': {'2': {'access_scopes': {
                    'employees': {'view': view, 'update': update}}}}}]}}


def target(who=3, department='d1', tenant='a', role='worker'):
    return {'tenant_id': tenant, 'telegram_id': who, 'department_id': department,
            'role': role, 'active': True, 'permissions': []}


@pytest.mark.parametrize('action', ['view', 'update'])
@pytest.mark.parametrize('scope,expected', [('none', [False]*4), ('self', [True,False,False,False]),
    ('department', [True,True,False,False]), ('all', [True,True,True,False])])
def test_independent_matrix(monkeypatch, action, scope, expected):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    profile = actor(scope, scope)
    assert [employee_target_allowed(profile, row, action) for row in [
        target(2), target(), target(4,'d2'), target(2,'d1','b')]] == expected


def test_read_does_not_grant_update_and_self_fields_remain_protected(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    profile = actor('all', 'self')
    assert _card_access(profile, target())[0] == []
    assert _card_access(profile, target(2, role='manager'))[0] == ['whatsapp_numbers']
    assert _card_access(profile, target(role='admin'))[0] == []
    profile['active'] = False
    assert not employee_action_allowed(profile, 'view')


def test_gate_sparse_cells_and_coarse_invitation_guard(monkeypatch):
    profile = actor('none', 'none')
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED', raising=False)
    assert employee_target_allowed(profile, target(), 'view')
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    assert not TenantPolicy(profile).allows_action('employees', 'manage')
    del profile['workflow']['policies'][0]['value']['2']['access_scopes']['employees']['update']
    assert employee_target_allowed(profile, target(), 'update')
    assert not employee_target_allowed(profile, target(), 'view')


def test_sql_is_parameterized_and_null_department_fails_closed(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    profile = actor()
    assert _employee_read_sql(profile) == ('m.department_id=%s::uuid', ['d1'])
    profile['department_id'] = None
    assert _employee_read_sql(profile) == ('FALSE', [])
    profile['tenant_status'] = 'suspended'
    assert not employee_target_allowed(profile, target(), 'update')


def test_catalog_capabilities_no_fake_create_delete(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    from tenant_scoped_access import SCOPE_ACTION_CATALOG
    assert SCOPE_ACTION_CATALOG['employees'] == ('view', 'create', 'update')
    caps = TenantPolicy(actor('self','none')).public_capabilities()
    assert caps['can_view_employees'] and not caps['can_update_employees']
    assert caps['pages']['employees']


@pytest.mark.parametrize('name,role,permissions', [
    ('Changed alias', 'manager', ['employees']),
    ('Original', 'operator', ['employees']),
    ('Original', 'manager', []),
])
def test_scoped_unversioned_upsert_denies_before_any_write(monkeypatch, name, role, permissions):
    from test_tenant_member_store import Connection
    import tenant_member_store as store
    profile = actor('self', 'self')
    connection = Connection(profile)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    monkeypatch.setattr(store, '_connect', lambda: connection)
    monkeypatch.setattr(store, '_locked_members', lambda *args, **kwargs: (profile, profile))
    with pytest.raises(store.MemberCardError) as error:
        store.upsert_member(tenant_id='a', owner_id=2, telegram_id=2,
                            display_name=name, role=role, permissions=permissions)
    assert error.value.code == 'member_field_read_only'
    assert error.value.status == 403
    assert not connection.committed
    assert all('UPDATE ' not in sql and 'INSERT ' not in sql for sql, _ in connection.cursor_value.queries)



@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'display_name': 'Changed alias'}, {'role': 'operator'}, {'permissions': []},
])
async def test_http_legacy_self_upsert_uses_real_store_guard(monkeypatch, changes):
    from unittest.mock import Mock
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_members_api import MemberApiContext, handlers
    from test_tenant_member_store import Connection
    import tenant_member_store as store
    profile = actor('self', 'self')
    connection = Connection(profile)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    monkeypatch.setattr(store, '_connect', lambda: connection)
    monkeypatch.setattr(store, '_locked_members', lambda *args, **kwargs: (profile, profile))
    unused = Mock(side_effect=AssertionError('Unrelated operation'))
    context = MemberApiContext(lambda request: profile, unused, unused, unused,
        'https://app.test', unused, store.upsert_member, unused, unused, unused,
        unused, unused, store.MemberCardError)
    app = web.Application()
    app.router.add_post('/api/platform/members', handlers(context)['members'])
    async with TestClient(TestServer(app)) as client:
        response = await client.post('/api/platform/members', json={
            'action': 'upsert', 'telegram_id': 2, 'display_name': 'Original',
            'role': 'manager', 'permissions': ['employees'], **changes})
        assert response.status == 403
        result = await response.json()
        assert result['code'] == 'member_field_read_only'
        assert 'members' not in result
    assert not connection.committed
    assert all('UPDATE ' not in sql and 'INSERT ' not in sql for sql, _ in connection.cursor_value.queries)
