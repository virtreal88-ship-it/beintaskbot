"""Synthetic localhost creation enforcement and lock races, no provider traffic."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import psycopg
import pytest
from test_employee_card_scopes_postgres import employees, fixture, A, B, D1, D2, query
import tenant_member_store as store


def configure(db, scope, *, permissions=None, extra_scopes=None):
    query(db,"UPDATE saas_tenant_members SET role='manager',permissions=%s::jsonb WHERE tenant_id=%s AND telegram_id=41",(json.dumps(permissions if permissions is not None else ['employees']),A))
    query(db,"INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps({'41':{'access_scopes':{'employees':{'view':'none','update':'none','create':scope},**(extra_scopes or {})}}})))


def create(department_id=D1, **kw):
    return store.upsert_member(tenant_id=A,owner_id=41,telegram_id=61,
        display_name='New synthetic employee',role=kw.pop('role','worker'),permissions=kw.pop('permissions',[]),department_id=department_id,**kw)


@pytest.mark.parametrize('scope,department,allowed', [('department',D1,True),('department',D2,False),('department',None,False),('all',D2,True),('all',None,True),('none',D1,False),('self',D1,False)])
def test_real_membership_creation_scopes(employees,scope,department,allowed):
    configure(employees,scope)
    if allowed:
        create(department)
        row=query(employees,'SELECT department_id,permissions FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=61',(A,))[0]
        assert str(row['department_id'])==str(department)
        assert row['permissions']==[]
    else:
        with pytest.raises(store.MemberCardError):create(department)
        assert not query(employees,'SELECT 1 FROM saas_tenant_members WHERE telegram_id=61')


def test_unavailable_department_and_escalation_are_rejected(employees):
    configure(employees,'department',permissions=['employees','tasks'],extra_scopes={'tasks':{'view':'self'}})
    for kwargs in [{'role':'admin'},{'permissions':['employees']},{'permissions':['tasks']}]:
        with pytest.raises(store.MemberCardError):create(**kwargs)
    query(employees,'UPDATE saas_tenant_departments SET active=FALSE WHERE tenant_id=%s AND id=%s',(A,D1))
    with pytest.raises(store.MemberCardError):create()
    with pytest.raises(store.MemberCardError):store.employee_creation_options(tenant_id=A,owner_id=41)
    configure(employees,'all')
    with pytest.raises(store.MemberCardError):create('00000000-0000-0000-0000-000000009399')
    assert not query(employees,'SELECT 1 FROM saas_tenant_members WHERE telegram_id=61')


def test_creation_options_reveal_only_own_department_without_read(employees):
    configure(employees,'department')
    options=store.employee_creation_options(tenant_id=A,owner_id=41)
    assert [d['id'] for d in options['departments']]==[D1]
    assert options['department_required']
    assert 'employees' not in options['permission_keys']
    with pytest.raises(store.MemberCardError):store.list_members(tenant_id=A,owner_id=41)
    query(employees,'UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=41',(D2,A))
    with pytest.raises(store.MemberCardError):create(D1)
    create(D2)


def test_creation_holds_tenant_membership_policy_and_department_fences(employees, monkeypatch):
    configure(employees,'department')
    checked=Event();release=Event();original=store._creation_department
    def held(*args):
        result=original(*args);checked.set();assert release.wait(10);return result
    monkeypatch.setattr(store,'_creation_department',held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(create)
        try:
            assert checked.wait(5)
            statements=[('SELECT id FROM saas_tenants WHERE id=%s FOR UPDATE',(A,)),
                ('SELECT id FROM saas_tenants WHERE id=%s FOR SHARE',(A,)),
                ('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=41',(A,)),
                ("UPDATE saas_tenant_workflow_policies SET value='{}' WHERE tenant_id=%s AND policy_key='members'",(A,)),
                ('UPDATE saas_tenant_departments SET active=FALSE WHERE tenant_id=%s AND id=%s',(A,D1))]
            for sql,args in statements:
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with employees() as conn:
                        conn.execute("SET LOCAL lock_timeout='100ms'")
                        conn.execute(sql,args)
        finally:release.set()
        future.result(timeout=5)
    assert query(employees,'SELECT department_id FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=61',(A,))[0]['department_id'].__str__()==D1


@pytest.mark.asyncio
async def test_http_c_without_r_provisions_in_department_with_minimal_receipt(employees,monkeypatch):
    from unittest.mock import Mock
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_members_api import MemberApiContext,handlers
    import tenant_email_auth
    monkeypatch.setattr(tenant_email_auth,'_hash_password',lambda password:'synthetic-test-hash')
    configure(employees,'department')
    unused=Mock(side_effect=AssertionError('No external calls'))
    context=MemberApiContext(lambda request:store.member(A,41),unused,unused,unused,'https://app.test',
        store.list_members,store.upsert_member,unused,unused,unused,unused,unused,store.MemberCardError,
        provision_employee_account=store.provision_employee_account,employee_creation_options=store.employee_creation_options)
    app=web.Application();handler=handlers(context)['members'];app.router.add_get('/api/platform/members',handler);app.router.add_post('/api/platform/members',handler)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/api/platform/members')).status==403
        response=await client.get('/api/platform/members?options=create');assert response.status==200
        assert [d['id'] for d in (await response.json())['creation_options']['departments']]==[D1]
        payload={'action':'provision_member_account','expected_tenant_id':A,'expected_user_id':41,
            'first_name':'Synthetic','last_name':'Employee','login':'synthetic.created','password':'Synthetic-test-pass-123!',
            'confirm_password':'Synthetic-test-pass-123!','role':'worker','permissions':[],'department_id':D1}
        response=await client.post('/api/platform/members',headers={'Origin':'https://app.test'},json=payload)
        assert response.status==200
        result=await response.json();assert set(result)=={'success','applied'}
        assert set(result['applied'])=={'telegram_id','card_version','created'}
        assert query(employees,'SELECT department_id FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=%s',(A,result['applied']['telegram_id']))[0]['department_id'].__str__()==D1


def test_explicit_c_without_legacy_grants_and_revocation(employees):
    configure(employees,'department',permissions=[])
    create()
    query(employees,'UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=41',(A,))
    with pytest.raises(store.MemberCardError):
        store.employee_creation_options(tenant_id=A,owner_id=41)


def test_foreign_department_is_never_a_creation_target(employees):
    other='00000000-0000-0000-0000-000000009399'
    query(employees,"INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s,%s,'Other company')",(B,other))
    configure(employees,'all')
    with pytest.raises(store.MemberCardError):create(other)
    assert other not in [row['id'] for row in store.employee_creation_options(tenant_id=A,owner_id=41)['departments']]


@pytest.mark.parametrize('operation',['membership','provision'])
def test_sparse_c_cannot_create_controlled_account_with_broader_grants(employees,monkeypatch,operation):
    import tenant_email_auth
    monkeypatch.setattr(tenant_email_auth,'_hash_password',lambda password:'synthetic-test-hash')
    configure(employees,'all',permissions=['employees','tasks'],extra_scopes={
        'tasks':{'view':'self','create':'none','update':'none','delete':'none'}})
    query(employees,"UPDATE saas_tenant_workflow_policies SET value=value #- '{41,access_scopes,employees,create}' WHERE tenant_id=%s AND policy_key='members'",(A,))
    options=store.employee_creation_options(tenant_id=A,owner_id=41)
    assert 'tasks' not in options['permission_keys']
    before={table:query(employees,f'SELECT to_jsonb(t) AS row FROM {table} t ORDER BY to_jsonb(t)::text')
        for table in ['saas_tenant_members','saas_auth_users','saas_user_profiles','saas_tenant_audit_events']}
    with pytest.raises(store.MemberCardError) as error:
        if operation=='membership':
            create(permissions=['tasks'])
        else:
            store.provision_employee_account(tenant_id=A,owner_id=41,first_name='Synthetic',last_name='Employee',
                login='synthetic.escalation',password='Synthetic-test-pass-123!',password_confirm='Synthetic-test-pass-123!',
                role='worker',permissions=['tasks'],department_id=D1)
    assert error.value.code=='member_field_read_only'
    assert {table:query(employees,f'SELECT to_jsonb(t) AS row FROM {table} t ORDER BY to_jsonb(t)::text') for table in before}==before
    # An empty-grant membership still uses unchanged sparse-C admission.
    create(permissions=[])


def test_customers_company_alias_and_hot_order_defaults_cannot_be_granted(employees):
    from tenant_policy import TenantPolicy
    from tenant_hot_order_policy import HotOrderPolicy
    configure(employees,'all',permissions=['employees','customers','hot_orders'],extra_scopes={
        'companies':{'view':'none','create':'none','update':'none','delete':'none'}})
    query(employees,"UPDATE saas_tenants SET modules='{\"customers\":true,\"hot_orders\":true}'::jsonb WHERE id=%s",(A,))
    query(employees,"UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{41,hot_order_create}','false') WHERE tenant_id=%s AND policy_key='members'",(A,))
    query(employees,"INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'hot_orders','{\"create_roles\":[\"manager\"]}'::jsonb)",(A,))
    actor=store.member(A,41)
    assert not TenantPolicy(actor).allows_action('companies','update')
    assert not HotOrderPolicy(TenantPolicy(actor)).allowed('create')
    for grant in ['customers','hot_orders']:
        with pytest.raises(store.MemberCardError):create(role='manager',permissions=[grant])
        assert not query(employees,'SELECT 1 FROM saas_tenant_members WHERE telegram_id=61')
    create(role='manager',permissions=[])
    child=store.member(A,61)
    assert not TenantPolicy(child).allows_action('companies','update')
    assert not HotOrderPolicy(TenantPolicy(child)).allowed('create')


def test_u_grant_expansion_denied_retained_rights_unchanged_and_matrix_owner_only(employees):
    configure(employees,'all',permissions=['employees','tasks','customers'],extra_scopes={
        'tasks':{'view':'self','create':'none','update':'none','delete':'none'},
        'companies':{'view':'none','create':'none','update':'none','delete':'none'}})
    query(employees,"UPDATE saas_tenants SET modules='{\"tasks\":true,\"customers\":true}'::jsonb WHERE id=%s",(A,))
    query(employees,"UPDATE saas_tenant_workflow_policies SET value=jsonb_set(jsonb_set(value,'{41,access_scopes,employees,view}','\"all\"'),'{41,access_scopes,employees,update}','\"all\"') WHERE tenant_id=%s AND policy_key='members'",(A,))
    def version():
        return query(employees,'SELECT updated_at FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=51',(A,))[0]['updated_at'].isoformat()
    before=query(employees,'SELECT * FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=51',(A,))
    with pytest.raises(store.MemberCardError):
        store.upsert_member(tenant_id=A,owner_id=41,telegram_id=51,expected_version=version(),changes={'permissions':['tasks']})
    with pytest.raises(store.MemberCardError):
        store.upsert_member(tenant_id=A,owner_id=41,telegram_id=51,display_name='Changed',role='worker',permissions=['tasks'])
    assert query(employees,'SELECT * FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=51',(A,))==before
    query(employees,"UPDATE saas_tenant_members SET permissions='[\"customers\"]'::jsonb WHERE tenant_id=%s AND telegram_id=51",(A,))
    with pytest.raises(store.MemberCardError):
        store.upsert_member(tenant_id=A,owner_id=41,telegram_id=51,expected_version=version(),changes={'role':'manager'})
    query(employees,"UPDATE saas_tenant_members SET permissions='[\"tasks\"]'::jsonb WHERE tenant_id=%s AND telegram_id=51",(A,))
    store.upsert_member(tenant_id=A,owner_id=41,telegram_id=51,expected_version=version(),changes={'display_name':'Safe alias'})
    assert query(employees,'SELECT permissions FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=51',(A,))==[{'permissions':['tasks']}]
    policy_before=query(employees,"SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(A,))
    with pytest.raises(store.MemberCardError) as error:
        store.upsert_member(tenant_id=A,owner_id=41,telegram_id=51,expected_version=version(),changes={
            'access_settings':{'expected_version':'forged','changes':{'scopes':{'tasks':{'view':'all','update':'all'}}}}})
    assert error.value.status==403 and error.value.fields['access_settings']=='owner_protected'
    assert query(employees,"SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(A,))==policy_before
