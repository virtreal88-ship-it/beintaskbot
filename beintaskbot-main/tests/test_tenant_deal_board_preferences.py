import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tenant_deal_board_api import DealBoardContext, handler
import tenant_deal_board_preferences as store
from tenant_platform_core import TenantPlatformError

TENANT = '00000000-0000-0000-0000-000000000101'
CATALOG = [{'pipeline_id': 10, 'stages': [{'stage_id': 100}, {'stage_id': 101}]}]


class Request:
    def __init__(self, method='GET', data=None, origin='https://crm.test'):
        self.method, self.data = method, data
        self.headers = {'Origin': origin}

    async def json(self):
        return self.data


@pytest.mark.parametrize('hidden', [None, {}, '10:100', [1], ['20:100'], ['10:999'], ['0:100'], ['10:100'] * 1001])
def test_reject_invalid_or_foreign_keys(hidden):
    with pytest.raises(TenantPlatformError):
        store.validate_hidden(hidden, {'10:100', '10:101'})


def test_validate_deduplicates():
    assert store.validate_hidden(['10:100', '10:100'], {'10:100'}) == ['10:100']


def test_api_auth_origin_and_method():
    preferences = Mock()
    profile = {'tenant_id': TENANT, 'telegram_id': 17, 'active': True}
    member = Mock(return_value=None)
    call = handler(DealBoardContext(member, preferences, 'https://crm.test', TenantPlatformError))
    assert (asyncio.run(call(Request()))).status == 401
    member.return_value = profile
    assert (asyncio.run(call(Request('DELETE')))).status == 405
    assert (asyncio.run(call(Request('POST', {'hidden_stage_keys': []}, 'https://foreign.test')))).status == 403
    for data in ({'hidden_stage_keys': None}, {'actor_id': 18, 'hidden_stage_keys': []}, []):
        assert (asyncio.run(call(Request('POST', data)))).status == 400
    preferences.assert_not_called()


def test_api_uses_authenticated_identity_and_roundtrip_shape():
    preferences = Mock(return_value={'catalog': CATALOG, 'hidden_stage_keys': ['10:100']})
    call = handler(DealBoardContext(lambda _: {'tenant_id': TENANT, 'telegram_id': 17}, preferences,
                                   'https://crm.test', TenantPlatformError))
    for request, hidden in ((Request(), None), (Request('POST', {'hidden_stage_keys': ['10:100']}), ['10:100'])):
        result = asyncio.run(call(request))
        assert result.status == 200
        assert result.headers['Cache-Control'] == 'no-store'
        assert json.loads(result.body)['catalog'] == CATALOG
        preferences.assert_called_with(tenant_id=TENANT, actor_id=17, hidden=hidden)


def test_api_error_does_not_claim_save():
    preferences = Mock(side_effect=TenantPlatformError('invalid'))
    call = handler(DealBoardContext(lambda _: {'tenant_id': TENANT, 'telegram_id': 17}, preferences,
                                   'https://crm.test', TenantPlatformError))
    result = asyncio.run(call(Request('POST', {'hidden_stage_keys': []})))
    assert result.status == 400 and not json.loads(result.body)['success']
    preferences.side_effect = RuntimeError('secret database detail')
    result = asyncio.run(call(Request()))
    assert result.status == 503 and b'secret' not in result.body


def test_store_fails_closed_without_access(monkeypatch):
    monkeypatch.setattr(store, 'member', lambda *_: None)
    connect = Mock()
    monkeypatch.setattr(store, '_connect', connect)
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17, hidden=[])
    connect.assert_not_called()


def test_jsonb_write_preserves_grants_users_and_tenants(monkeypatch):
    from test_tenant_account_notification_preferences import Database
    database = Database()
    policy = SimpleNamespace(kommo_access=lambda: True, deal_board=lambda: CATALOG, member_settings={})
    monkeypatch.setattr(store, 'TenantPolicy', lambda _: policy)
    monkeypatch.setattr(store, '_connect', lambda: database)
    monkeypatch.setattr(store, 'member', lambda tenant, actor: {'tenant_id': tenant, 'telegram_id': actor})
    try:
        database.query('''CREATE TABLE saas_tenant_workflow_policies(
            tenant_id uuid,policy_key text,value jsonb,updated_by bigint,updated_at timestamptz DEFAULT now(),
            PRIMARY KEY(tenant_id,policy_key))''')
        initial = {'17': {'pipeline_ids': ['10'], 'notifications': {'x': True}}, '18': {'pipeline_ids': ['20']}}
        database.query("INSERT INTO saas_tenant_workflow_policies VALUES(%s::uuid,'members',%s::jsonb,17,now())", (TENANT, json.dumps(initial)))
        other = '00000000-0000-0000-0000-000000000102'
        database.query("INSERT INTO saas_tenant_workflow_policies VALUES(%s::uuid,'members',%s::jsonb,17,now())", (other, json.dumps(initial)))
        result = store.preferences(tenant_id=TENANT, actor_id=17, hidden=['10:100'])
        saved = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (TENANT,))[0]['value']
        assert saved['17']['pipeline_ids'] == ['10']
        assert saved['17']['notifications'] == {'x': True}
        assert saved['18'] == initial['18']
        assert saved['17']['kommo_deal_board'] == {'hidden_stage_keys': ['10:100']}
        assert database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (other,))[0]['value'] == initial
        store.preferences(tenant_id=TENANT, actor_id=19, hidden=['10:101'])
        with_new_actor = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (TENANT,))[0]['value']
        assert with_new_actor['19']['kommo_deal_board']['hidden_stage_keys'] == ['10:101']
        assert with_new_actor['17'] == saved['17']
        policy.member_settings = saved['17']
        assert store.preferences(tenant_id=TENANT, actor_id=17) == result
        # A later rights change cannot disclose stage metadata from old settings.
        policy.deal_board = lambda: []
        assert store.preferences(tenant_id=TENANT, actor_id=17)['hidden_stage_keys'] == []
        policy.deal_board = lambda: CATALOG
        store.preferences(tenant_id=TENANT, actor_id=17, hidden=[])
        assert database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (TENANT,))[0]['value']['17']['kommo_deal_board'] == {'hidden_stage_keys': []}
    finally:
        database.close()
