import asyncio
import json
from unittest.mock import Mock

import pytest
from multidict import MultiDict

from tenant_task_view_api import TaskViewContext, handler
import tenant_task_view_preferences as store
from tenant_platform_core import TenantPlatformError

TENANT = '00000000-0000-0000-0000-000000000101'
OTHER = '00000000-0000-0000-0000-000000000102'


class Request:
    def __init__(self, method='GET', data=None, origin='https://crm.test', tenant=TENANT, actor='17'):
        self.method, self.data = method, data
        self.headers = {'Origin': origin}
        self.query = MultiDict(expected_tenant_id=tenant, expected_user_id=actor)

    async def json(self):
        return self.data


def profile(**changes):
    return dict(tenant_id=TENANT, telegram_id=17, active=True, role='operator',
                permissions=['tasks'], modules={'tasks': True}, **changes)


def test_api_auth_csrf_input_and_context():
    preferences = Mock()
    member = Mock(return_value=None)
    call = handler(TaskViewContext(member, preferences, 'https://crm.test', TenantPlatformError))
    assert asyncio.run(call(Request())).status == 401
    member.return_value = profile()
    assert asyncio.run(call(Request('DELETE'))).status == 405
    for origin in ('', 'https://foreign.test'):
        assert asyncio.run(call(Request('POST', {'source': 'native'}, origin))).status == 403
    for data in ({'source': None}, {'source': []}, {'source': 'all'}, [], {'source': 'native', 'actor_id': 18}):
        assert asyncio.run(call(Request('POST', data))).status == 400
    for request in (Request(tenant=OTHER), Request(actor='18')):
        assert asyncio.run(call(request)).status == 409
    request = Request(); request.query.clear()
    assert asyncio.run(call(request)).status == 400
    request = Request(); request.query.add('expected_user_id', '18')
    assert asyncio.run(call(request)).status == 400
    preferences.assert_not_called()


def test_api_only_authenticated_identity_and_no_store():
    preferences = Mock(return_value={'source': 'native'})
    call = handler(TaskViewContext(lambda _: profile(), preferences, 'https://crm.test', TenantPlatformError))
    for request, source in ((Request(), None), (Request('POST', {'source': 'native'}), 'native')):
        result = asyncio.run(call(request))
        assert result.status == 200
        assert result.headers['Cache-Control'] == 'no-store'
        assert json.loads(result.body) == {'success': True, 'source': 'native'}
        preferences.assert_called_with(tenant_id=TENANT, actor_id=17, source=source)
    preferences.side_effect = RuntimeError('secret database detail')
    result = asyncio.run(call(Request()))
    assert result.status == 503 and b'secret' not in result.body
    preferences.side_effect = TenantPlatformError('invalid')
    assert asyncio.run(call(Request())).status == 400


@pytest.mark.parametrize('source', [None, {}, [], True, 1, 'Beintaskbot', 'all', ''])
def test_invalid_source(source):
    with pytest.raises(TenantPlatformError):
        store.validate_source(source)


def test_current_member_access_and_unavailable_saved_preference(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    current = profile(workflow={'policies': {'members': {'17': {'task_view': {'source': 'kommo'}}}}})
    member = Mock(return_value=current)
    monkeypatch.setattr(store, 'member', member)
    connect = Mock(); monkeypatch.setattr(store, '_connect', connect)
    monkeypatch.setattr(store, '_sources', lambda policy: [{'source': 'native', 'view': True}, {'source': 'kommo', 'view': False}])
    assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'kommo'}
    member.assert_called_with(TENANT, 17)
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17, source='kommo')
    for value in (None, {**current, 'active': False}, {**current, 'tenant_status': 'suspended'}):
        member.return_value = value
        with pytest.raises(TenantPlatformError):
            store.preferences(tenant_id=TENANT, actor_id=17, source='native')
    connect.assert_not_called()


def test_sources_use_existing_policy_without_granting_access(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    current = profile()
    current['permissions'] = []
    monkeypatch.setattr(store, 'member', lambda *_: current)
    monkeypatch.setattr('tenant_task_flow.scoped_access_enabled', lambda: False)
    with pytest.raises(TenantPlatformError):
        store.preferences(tenant_id=TENANT, actor_id=17)
    current['permissions'] = ['tasks']
    assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'native'}


def test_jsonb_tenant_user_isolation_and_roundtrip(monkeypatch):
    from test_tenant_account_notification_preferences import Database
    database = Database()
    monkeypatch.setattr(store, '_connect', lambda: database)
    def member(tenant, actor):
        rows = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (tenant,))
        return {**profile(), 'tenant_id': tenant, 'telegram_id': actor,
                'workflow': {'policies': {'members': rows[0]['value'] if rows else {}}}}
    monkeypatch.setattr(store, 'member', member)
    try:
        database.query('''CREATE TABLE saas_tenant_workflow_policies(
            tenant_id uuid,policy_key text,value jsonb,updated_by bigint,updated_at timestamptz DEFAULT now(),
            PRIMARY KEY(tenant_id,policy_key))''')
        initial = {'17': {'pipeline_ids': ['10'], 'notifications': {'x': True}, 'native_crm_actions': {'tasks': {'delete': False}}},
                   '18': {'pipeline_ids': ['20']}}
        for tenant in (TENANT, OTHER):
            database.query("INSERT INTO saas_tenant_workflow_policies VALUES(%s::uuid,'members',%s::jsonb,17,now())", (tenant, json.dumps(initial)))
        assert store.preferences(tenant_id=TENANT, actor_id=17, source='kommo') == {'source': 'kommo'}
        assert store.preferences(tenant_id=TENANT, actor_id=17) == {'source': 'kommo'}
        saved = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (TENANT,))[0]['value']
        assert saved == {**initial, '17': {**initial['17'], 'task_view': {'source': 'kommo'}}}
        assert database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (OTHER,))[0]['value'] == initial
        store.preferences(tenant_id=TENANT, actor_id=19, source='native')
        newer = database.query("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid", (TENANT,))[0]['value']
        assert newer == {**saved, '19': {'task_view': {'source': 'native'}}}
        fresh = '00000000-0000-0000-0000-000000000103'
        store.preferences(tenant_id=fresh, actor_id=17, source='native')
        assert store.preferences(tenant_id=fresh, actor_id=17) == {'source': 'native'}
    finally:
        database.close()
