import asyncio
import json
from unittest.mock import Mock

import pytest

import tenant_deal_view_preferences as store
import tenant_task_view_preferences as task_store
from tenant_deal_view_api import DealViewContext, handler
from tenant_platform_core import TenantPlatformError
from test_tenant_task_view_preferences import Request, TENANT, OTHER


def profile(settings=None, **changes):
    return {**{'tenant_id': TENANT, 'telegram_id': 17, 'active': True,
        'role': 'manager', 'permissions': ['deals'], 'modules': {'deals': True},
        'workflow': {'policies': {'members': {'17': settings or {}}}}}, **changes}


def test_deal_api_authenticated_identity_csrf_and_input():
    save = Mock(return_value={'source': 'kommo'})
    member = Mock(return_value=profile())
    call = handler(DealViewContext(member, save, 'https://crm.test', TenantPlatformError))
    for req in (Request(), Request('POST', {'source': 'kommo'})):
        response = asyncio.run(call(req))
        assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
        assert json.loads(response.body) == {'success': True, 'source': 'kommo'}
        save.assert_called_with(tenant_id=TENANT, actor_id=17,
                                source='kommo' if req.method == 'POST' else None)
    save.reset_mock()
    assert asyncio.run(call(Request('POST', {'source': 'native'}, 'https://foreign.test'))).status == 403
    for data in ({'source': None}, {'source': []}, {'source': 'all'}, {'source': 'native', 'tenant_id': OTHER}):
        assert asyncio.run(call(Request('POST', data))).status == 400
    assert asyncio.run(call(Request(tenant=OTHER))).status == 409
    assert asyncio.run(call(Request(actor='18'))).status == 409
    missing = Request(); missing.query.clear()
    assert asyncio.run(call(missing)).status == 400
    member.return_value = None
    assert asyncio.run(call(Request())).status == 401
    save.assert_not_called()


def test_source_access_is_native_scoped_and_kommo_independent(monkeypatch):
    monkeypatch.setattr('tenant_policy.TenantPolicy.action_scope', lambda self, module, action: 'none' if module == 'deals' else None)
    current = profile({'kommo_user_id': 123, 'deal_view': {'source': 'native'}})
    member = Mock(return_value=current)
    monkeypatch.setattr(store, 'member', member)
    connect = Mock(); monkeypatch.setattr(store, '_connect', connect)
    # A saved source becoming unavailable remains a presentation preference.
    assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'native'}
    member.assert_called_with(TENANT, 17)
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17, source='native')
    member.return_value = profile()
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17)
    connect.assert_not_called()


def test_native_grant_does_not_grant_kommo_and_no_read_rewrites(monkeypatch):
    current = profile({'deal_view': {'source': 'kommo'}})
    monkeypatch.setattr(store, 'member', lambda *_: current)
    connect = Mock(); monkeypatch.setattr(store, '_connect', connect)
    assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'kommo'}
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17, source='kommo')
    current['workflow'] = {}
    assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'kommo'}
    current['permissions'] = {'deals': True}
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17)
    current['active'] = False
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17)
    connect.assert_not_called()


def test_shared_storage_keeps_task_deal_preferences_grants_and_tenants_independent(monkeypatch):
    from test_tenant_account_notification_preferences import Database
    database = Database()
    def member(tenant, actor):
        rows = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (tenant,))
        return profile(role='owner', tenant_id=tenant, telegram_id=actor,
            workflow={'policies': {'members': rows[0]['value'] if rows else {}}})
    for module in (store, task_store):
        monkeypatch.setattr(module, '_connect', lambda: database)
        monkeypatch.setattr(module, 'member', member)
    try:
        database.query('''CREATE TABLE saas_tenant_workflow_policies(
            tenant_id uuid,policy_key text,value jsonb,updated_by bigint,updated_at timestamptz DEFAULT now(),
            PRIMARY KEY(tenant_id,policy_key))''')
        initial = {'17': {'pipeline_ids': ['10'], 'kommo_deal_board': {'hidden_stage_keys': ['10:100']},
                         'task_view': {'source': 'kommo'}, 'native_crm_actions': {'deals': {'delete': False}}},
                   '18': {'deal_view': {'source': 'kommo'}}}
        for tenant in (TENANT, OTHER):
            database.query("INSERT INTO saas_tenant_workflow_policies VALUES(%s::uuid,'members',%s::jsonb,17,now())", (tenant, json.dumps(initial)))
        store.preferences(tenant_id=TENANT, actor_id=17, source='native')
        assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'native'}
        assert task_store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'kommo'}
        def saved(tenant):
            return database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (tenant,))[0]['value']
        assert saved(TENANT) == {**initial, '17': {**initial['17'], 'deal_view': {'source': 'native'}}}
        assert saved(OTHER) == initial
        task_store.preferences(tenant_id=TENANT, actor_id=17, source='native')
        store.preferences(tenant_id=TENANT, actor_id=17, source='kommo')
        assert saved(TENANT)['17'] == {**initial['17'], 'task_view': {'source': 'native'}, 'deal_view': {'source': 'kommo'}}
    finally:
        database.close()


@pytest.mark.parametrize('value', [None, [], {'source': []}, {'source': 'unknown'}])
def test_malformed_saved_preference_uses_default_without_write(monkeypatch, value):
    monkeypatch.setattr(store, 'member', lambda *_: profile({'deal_view': value}))
    connect = Mock(); monkeypatch.setattr(store, '_connect', connect)
    assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'kommo'}
    connect.assert_not_called()
