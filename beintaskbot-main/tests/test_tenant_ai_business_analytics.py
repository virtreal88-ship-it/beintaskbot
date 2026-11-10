from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import tenant_ai_business_analytics as analytics
import tenant_ai_business_analytics_api as api
from tenant_linear_policy import TenantLinearError


TENANT = '11111111-1111-4111-8111-111111111111'
OWNER = {'tenant_id': TENANT, 'telegram_id': 7}


class _Cursor:
    def __init__(self, present=()):
        self.present = set(present)
        self.current = None
        self.calls = []

    def execute(self, sql, params):
        assert sql == 'SELECT to_regclass(%s) AS relation'
        relation = params[0]
        assert relation in {name for source in analytics._SOURCE_CATALOG for name in source['relations']}
        self.calls.append((sql, params))
        self.current = {'relation': relation if relation in self.present else None}

    def fetchone(self):
        return self.current


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @contextmanager
    def cursor(self):
        yield self._cursor


def _owner_access(monkeypatch, role='owner'):
    monkeypatch.setattr(analytics, 'access', lambda cur, profile, permission: (
        profile['tenant_id'], profile['telegram_id'], SimpleNamespace(profile={'role': role})))


def test_sources_tool_is_schema_only_and_uses_static_relations(monkeypatch):
    _owner_access(monkeypatch)
    relations = {relation for source in analytics._SOURCE_CATALOG for relation in source['relations']}
    cursor = _Cursor(relations)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(cursor))
    monkeypatch.setattr('tenant_platform._ensure_schema', lambda conn: None)
    events = []
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: events.append(event))

    result = analytics.execute(OWNER, 'list_analytics_sources', {})

    assert result['read_only'] is True
    assert result['assistant_connected'] is False
    assert result['checked_at'] is None
    assert all(source['schema_status'] == 'present' for source in result['sources'])
    assert all(source['freshness'] == 'not_checked' for source in result['sources'])
    assert all(source['record_coverage'] == 'not_checked' for source in result['sources'])
    assert len(cursor.calls) == len(relations)
    assert events == [{'tenant_id': TENANT, 'actor_telegram_id': 7,
        'action': 'ai_business_analytics_tool_read', 'entity_type': 'analytics_tool',
        'entity_id': 'list_analytics_sources',
        'payload': {'tool': 'list_analytics_sources', 'read_only': True}}]


def test_source_tool_fails_closed_for_non_owner(monkeypatch):
    _owner_access(monkeypatch, role='admin')
    cursor = _Cursor()
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(cursor))
    monkeypatch.setattr('tenant_platform._ensure_schema', lambda conn: None)

    with pytest.raises(TenantLinearError) as error:
        analytics.execute(OWNER, 'list_analytics_sources', {})

    assert error.value.status == 403
    assert not cursor.calls


def test_missing_relation_is_not_reported_as_current_or_complete(monkeypatch):
    _owner_access(monkeypatch)
    relations = {relation for source in analytics._SOURCE_CATALOG for relation in source['relations']}
    relations.remove('saas_crm_messages')
    cursor = _Cursor(relations)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(cursor))

    result = analytics.list_analytics_sources(OWNER)

    connect = next(source for source in result['sources'] if source['id'] == 'connect_conversations')
    assert connect['schema_status'] == 'partial_or_missing'
    assert connect['freshness'] == 'not_checked'
    assert connect['record_coverage'] == 'not_checked'
    assert result['assistant_connected'] is False


@pytest.mark.parametrize('tool,args', [
    ('run_sql', {}),
    ('list_analytics_sources', {'tenant_id': TENANT}),
    ('get_company_risks', {'table': 'saas_crm_messages'}),
    ('get_company_risks', {'limit': 21}),
])
def test_tool_registry_rejects_unknown_or_unbounded_arguments(monkeypatch, tool, args):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    monkeypatch.setattr('tenant_platform._ensure_schema', lambda conn: None)

    with pytest.raises(TenantLinearError):
        analytics.execute(OWNER, tool, args)


def test_company_risks_reuses_owner_reader_and_removes_identity_fields(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    monkeypatch.setattr('tenant_platform._ensure_schema', lambda conn: None)
    calls = []
    monkeypatch.setattr('tenant_proactive_risk_store.list_findings', lambda profile, limit: (
        calls.append((profile, limit)) or {'tenant_id': TENANT, 'user_id': 7,
          'findings': [{'rule_id': 'task_overdue', 'facts': ['source evidence']}], 'checked_at': 'now'}))
    events = []
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: events.append(event))

    result = analytics.execute(OWNER, 'get_company_risks', {'limit': 5})

    assert calls == [(OWNER, 5)]
    assert 'tenant_id' not in result and 'user_id' not in result
    assert result['findings'][0]['rule_id'] == 'task_overdue'
    assert result['read_only'] is True
    assert events[0]['entity_id'] == 'get_company_risks'
    assert events[0]['payload'] == {'tool': 'get_company_risks', 'read_only': True}


class _Request:
    def __init__(self, *, origin='https://crm.pro.az', content_type='application/json', data=None):
        self.headers = {'Origin': origin}
        self.content_type = content_type
        self.data = data or {}

    async def json(self):
        return self.data


@pytest.mark.asyncio
async def test_api_checks_origin_and_session_identity_before_tool_call(monkeypatch):
    responses = []
    monkeypatch.setattr(api.web, 'json_response', lambda payload, status=200, headers=None: (
        responses.append((payload, status, headers)) or responses[-1]))
    invoked = []
    monkeypatch.setattr(api, 'execute', lambda *args: invoked.append(args) or {'read_only': True})
    member = lambda request: OWNER
    handle = api.handler(member, 'https://crm.pro.az')
    body = {'expected_tenant_id': TENANT, 'expected_user_id': 7,
            'tool': 'list_analytics_sources', 'arguments': {}}

    result = await handle(_Request(origin='https://evil.example', data=body))
    assert result[1] == 403
    assert not invoked

    result = await handle(_Request(data={**body, 'expected_user_id': 8}))
    assert result[1] == 409
    assert not invoked

    result = await handle(_Request(data=body))
    assert result[1] == 200
    assert invoked == [(OWNER, 'list_analytics_sources', {})]
    assert result[2] == {'Cache-Control': 'no-store'}
