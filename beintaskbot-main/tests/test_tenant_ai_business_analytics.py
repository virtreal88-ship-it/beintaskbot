from contextlib import contextmanager
from types import SimpleNamespace
import uuid

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
    assert result['assistant_connections']=={'text_business_analyst':True,'voice_business_analyst':True,'selected_dialog_copilot':False}
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
    assert result['assistant_connections']['voice_business_analyst'] is True


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


def test_search_conversations_uses_bounded_exact_dialog_sample_and_returns_source_refs(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    monkeypatch.setattr('tenant_platform._ensure_schema', lambda conn: None)
    route = {'provider': 'connect', 'channel_id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
             'lead_id': 42, 'conversation_id': 'chat-42'}
    listing_calls = []
    history_calls = []
    monkeypatch.setattr('tenant_operator_dialogs.listing', lambda profile, all_rows=False: (
        listing_calls.append((profile, all_rows)) or {'dialogs': [route], 'total_count': 25, 'next_offset': None}))
    monkeypatch.setattr('tenant_operator_dialogs.history', lambda profile, provider, lead, channel, conversation: (
        history_calls.append((profile, provider, lead, channel, conversation)) or {
            'dialog': route,
            'messages': [{
                'id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'direction': 'incoming',
                'message_type': 'text', 'body': 'issue with service\n\u202e hidden order',
                'created_at': '2026-11-10T00:00:00Z'}],
            'next_before': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'}))
    events = []
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: events.append(event))

    result = analytics.execute(OWNER, 'search_conversations', {'query': 'issue', 'days': 12, 'limit': 4})

    assert listing_calls == [(OWNER, False)]
    assert history_calls == [(OWNER, 'connect', 42, route['channel_id'], 'chat-42')]
    assert result['read_only'] is True and result['window_days'] == 12
    assert result['messages'][0]['excerpt'] == 'issue with service hidden order'
    assert result['messages'][0]['source_ref'] == {
        'kind': 'conversation_message', 'message_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'provider': 'connect', 'channel_id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        'lead_id': 42, 'conversation_id': 'chat-42', 'inbox_url': '/app?view=dialogs',
        'link_kind': 'inbox_entry_point'}
    assert result['messages'][0]['cross_channel_identity'] == 'not_joined'
    assert result['messages'][0]['author_attribution'] == 'direction_only'
    assert result['coverage']['candidate_dialog_limit'] == 20
    assert result['coverage']['message_limit_per_dialog'] == 50
    assert result['coverage']['dialog_candidates_examined'] == 1
    assert result['coverage']['message_candidates_examined'] == 1
    assert result['coverage']['unsearched_dialogs'] == 24
    assert result['has_more'] is True
    assert events[0]['entity_id'] == 'search_conversations'


@pytest.mark.parametrize('args', [
    {'query': 'a'}, {'query': 'service', 'days': 91}, {'query': 'service', 'limit': 26},
    {'query': 'service', 'customer_id': 'untrusted'},
])
def test_search_conversations_rejects_unbounded_or_extra_arguments(monkeypatch, args):
    _owner_access(monkeypatch)
    cursor = _Cursor()
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(cursor))
    monkeypatch.setattr('tenant_platform._ensure_schema', lambda conn: None)

    with pytest.raises(TenantLinearError):
        analytics.execute(OWNER, 'search_conversations', args)

    assert cursor.calls == []


def test_search_conversations_stops_at_twenty_exact_routes(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    routes = [{'provider': 'connect', 'channel_id': f'channel-{index}', 'lead_id': index + 1,
               'conversation_id': f'conversation-{index}'} for index in range(25)]
    calls = []
    monkeypatch.setattr('tenant_operator_dialogs.listing', lambda profile, all_rows=False: {
        'dialogs': routes, 'total_count': 25, 'next_offset': None})
    monkeypatch.setattr('tenant_operator_dialogs.history', lambda profile, provider, lead, channel, conversation: (
        calls.append((provider, lead, channel, conversation)) or {'dialog': {
            'provider': provider, 'lead_id': lead, 'channel_id': channel, 'conversation_id': conversation},
            'messages': [], 'next_before': None}))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_conversations', {'query': 'support'})

    assert len(calls) == 20
    assert result['coverage']['dialog_candidates_examined'] == 20
    assert result['coverage']['unsearched_dialogs'] == 5
    assert result['has_more'] is True


def test_search_coverage_counts_only_work_completed_before_limit_stop(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    routes = [{'provider': 'connect', 'channel_id': f'channel-{index}', 'lead_id': index + 1,
               'conversation_id': f'conversation-{index}'} for index in range(25)]
    calls = []
    monkeypatch.setattr('tenant_operator_dialogs.listing', lambda profile, all_rows=False: {
        'dialogs': routes, 'total_count': 25, 'next_offset': None})
    monkeypatch.setattr('tenant_operator_dialogs.history', lambda profile, provider, lead, channel, conversation: (
        calls.append(lead) or {'dialog': {'provider': provider, 'lead_id': lead, 'channel_id': channel,
            'conversation_id': conversation}, 'messages': [{'id': f'message-{lead}', 'direction': 'incoming',
            'message_type': 'text', 'body': 'service issue', 'created_at': '2026-11-10T00:00:00Z'}],
            'next_before': None}))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_conversations', {'query': 'service', 'limit': 2})

    assert calls == [1, 2, 3]
    assert len(result['messages']) == 2
    assert result['coverage']['dialog_candidates_examined'] == 3
    assert result['coverage']['message_candidates_examined'] == 3
    assert result['coverage']['unsearched_dialogs'] == 22
    assert result['has_more'] is True


def test_search_marks_incomplete_coverage_when_a_route_is_unavailable(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    route = {'provider': 'connect', 'channel_id': 'channel-1', 'lead_id': 1,
             'conversation_id': 'conversation-1'}
    monkeypatch.setattr('tenant_operator_dialogs.listing', lambda profile, all_rows=False: {
        'dialogs': [route], 'total_count': 1, 'next_offset': None})

    def unavailable(*args, **kwargs):
        raise TenantLinearError('Dialog unavailable.', 404)

    monkeypatch.setattr('tenant_operator_dialogs.history', unavailable)
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_conversations', {'query': 'service'})

    assert result['coverage']['dialog_candidates_examined'] == 1
    assert result['coverage']['unavailable_routes'] == 1
    assert result['has_more'] is True


def test_conversation_timeline_reuses_exact_history_and_drops_transport_fields(monkeypatch):
    route = {'provider': 'connect', 'lead_id': 42, 'channel_id': 'channel-1', 'conversation_id': 'chat-1'}
    calls = []
    monkeypatch.setattr('tenant_operator_dialogs.history', lambda profile, provider, lead, channel, conversation, before=None: (
        calls.append((profile, provider, lead, channel, conversation, before)) or {
            'dialog': {'channel_id': channel, 'lead_id': lead, 'conversation_id': conversation,
                       'customer_name': 'Customer', 'identity_conflict': False},
            'messages': [
                {'id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'direction': 'incoming',
                 'message_type': 'text', 'body': 'hello\u202e there', 'created_at': '2026-11-10T12:00:00Z',
                 'raw': {'private': 'secret'}, 'media_url': 'https://private.invalid/file'},
                {'id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'direction': 'outgoing',
                 'message_type': 'audio', 'body': '', 'audio_transcript': 'hello back',
                 'happened_at': '2026-11-10T12:01:00Z'},
            ], 'next_before': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'}))
    monkeypatch.setattr(analytics, '_authorized_owner', lambda profile: (TENANT, 7))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'get_conversation_timeline', {**route, 'before': None, 'limit': 1})

    assert calls == [(OWNER, 'connect', 42, 'channel-1', 'chat-1', None)]
    assert result['conversation']['display_label'] == 'Customer'
    assert result['conversation']['display_label_untrusted'] is True
    assert result['conversation']['link_kind'] == 'inbox_entry_point'
    assert result['has_more'] is True
    assert result['next_before'] == 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    assert len(result['messages']) == 1
    assert result['messages'][0]['text'] == ''
    assert result['messages'][0]['transcript'] == 'hello back'
    assert result['messages'][0]['source_ref']['inbox_url'] == '/app?view=dialogs'
    assert result['messages'][0]['source_ref']['link_kind'] == 'inbox_entry_point'
    assert 'raw' not in result['messages'][0] and 'media_url' not in result['messages'][0]


def test_conversation_timeline_discards_read_if_owner_role_changes(monkeypatch):
    calls = []
    monkeypatch.setattr('tenant_operator_dialogs.history', lambda *args, **kwargs: (
        calls.append('history') or {'dialog': {}, 'messages': [{'id': 'x', 'body': 'not returned'}]}))
    auth_calls = []

    def authorize(profile):
        auth_calls.append(True)
        if len(auth_calls) == 1:
            return TENANT, 7
        raise TenantLinearError('Company owner access required.', 403)

    monkeypatch.setattr(analytics, '_authorized_owner', authorize)

    with pytest.raises(TenantLinearError) as error:
        analytics.execute(OWNER, 'get_conversation_timeline', {
            'provider': 'kommo', 'lead_id': 1, 'channel_id': 'kommo:1'})

    assert error.value.status == 403
    assert calls == ['history']
    assert len(auth_calls) == 2


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


def test_support_case_search_uses_bounded_existing_reader_and_marks_evidence(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    calls = []
    candidate = {'id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'title': 'Login fails',
        'status': 'knowledge_published', 'result_confirmed': True,
        'outcome': {'status': 'confirmed', 'text': 'Customer confirmed repair'},
        'cause': {'status': 'suspected', 'text': 'Possible expired token'},
        'case_kind': 'technical', 'symptoms': ['Login fails'],
        'source_provenance': 'customer_message_present',
        'source_message_ids': ['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']}
    monkeypatch.setattr('tenant_knowledge_candidates_store.listing', lambda profile, args: (
        calls.append((profile, args)) or {'candidates': [candidate], 'next_before': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'}))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_support_cases', {'query': 'login', 'status': 'knowledge_published'})

    assert calls == [(OWNER, {'status': 'knowledge_published', 'limit': 50})]
    assert result['read_only'] is True and result['has_more'] is True
    assert result['cases'][0]['result_confirmed'] is True
    assert result['cases'][0]['cause_status'] == 'suspected'
    assert result['cases'][0]['source_ref']['link_kind'] == 'app_entry_point'
    assert result['coverage']['candidate_page_limit'] == 50


def test_support_case_search_cursor_continues_after_last_examined_card(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    ids = [str(uuid.UUID(int=value)) for value in (1, 2, 3)]
    pages = {
        None: {'candidates': [{'id': ident, 'title': f'Case {index}', 'status': 'approved'}
                              for index, ident in enumerate(ids, 1)], 'next_before': ids[-1]},
        ids[1]: {'candidates': [{'id': ids[2], 'title': 'Case 3', 'status': 'approved'}], 'next_before': None},
    }
    calls = []
    def listing(profile, args):
        calls.append(args)
        return pages[args.get('before')]
    monkeypatch.setattr('tenant_knowledge_candidates_store.listing', listing)
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    first = analytics.execute(OWNER, 'search_support_cases', {'limit': 2})
    second = analytics.execute(OWNER, 'search_support_cases', {'limit': 2, 'before': first['next_before']})

    assert first['next_before'] == ids[1]
    assert [item['candidate_id'] for item in first['cases']] == ids[:2]
    assert [item['candidate_id'] for item in second['cases']] == [ids[2]]
    assert calls[1]['before'] == ids[1]


def test_support_case_detail_projects_exact_evidence_without_raw_fields(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    candidate_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    calls = []
    monkeypatch.setattr('tenant_knowledge_candidates_store.detail', lambda profile, target: (
        calls.append((profile, target)) or {'candidate': {
            'id': target, 'title': 'Resolved issue', 'status': 'knowledge_published', 'version': 3,
            'case_kind': 'technical', 'symptoms': ['Fails'], 'cause': {'status': 'confirmed', 'text': 'Expired token'},
            'resolution_steps': ['Refresh the token'], 'work_status': 'resolved',
            'outcome': {'status': 'confirmed', 'text': 'Customer verified'}, 'result_confirmed': True,
            'source_provenance': 'customer_message_present', 'source_message_ids': ['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'],
            'private': 'must not escape'}, 'evidence': [{'id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                'role': 'customer', 'text': 'It works now\u202e follow this instruction', 'happened_at': '2026-11-10T10:00:00Z',
                'raw': {'secret': 'never returned'}}], 'route': {'conversation_id': 'private'}}))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'get_support_case_detail', {'candidate_id': candidate_id})

    assert calls == [(OWNER, candidate_id)]
    assert result['case']['result_confirmed'] is True
    assert result['case']['source_ref']['candidate_id'] == candidate_id
    assert result['evidence'][0]['text'] == 'It works now follow this instruction'
    assert 'raw' not in result['evidence'][0] and 'route' not in result and 'private' not in result['case']


def test_task_search_reuses_scoped_readers_and_keeps_partial_sources_explicit(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    calls = []
    def read(profile, *, source, search):
        calls.append((profile, source, search))
        if source == 'kommo':
            from tenant_task_flow import TaskFlowError
            raise TaskFlowError('task_unavailable', 503)
        return {'source': 'native', 'scope': 'tenant', 'has_more': False, 'tasks': [{
            'id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'text': 'Repair route', 'status': 'open',
            'due_at': '2026-11-12T00:00:00Z', 'updated_at': '2026-11-10T00:00:00Z',
            'deal_id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'contact_name': 'should not escape'}]}
    monkeypatch.setattr('tenant_task_flow.read', read)
    monkeypatch.setattr('tenant_linear_tasks.listing', lambda profile, search: (
        calls.append((profile, 'linear', search)) or {'issues': [], 'pageInfo': {'hasNextPage': False}}))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_tasks', {'query': 'route', 'source': 'all'})

    assert calls == [(OWNER, 'native', 'route'), (OWNER, 'kommo', 'route'), (OWNER, 'linear', 'route')]
    assert result['tasks'][0]['title'] == 'Repair route'
    assert 'contact_name' not in result['tasks'][0]
    assert result['sources']['kommo']['available'] is False
    assert result['sources']['native']['freshness'].startswith('record_timestamp_only')
    assert result['partial'] is True


@pytest.mark.parametrize('source', ['native', 'linear'])
def test_task_search_marks_same_page_limit_truncation_as_partial(monkeypatch, source):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    task_rows = [
        {'id': f'{source}-1', 'text': 'matching task one', 'status': 'open'},
        {'id': f'{source}-2', 'text': 'matching task two', 'status': 'open'},
    ]
    if source == 'native':
        from tenant_task_flow import read
        def task_read(profile, *, source, search):
            assert (source, search) == ('native', 'matching')
            return {'source': 'native', 'scope': 'tenant', 'has_more': False, 'tasks': task_rows}
        monkeypatch.setattr('tenant_task_flow.read', task_read)
    else:
        monkeypatch.setattr('tenant_linear_tasks.listing', lambda profile, search: {
            'issues': [{'id': row['id'], 'title': row['text'], 'state': {'name': row['status']}} for row in task_rows],
            'pageInfo': {'hasNextPage': False}})
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_tasks', {'query': 'matching', 'source': source, 'limit': 1})

    assert len(result['tasks']) == 1
    assert result['sources'][source]['has_more'] is True
    assert result['partial'] is True


def test_task_timeline_is_honest_when_only_snapshot_exists(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    task_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    calls = []
    monkeypatch.setattr('tenant_task_flow.read', lambda profile, *, source, task_id: (
        calls.append((profile, source, task_id)) or {'task': {'id': task_id, 'text': 'Fix bug',
            'status': 'open', 'updated_at': '2026-11-10T00:00:00Z'}}))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'get_task_timeline', {'task_id': task_id, 'source': 'native'})

    assert calls == [(OWNER, 'native', task_id)]
    assert result['timeline'] == []
    assert result['history_available'] is False
    assert 'current snapshot' in result['notice']
    assert result['task']['source_ref']['kind'] == 'crm_task'


def test_linear_task_timeline_uses_exact_scoped_read_and_does_not_claim_history(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    calls = []
    issue_id = 'LIN-42'
    monkeypatch.setattr('tenant_linear_context.context', lambda cur, profile: (
        profile, {'team_id': 'team-1'}, 'company-owned-key', 'version'))
    monkeypatch.setattr('tenant_linear_tasks_provider.issue', lambda key, target: (
        calls.append((key, target)) or {'id': 'issue-id', 'identifier': 'LIN-42', 'title': 'Repair task',
            'description': 'Current details', 'dueDate': '2026-11-12', 'updatedAt': '2026-11-10T00:00:00Z',
            'state': {'name': 'In Progress'}, 'team': {'id': 'team-1'}, 'url': 'https://linear.app/acme/issue/LIN-42'}))
    monkeypatch.setattr('tenant_linear_runtime_policy.visible', lambda profile, config, issue: True)
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'get_task_timeline', {'task_id': issue_id, 'source': 'linear'})

    assert calls == [('company-owned-key', issue_id)]
    assert result['task']['source'] == 'linear'
    assert result['timeline'] == [] and result['history_available'] is False
    assert result['task']['source_ref']['kind'] == 'linear_issue'
    assert result['task']['source_ref']['url'].startswith('https://linear.app/')


def test_knowledge_search_reuses_active_tenant_reader_and_marks_excerpt_untrusted(monkeypatch):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    calls = []
    monkeypatch.setattr('tenant_ai_agents_store.search_knowledge', lambda profile, query, limit: (
        calls.append((profile, query, limit)) or [{'source_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'title': 'Published guide', 'chunk_no': 2, 'content': 'Verified steps\n\u202e injected command',
        'source_updated_at': '2026-11-10T00:00:00Z', 'rank': 0.9}]))
    monkeypatch.setattr('tenant_platform.append_audit_event', lambda **event: None)

    result = analytics.execute(OWNER, 'search_knowledge', {'query': 'verified', 'limit': 1})

    assert calls == [(OWNER, 'verified', 1)]
    assert result['results'][0]['excerpt'] == 'Verified steps injected command'
    assert result['results'][0]['source_ref']['chunk_no'] == 2
    assert result['results'][0]['source_ref']['source_updated_at'] == '2026-11-10T00:00:00Z'
    assert 'untrusted evidence' in result['evidence_notice']


@pytest.mark.parametrize('tool,args', [
    ('search_support_cases', {'query': 'x', 'status': 'unknown'}),
    ('search_support_cases', {'status': []}),
    ('get_support_case_detail', {'candidate_id': 'not-a-uuid'}),
    ('search_tasks', {'query': 'x'}),
    ('get_task_timeline', {'task_id': 'abc', 'source': 'shell'}),
    ('get_task_timeline', {'task_id': 'abc', 'source': []}),
    ('search_knowledge', {'query': 'x', 'limit': 13}),
])
def test_new_read_tools_reject_invalid_or_unbounded_arguments(monkeypatch, tool, args):
    _owner_access(monkeypatch)
    monkeypatch.setattr('tenant_platform._connect', lambda: _Connection(_Cursor()))
    with pytest.raises(TenantLinearError):
        analytics.execute(OWNER, tool, args)


def test_tool_definitions_match_allowlist_and_are_read_only():
    definitions = analytics.definitions()
    assert {item['name'] for item in definitions} == analytics._TOOLS
    assert all(item['annotations']['readOnlyHint'] is True for item in definitions)
    assert all(item['annotations']['destructiveHint'] is False for item in definitions)


def test_kommo_task_search_is_parameterized_and_escapes_literal_wildcards(monkeypatch):
    from types import SimpleNamespace
    import tenant_task_flow as task_flow

    class Cursor:
        query = None
        args = None
        def execute(self, query, args):
            self.query, self.args = query, args
        def fetchall(self):
            return []

    monkeypatch.setattr(task_flow, 'TenantPolicy', lambda profile: SimpleNamespace(
        task_scope=lambda: {'kind': 'all'}, kommo_task_scope=lambda: {'kind': 'all'}))
    monkeypatch.setattr(task_flow, 'task_filters', lambda tenant_id, scope: ('tenant_id=%s::uuid', [tenant_id]))
    cursor = Cursor()
    search = r'literal % _ \\ tail'

    task_flow._rows(cursor, OWNER, 'kommo', limit=201, search=search)

    assert 'ILIKE %s ESCAPE' in cursor.query
    assert search not in cursor.query
    assert cursor.args[0] == TENANT
    assert cursor.args[-2] == r'%literal \% \_ \\\\ tail%'
