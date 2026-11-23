import asyncio
import json
from unittest.mock import Mock
import pytest
from tenant_linear_policy import TenantLinearError
import tenant_dialog_view_preferences as store

A = '00000000-0000-0000-0000-000000000101'
B = '00000000-0000-0000-0000-000000000102'
AGENT = '00000000-0000-0000-0000-000000000201'
PROFILE = {'tenant_id': A, 'telegram_id': 17, 'active': True}


@pytest.mark.parametrize('bad', [[], None, {}, {**store.DEFAULTS,'extra':1},
    {**store.DEFAULTS,'agent':True}, {**store.DEFAULTS,'agent':'17'},
    {**store.DEFAULTS,'state':'unknown'}, {**store.DEFAULTS,'lifecycle':'closed'},
    {**store.DEFAULTS,'channel':'connect:'+'x'*250}, {**store.DEFAULTS,'channel':'connect:\nsecret'},
    {**store.DEFAULTS,'channel':'other:test'}])
def test_invalid_payload(bad):
    with pytest.raises(TenantLinearError): store.validate(bad)


def test_saved_stale_choices_and_scoped_ai(monkeypatch):
    cur = Mock(); cur.fetchone.return_value = {'allowed': False}
    policy = Mock(); policy.profile = {'role':'owner'}
    monkeypatch.setattr(store,'projection',lambda *a: ('WITH visible AS (SELECT 1) ', []))
    value = {**store.DEFAULTS,'channel':'connect:'+A,'agent':AGENT,'state':'manual'}
    assert store._available(cur,A,policy,value) == {**value,'channel':'','agent':''}
    assert cur.execute.call_args.args[1] == (A, AGENT)
    policy.profile = {'role':'operator'};policy.has_scoped_access.return_value = True
    cur.reset_mock()
    assert store._available(cur,A,policy,{**value,'channel':''}) == {**store.DEFAULTS}
    cur.execute.assert_not_called()


def test_atomic_roundtrip_preserves_other_members_tenants_and_permissions(monkeypatch):
    from test_tenant_account_notification_preferences import Database
    database = Database()
    monkeypatch.setattr(store,'_connect',lambda:database)
    monkeypatch.setattr(store,'ensure_schema',lambda conn:None)
    monkeypatch.setattr(store,'ensure_connect_schema',lambda conn:None)
    monkeypatch.setattr(store,'_available',lambda cur, tenant, policy, value:value)
    def access(cur, profile, permission):
        assert permission == 'dialogs'
        rows = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid",(profile['tenant_id'],))
        policy = Mock();policy.member_settings = (rows[0]['value'] if rows else {}).get(str(profile['telegram_id']),{})
        return profile['tenant_id'],profile['telegram_id'],policy
    monkeypatch.setattr(store,'access',access)
    try:
        database.query('''CREATE TABLE saas_tenant_workflow_policies(tenant_id uuid,policy_key text,value jsonb,
          updated_by bigint,updated_at timestamptz DEFAULT now(),PRIMARY KEY(tenant_id,policy_key))''')
        initial={'17':{'permissions':['dialogs'],'notifications':{'x':True}},'18':{'role':'owner'}}
        for tenant in (A,B):
            database.query("INSERT INTO saas_tenant_workflow_policies VALUES(%s::uuid,'members',%s::jsonb,17,now())",(tenant,json.dumps(initial)))
        value={**store.DEFAULTS,'channel':'connect:'+A,'agent':AGENT,'state':'manual','lifecycle':'completed'}
        assert store.preferences(PROFILE,value)==value
        assert store.preferences(PROFILE)==value
        actual=database.query('SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid',(A,))[0]['value']
        assert actual=={**initial,'17':{**initial['17'],'dialog_view':value}}
        assert store.preferences({**PROFILE,'telegram_id':18})==store.DEFAULTS
        assert database.query('SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid',(B,))[0]['value']==initial
        store.preferences({**PROFILE,'telegram_id':19},store.DEFAULTS)
        assert store.preferences({**PROFILE,'telegram_id':19})==store.DEFAULTS
    finally: database.close()


class Request:
    def __init__(self,method='GET',data=None,origin='https://crm.test',headers=None):
        self.method=method;self.headers={'Origin':origin,**(headers or {})};self.query={}
        self.content_type='application/json';self.raw=json.dumps(data).encode();self.content_length=len(self.raw)
    async def read(self): return self.raw


def test_api_auth_identity_csrf_shape_and_no_store(monkeypatch):
    import tenant_operator_api as api
    member=Mock(return_value=None);service=Mock(return_value=dict(store.DEFAULTS))
    monkeypatch.setattr(api,'dialog_view_preferences',service)
    handle=api.handler(member,'https://crm.test',Mock(),'view_preferences')
    assert asyncio.run(handle(Request())).status==403
    member.return_value=PROFILE
    for origin in ('','https://foreign.test'):
        assert asyncio.run(handle(Request('POST',{},origin))).status==403
    bad_identity={**store.DEFAULTS,'expected_tenant_id':B,'expected_user_id':'17'}
    assert asyncio.run(handle(Request('POST',bad_identity))).status==409
    assert asyncio.run(handle(Request(headers={'X-Tenant-Id':A,'X-User-Id':'18'}))).status==409
    service.assert_not_called()
    data={**store.DEFAULTS,'expected_tenant_id':A,'expected_user_id':'17'}
    for request in (Request(headers={'X-Tenant-Id':A,'X-User-Id':'17'}),Request('POST',data)):
        response=asyncio.run(handle(request));assert response.status==200
        assert response.headers['Cache-Control']=='no-store'
        assert json.loads(response.body)=={'success':True,'result':store.DEFAULTS}
    service.assert_called_with(PROFILE,store.DEFAULTS)
    service.side_effect=TenantLinearError('forbidden',403)
    assert asyncio.run(handle(Request())).status==403
    service.side_effect=RuntimeError('private detail')
    response=asyncio.run(handle(Request()));assert response.status==503 and b'private' not in response.body

# Exercise the real authorized projection and current membership against PostgreSQL/WASM.
from test_tenant_support_inbox import database, db
from test_tenant_operator_workspace import operator_db


def test_real_projection_resets_foreign_sources_and_revoked_member(operator_db, monkeypatch):
    from test_tenant_support_inbox import PROFILE as owner, CA, CB
    from test_tenant_operator_workspace import employee
    monkeypatch.setattr(store,'_connect',lambda:operator_db)
    chosen={**store.DEFAULTS,'channel':'connect:'+CA}
    assert store.preferences(owner,chosen)==chosen
    assert store.preferences(owner)==chosen
    assert store.preferences(owner,{**chosen,'channel':'connect:'+CB})==store.DEFAULTS
    person=employee(operator_db,['dialogs'])
    assert store.preferences(person,chosen)==chosen
    operator_db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=%s',
                        (person['tenant_id'],person['telegram_id']))
    operator_db.commit()
    with pytest.raises(TenantLinearError) as error: store.preferences(person)
    assert error.value.status==403


def test_company_panel_member_tenant_preference_and_independent_filter(operator_db, monkeypatch):
    from test_tenant_support_inbox import PROFILE as owner
    from test_tenant_operator_workspace import employee
    monkeypatch.setattr(store,'_connect',lambda:operator_db)
    person=employee(operator_db,['dialogs'])
    assert store.company_panel_preferences(person)=={'open':False}
    for bad in ({}, {'open':1}, {'open':'true'}, {'open':True,'channel':'foreign'}):
        with pytest.raises(TenantLinearError) as error:
            store.company_panel_preferences(person,bad)
        assert error.value.status==400
    filters={**store.DEFAULTS,'lifecycle':'completed'}
    store.preferences(person,filters)
    assert store.company_panel_preferences(person,{'open':True})=={'open':True}
    assert store.company_panel_preferences(person)=={'open':True}
    assert store.preferences(person)==filters
    assert store.company_panel_preferences(owner)=={'open':False}
    assert store.company_panel_preferences(person,{'open':False})=={'open':False}
    assert store.company_panel_preferences(person)=={'open':False}
    operator_db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=%s',
                        (person['tenant_id'],person['telegram_id']))
    operator_db.commit()
    for value in (None,{'open':True}):
        with pytest.raises(TenantLinearError) as error:
            store.company_panel_preferences(person,value)
        assert error.value.status==403


def test_company_panel_preference_endpoint_fences_expected_identity_and_csrf(monkeypatch):
    import tenant_operator_api as api
    service=Mock(return_value={'open':True})
    monkeypatch.setattr(api,'company_panel_preferences',service)
    handle=api.handler(Mock(return_value=PROFILE),'https://crm.test',Mock(),'company_panel_preferences')
    identity={'expected_tenant_id':A,'expected_user_id':'17'}
    assert asyncio.run(handle(Request('POST',{'open':True,**identity},'https://foreign.test'))).status==403
    assert asyncio.run(handle(Request('POST',{'open':True,**identity,'expected_tenant_id':B}))).status==409
    assert asyncio.run(handle(Request(headers={'X-Tenant-Id':B,'X-User-Id':'17'}))).status==409
    service.assert_not_called()
    for request in (Request(headers={'X-Tenant-Id':A,'X-User-Id':'17'}),Request('POST',{'open':True,**identity})):
        response=asyncio.run(handle(request))
        assert response.status==200
        assert response.headers['Cache-Control']=='no-store'
        assert json.loads(response.body)['result']=={'open':True}
    service.assert_called_with(PROFILE,{'open':True})
