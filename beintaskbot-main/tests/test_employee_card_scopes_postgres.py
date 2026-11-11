"""Employee list and mutation scopes, synthetic localhost PostgreSQL only."""
import json
import pytest
from test_member_email_assign_postgres import fixture, query, A, B
import tenant_member_store as store

D1 = '00000000-0000-0000-0000-000000009301'
D2 = '00000000-0000-0000-0000-000000009302'


@pytest.fixture
def employees(fixture, monkeypatch):
    monkeypatch.setattr(store, '_connect', fixture)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    with fixture() as conn:
        for dep in [D1, D2]:
            conn.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s,%s,%s)', (A, dep, dep))
        conn.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=41', (D1,A))
        for person, dep in [(51,D1),(52,D2),(53,None)]:
            conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,department_id) VALUES(%s,%s,'Synthetic','worker',%s)", (A,person,dep))
    return fixture


def scopes(f, view='department', update='self'):
    query(f, "INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value", (A, json.dumps({'41': {'access_scopes': {'employees': {'view':view,'update':update}}}})))


@pytest.mark.parametrize('scope,ids', [('self',[41]),('department',[41,51]),('all',[11,41,51,52,53])])
def test_list_has_no_foreign_or_out_of_scope_identity(employees,scope,ids):
    scopes(employees, scope, 'none')
    rows = store.list_members(tenant_id=A,owner_id=41)
    assert sorted(row['telegram_id'] for row in rows) == ids
    assert all(row['editable_fields'] == [] for row in rows)
    assert all(not row['password_assign_available'] for row in rows)
    assert all('department_catalog' not in row and 'workflow' not in row for row in rows)


def test_recheck_department_revocation_and_independent_mutation(employees):
    scopes(employees, 'all', 'department')
    rows = store.list_members(tenant_id=A,owner_id=41)
    version = next(row['card_version'] for row in rows if row['telegram_id']==51)
    query(employees, 'UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=51', (D2,A))
    with pytest.raises(store.MemberCardError):
        store.upsert_member(tenant_id=A,owner_id=41,telegram_id=51,expected_version=version,changes={'display_name':'Denied'})
    with pytest.raises(store.MemberCardError):
        store.deactivate_member(tenant_id=A,owner_id=41,telegram_id=52)
    query(employees, 'UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=41',(A,))
    with pytest.raises(store.MemberCardError):
        store.list_members(tenant_id=A,owner_id=41)


def test_self_safe_fields_only_and_tenant_boundary(employees):
    scopes(employees, 'self', 'self')
    row = store.list_members(tenant_id=A,owner_id=41)[0]
    assert row['editable_fields'] == ['whatsapp_numbers']
    for field,value in [('role','admin'),('active',False),('department_id',D2),('permissions',['employees'])]:
        with pytest.raises(store.MemberCardError):
            store.upsert_member(tenant_id=A,owner_id=41,telegram_id=41,expected_version=row['card_version'],changes={field:value})
    result = store.upsert_member(tenant_id=A,owner_id=41,telegram_id=41,expected_version=row['card_version'],changes={'whatsapp_numbers':[]})
    assert result['card_version'] != row['card_version']
    with pytest.raises(store.MemberCardError):
        store.set_member_whatsapp_numbers(tenant_id=B,owner_id=41,telegram_id=42,numbers=[])


def test_scope_none_denies_read_even_with_legacy_grant(employees):
    query(employees, "UPDATE saas_tenant_members SET permissions='[\"employees\"]'::jsonb WHERE tenant_id=%s AND telegram_id=41", (A,))
    scopes(employees, 'none', 'all')
    with pytest.raises(store.MemberCardError):
        store.list_members(tenant_id=A,owner_id=41)
    # Update remains independent of read; no real messages/accounts are created.
    store.set_member_whatsapp_numbers(tenant_id=A,owner_id=41,telegram_id=51,numbers=[])


@pytest.mark.asyncio
async def test_actual_http_unversioned_self_upsert_denied_and_strict_safe_updates_work(employees):
    from unittest.mock import Mock
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_members_api import MemberApiContext, handlers
    query(employees, "UPDATE saas_tenant_members SET role='manager',permissions='[\"employees\"]'::jsonb WHERE tenant_id=%s AND telegram_id=41", (A,))
    scopes(employees, 'all', 'all')
    unused = Mock(side_effect=AssertionError('Unrelated operation'))
    context = MemberApiContext(lambda request: store.member(A,41), unused, unused, unused,
        'https://app.test', store.list_members, store.upsert_member, unused, unused,
        unused, unused, unused, store.MemberCardError)
    app = web.Application()
    app.router.add_post('/api/platform/members', handlers(context)['members'])
    before = query(employees, 'SELECT * FROM saas_tenant_members ORDER BY tenant_id,telegram_id')
    async with TestClient(TestServer(app)) as client:
        for change in [{'display_name':'Forbidden alias'}, {'role':'operator'}, {'permissions':[]}]:
            response = await client.post('/api/platform/members', json={
                'action':'upsert','telegram_id':41,'display_name':'Synthetic',
                'role':'manager','permissions':['employees'],**change})
            assert response.status == 403
            assert (await response.json())['code'] == 'member_field_read_only'
            assert query(employees, 'SELECT * FROM saas_tenant_members ORDER BY tenant_id,telegram_id') == before
        rows = store.list_members(tenant_id=A,owner_id=41)
        for target_id, change in [(41,{'whatsapp_numbers':[]}), (51,{'display_name':'Permitted alias'})]:
            version = next(row['card_version'] for row in rows if row['telegram_id']==target_id)
            response = await client.post('/api/platform/members', headers={'Origin':'https://app.test'}, json={
                'action':'update_member_card','telegram_id':target_id,'expected_version':version,
                'expected_tenant_id':A,'expected_user_id':41,'changes':change})
            assert response.status == 200
            result = await response.json()
            assert set(result['applied']) == {'telegram_id','card_version'}
        assert query(employees, 'SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=51',(A,)) == [{'display_name':'Permitted alias'}]
