"""Controlled PostgreSQL/WASM comparison with the frozen 9bf0948 list path.

Synthetic tenants only. Statement counts are not production latency claims.
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import uuid

import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_tenant_operator_workspace import operator_db, employee
import tenant_operator_dialogs as dialogs
import tenant_ai_group_coordination as coordination
from tenant_linear_policy import TenantLinearError

_spec = importlib.util.spec_from_file_location('operator_list_baseline', Path(__file__).parent / 'fixtures/operator_list_baseline.py')
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)


def normalize(value):
    if isinstance(value, dict):
        # SQL rank is an implementation detail; source selections are covered
        # separately below and in the lifecycle group-card regression suite.
        return {key: normalize(item) for key, item in value.items() if key not in {'logical_rank', 'group_sources'}}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str) and len(value) > 19 and value[4] == '-' and value[10] == 'T':
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return value


@pytest.fixture
def fixture(operator_db, monkeypatch):
    monkeypatch.setattr(baseline, '_connect', lambda: operator_db)
    monkeypatch.setattr(baseline, 'runtime_flag', lambda _key: True)
    operator_db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled,auto_send_enabled) VALUES(%s::uuid,TRUE,TRUE)", (A,))
    operator_db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected')", (A,))
    operator_db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,mode,scopes) VALUES(%s::uuid,%s::uuid,'Primary',11,TRUE,'manual',%s::jsonb)",
        (A, AGENT, json.dumps([{'provider': 'connect', 'channel_id': CA, 'allow_group_messages': True}])))
    operator_db.commit()
    return operator_db


def add_rows(db, count):
    db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,
        contact_name,last_message_at) SELECT %s::uuid,%s::uuid,'fixture-'||n,1000+n,'Synthetic '||n,
        '2026-10-01T00:00:00Z'::timestamptz+n*interval '1 minute' FROM generate_series(1,%s) n''', (A, CA, count))
    db.commit()


def measured(db, function, **kwargs):
    statements = []
    execute = db.execute
    def capture(sql, params=()):
        statements.append(sql)
        return execute(sql, params)
    db.execute = capture
    try:
        return normalize(function(PROFILE, **kwargs)), statements
    finally:
        db.execute = execute


def assert_equal(db, **kwargs):
    old, old_sql = measured(db, baseline.listing, **kwargs)
    new, new_sql = measured(db, dialogs.listing, **kwargs)
    # The frozen 9bf0948 oracle predates explicit AI-management admission.
    # This owner fixture must retain access; compare all pre-existing fields.
    assert new.get('ai_management_restricted') is False
    assert {key:value for key,value in new.items() if key != 'ai_management_restricted'} == old
    return new, old_sql, new_sql


def test_projection_once_and_constant_enrichment_round_trips(fixture):
    small, old_small, new_small = assert_equal(fixture)
    add_rows(fixture, 120)
    large, old_large, new_large = assert_equal(fixture)
    assert large['total_count'] == 121 and len(large['dialogs']) == 50 and large['next_offset'] == 50
    assert sum('WITH routes AS (' in sql for sql in old_large) == 4
    assert sum('WITH routes AS (' in sql for sql in new_large) == 1
    assert len(new_large) == len(new_small), 'no per-route enrichment statements'
    assert len(old_large) > len(old_small) + 120
    print(f'CONTROLLED direct routes 1→121: baseline statements {len(old_small)}→{len(old_large)}, optimized {len(new_small)}→{len(new_large)}; projections4→1')
    assert_equal(fixture, offset=50)
    assert_equal(fixture, offset=100)
    assert_equal(fixture, offset=10000)


@pytest.mark.parametrize('filters', [
    {'search': 'synthetic 1'}, {'channel': 'connect:' + CA}, {'channel': 'connect:' + CB},
    {'agent': 'unassigned'}, {'agent': AGENT}, {'state': 'needs_assignment'},
    {'state': 'no_agent'}, {'state': 'manual'}, {'state': 'attention'},
    {'search': 'NOT PRESENT'}, {'search': 'synthetic', 'offset': 50},
])
def test_filters_and_global_counts_match_before_pagination(fixture, filters):
    add_rows(fixture, 65)
    assert_equal(fixture, **filters)


def test_readmark_unread_and_other_tenant_scope_unchanged(fixture):
    initial, _, _ = assert_equal(fixture)
    assert initial['unread_count'] == 0
    message = str(uuid.uuid4())
    fixture.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,created_at) VALUES(%s::uuid,%s::uuid,41,'later',%s,'incoming','Synthetic later',clock_timestamp()+interval '1 second')", (message, A, 'connect:' + CA))
    fixture.commit()
    unread, _, _ = assert_equal(fixture)
    assert unread['unread_count'] == 1
    assert all(row['channel_id'] != CB for row in unread['dialogs'])
    # Global unread count does not change when filters hide the unread route.
    filtered, _, _ = assert_equal(fixture, search='not present')
    assert filtered['unread_count'] == 1 and filtered['total_count'] == 0
    dialogs.mark_read(PROFILE, {'provider': 'connect', 'lead_id': 41, 'channel_id': CA, 'conversation_id': 'client', 'message_id': message})
    read, _, _ = assert_equal(fixture)
    assert read['unread_count'] == 0


def test_jobs_conflicts_and_revoked_membership_preserve_eligibility(fixture):
    fixture.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid", (json.dumps({'conversationIdentityConflict': True, 'conversationId': 'client'}), A))
    fixture.commit()
    result, _, _ = assert_equal(fixture)
    assert result['dialogs'][0]['agent_assignment']['reason'] == 'conversation_identity_conflict'
    fixture.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11", (A,));fixture.commit()
    for listing in (baseline.listing, dialogs.listing):
        with pytest.raises(TenantLinearError) as caught:
            listing(PROFILE)
        assert caught.value.status == 403


def test_group_fold_selection_assignee_and_channel_state_match(fixture):
    channel2 = str(uuid.UUID(int=203))
    fixture.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')", (A, channel2))
    employee(fixture, ['dialogs'], actor=33)
    groups = ['120363000001@g.us', '120363000002@g.us', '120363000003@g.us']
    for i, conversation in enumerate(groups):
        for j, channel in enumerate((CA, channel2)):
            fixture.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind,display_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,%s,'group',%s,'2026-10-02T00:00:00Z'::timestamptz+%s*interval '1 minute')", (A, channel, conversation, 3000+i*2+j, 'Group ' + str(i), j))
        assignments = [{'agent_id': AGENT, 'channel_id': CA}] if i < 2 else []
        fixture.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,assignments,assigned_to) VALUES(%s::uuid,%s,%s,%s::jsonb,33)", (A, conversation, 'active' if i == 0 else 'paused', json.dumps(assignments)))
    fixture.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason) VALUES(%s::uuid,'connect',%s,%s,'human','employee')", (A, CA, groups[0]))
    fixture.commit()
    result, old, new = assert_equal(fixture)
    group_rows = [row for row in result['dialogs'] if row['customer_kind'] == 'group']
    assert len(group_rows) == 3, 'one logical group across two physical channels'
    raw_groups = [row for row in dialogs.listing(PROFILE)['dialogs'] if row['customer_kind'] == 'group']
    assert all({source['channel_id'] for source in row['group_sources']} == {CA, channel2} for row in raw_groups)
    assert sum('FROM saas_dialog_rollout' in sql for sql in new)==3, 'legacy groups must not recheck rollout per row'
    assert all(row['assignee_name'] == 'Operator' for row in group_rows)
    assert_equal(fixture, state='manual')
    assert_equal(fixture, channel='connect:' + channel2)
    assert_equal(fixture, agent=AGENT)
    assert len(new) < len(old)
    print(f'CONTROLLED 3 logical groups + direct route: baseline {len(old)} statements, optimized {len(new)}')


@pytest.mark.parametrize('resource', ['dialogs', 'agents'])
@pytest.mark.parametrize('headers', [
    {'X-Tenant-Id': B, 'X-User-Id': '11'},
    {'X-Tenant-Id': A, 'X-User-Id': '22'},
    {'X-Tenant-Id': A},
])
def test_optional_get_identity_rejects_silent_cookie_switch_before_read(fixture, monkeypatch, resource, headers):
    import asyncio
    from unittest.mock import Mock
    from aiohttp.test_utils import make_mocked_request
    import tenant_operator_api as operator_api
    import tenant_ai_agents_api as agents_api
    api = operator_api if resource == 'dialogs' else agents_api
    read_name = 'listing' if resource == 'dialogs' else 'list_agents'
    read = Mock(side_effect=AssertionError('mismatched read must not run'))
    monkeypatch.setattr(api, read_name, read)
    handler = api.handler(lambda _request: PROFILE, 'https://fixture.test', Mock(), resource)
    result = asyncio.run(handler(make_mocked_request('GET', '/api/platform/ai/' + resource, headers=headers)))
    assert result.status == 409
    read.assert_not_called()


@pytest.mark.parametrize('resource', ['dialogs', 'agents'])
@pytest.mark.parametrize('scoped', [False, True])
def test_optional_get_identity_is_backward_compatible_but_never_grants_access(fixture, resource, scoped):
    import asyncio
    from unittest.mock import Mock
    from aiohttp.test_utils import make_mocked_request
    import tenant_operator_api as operator_api
    import tenant_ai_agents_api as agents_api
    api = operator_api if resource == 'dialogs' else agents_api
    handler = api.handler(lambda _request: PROFILE, 'https://fixture.test', Mock(), resource)
    headers = {'X-Tenant-Id': A, 'X-User-Id': '11'} if scoped else {}
    def request():
        return asyncio.run(handler(make_mocked_request('GET', '/api/platform/ai/' + resource, headers=headers)))
    assert request().status == 200
    fixture.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11", (A,));fixture.commit()
    assert request().status == 403, 'matching headers are not authorization'


def test_active_lifecycle_uses_one_batch_workflow_snapshot(fixture):
    from tenant_dialog_lifecycle import migrate
    from tenant_dialog_rollout import transition
    with fixture.cursor() as cur:
        migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    fixture.commit()
    before,before_sql=measured(fixture,dialogs.listing)
    add_rows(fixture,80)
    after,after_sql=measured(fixture,dialogs.listing)
    assert len(after['dialogs'])>len(before['dialogs'])
    assert all(row['lifecycle']['state']=='waiting' for row in after['dialogs'])
    assert all(row['can_send'] is False for row in after['dialogs'])
    def workflow_reads(statements):
        return [sql for sql in statements if sql.startswith('SELECT * FROM saas_dialog_workflows WHERE tenant_id=')]
    assert len(workflow_reads(before_sql))==len(workflow_reads(after_sql))==1
    assert len(before_sql)==len(after_sql), 'workflow projection must not add per-route SQL'


def test_active_group_list_batches_guards_and_retains_capabilities(fixture, monkeypatch):
    from tenant_dialog_lifecycle import migrate
    from tenant_dialog_rollout import transition
    import tenant_dialog_access as access
    with fixture.cursor() as cur:
        migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    fixture.commit()
    def add_groups(start, end):
        for n in range(start, end):
            fixture.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,
              conversation_id,lead_id,conversation_kind,contact_name,last_message_at)
              VALUES(%s::uuid,%s::uuid,%s,%s,'group','Synthetic group',now())''',
              (A, CA, str(120363001000+n)+'@g.us', 9000+n))
        fixture.commit()
    add_groups(0, 1)
    small, small_sql = measured(fixture, dialogs.listing)
    add_groups(1, 61)
    optimized, optimized_sql = measured(fixture, dialogs.listing)
    assert optimized['total_count'] == 62
    assert len(optimized_sql) == len(small_sql), 'guard round trips must not grow per group'
    old_apply = access.apply_capabilities
    def legacy_apply(cur, tenant, policy, dialog, **_kwargs):
        return old_apply(cur, tenant, policy, dialog)
    monkeypatch.setattr(access, 'apply_capabilities', legacy_apply)
    legacy, legacy_sql = measured(fixture, dialogs.listing)
    assert optimized == legacy
    assert len(legacy_sql) > len(optimized_sql) + 600
    print(f'ACTIVE GROUP GUARDS 1→61: statements {len(small_sql)}→{len(optimized_sql)}, legacy {len(legacy_sql)}')
