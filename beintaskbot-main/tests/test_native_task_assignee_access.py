"""EMP02: real native HTTP, current membership/policy and PostgreSQL row locks."""
import asyncio
import json
import logging
import threading
import time
import uuid
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
from psycopg.rows import dict_row
import pytest
import requests

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, CA
import tenant_crm_native_task_api as api
import tenant_crm_native_tasks as tasks
import tenant_crm_native_deals as native_deals
import tenant_member_store as members
import tenant_workflow_store as workflow
from tenant_policy import TenantPolicy


ORIGIN = 'https://native-assignee.example'
LOGGER = logging.getLogger(__name__)


@pytest.fixture
def native(db, monkeypatch):
    factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    for module in (tasks, members, native_deals, workflow):
        monkeypatch.setattr(module, '_connect', factory)
    forbidden = Mock(side_effect=AssertionError('Native task assignment must not contact an external provider'))
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    db.execute('UPDATE saas_tenants SET modules=%s::jsonb WHERE id=%s::uuid',
               (json.dumps({'tasks': True, 'deals': True}), A))
    people = [
        (31, 'admin', ['tasks'], True),
        (32, 'admin', ['tasks', 'tasks:view', 'tasks:create', 'tasks:update'], True),
        (34, 'manager', ['tasks', 'deals'], True), (35, 'operator', ['tasks'], True),
        (41, 'operator', ['tasks'], True),
        (42, 'operator', ['tasks', 'tasks:view', 'tasks:update'], True),
        (43, 'operator', ['tasks', 'tasks:view'], True),
        (44, 'operator', ['tasks', 'tasks:create'], True),
        (45, 'operator', ['tasks', 'tasks:update'], True), (46, 'operator', [], True),
        (47, 'operator', ['tasks:unknown'], True), (48, 'master', ['tasks'], True),
        (49, 'operator', ['tasks'], False), (50, 'operator', ['tasks:view', 'tasks:update'], True),
    ]
    for actor, role, permissions, active in people:
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,active) VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s)",
                   (A, actor, 'Synthetic member ' + str(actor), role, json.dumps(permissions), active))
    for tenant in (A, B):
        for pipeline, stage, owner in (('1', '10', 34 if tenant == A else 22), ('2', '20', 11 if tenant == A else 22)):
            db.execute('INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,owner_telegram_id) VALUES(%s::uuid,%s,%s,%s)',
                       (tenant, pipeline, 'Synthetic pipeline ' + pipeline, owner))
            db.execute('INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,%s,%s,%s)',
                       (tenant, pipeline, stage, 'Synthetic stage ' + stage))
    db.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name,settings) VALUES(%s::uuid,'1','11','Hidden stage','{\"visible\":false}'::jsonb)", (A,))
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'modules',%s::jsonb,11)",
               (A, json.dumps({'tasks': True, 'deals': True})))
    contact = str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s::uuid,%s::uuid,'Synthetic task customer')", (contact, A))
    deals = [str(uuid.uuid4()) for _ in range(5)]
    for ident, tenant, pipeline, stage, channel, conversation in (
        (deals[0], A, '1', '10', CA, 'client'), (deals[1], A, '2', '20', None, ''),
        (deals[2], A, '1', '11', None, ''), (deals[3], B, '1', '10', None, ''),
        (deals[4], A, '1', '10', None, ''),
    ):
        db.execute('''INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,
          responsible_id,contact_id,channel_id,conversation_id) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s::uuid,%s::uuid,%s)''',
          (ident, tenant, 'Synthetic task deal', pipeline, stage, 35 if ident == deals[4] else 34 if tenant == A else 22,
           contact if tenant == A else None, channel, conversation))
    db.commit()
    yield {'db': db, 'factory': factory, 'deals': deals, 'contact': contact}
    forbidden.assert_not_called()


def _app(*, cached=None):
    def authenticated(request):
        actor = int(request.headers.get('X-Synthetic-Actor', '11'))
        tenant = request.headers.get('X-Synthetic-Tenant', A)
        if cached is not None and actor == cached['telegram_id'] and tenant == cached['tenant_id']:
            return cached  # Genuine prior database snapshot, as in a stale session.
        return members.member(tenant, actor)
    app = web.Application()
    handle = api.handler(authenticated, ORIGIN, LOGGER)
    app.router.add_route('*', '/tasks', handle)
    app.router.add_route('*', '/tasks/{id}', handle)
    return app


async def _get(http, path='/tasks', *, actor=11, tenant=A, **query):
    response = await http.get(path, params=query, headers={'X-Synthetic-Actor': str(actor), 'X-Synthetic-Tenant': tenant})
    return response.status, await response.json()


async def _post(http, payload, path='/tasks', *, actor=11, tenant=A):
    response = await http.post(path, json=payload, headers={
        'Origin': ORIGIN, 'X-Synthetic-Actor': str(actor), 'X-Synthetic-Tenant': tenant})
    return response.status, await response.json()


def _command(f, target=41, *, deal=None):
    return {'deal_id': deal or f['deals'][0], 'responsible_id': target, 'text': 'Call synthetic customer SKU-42',
            'due_at': '2026-11-10T10:00:00+04:00', 'request_id': str(uuid.uuid4())}


def _no_tasks(f):
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 0


@pytest.mark.parametrize('actor,target', [(11, 11), (11, 31), (11, 41), (11, 42), (11, 50), (31, 42), (32, 41)])
def test_real_options_and_create_accept_only_current_usable_task_assignees(native, actor, target):
    f = native
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, actor=actor, options='1', deal_id=f['deals'][0])
            assert status == 200, options
            eligible = {row['telegram_id'] for row in options['members']}
            assert eligible == {11, 31, 32, 34, 35, 41, 42, 50}
            assert options['assignment']['criterion'] == 'active_tasks_view_update'
            assert options['selected_deal_id'] == f['deals'][0]
            status, created = await _post(http, _command(f, target), actor=actor)
            assert status == 200, created
            row = created['task']
            assert row['responsible_id'] == target and row['created_by'] == actor
            assert row['deal_id'] == f['deals'][0] and row['contact_id'] == f['contact']
            assert row['channel_id'] == CA and row['conversation_id'] == 'client'
    asyncio.run(scenario())


@pytest.mark.parametrize('target', [43, 44, 45, 46, 47, 48, 49, 22])
def test_task_read_create_update_only_unknown_master_inactive_and_foreign_targets_are_excluded(native, target):
    f = native
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, options='1', deal_id=f['deals'][0])
            assert status == 200 and target not in {row['telegram_id'] for row in options['members']}
            status, rejected = await _post(http, _command(f, target))
            assert status == 409 and rejected['error'] == 'assignee_unavailable'
    asyncio.run(scenario())
    _no_tasks(f)


@pytest.mark.parametrize('source', ['tenant', 'workflow'])
def test_module_disabled_effective_policy_excludes_flat_grants_but_preserves_owner_override(native, source):
    f = native
    if source == 'tenant':
        f['db'].execute("DELETE FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='modules'", (A,))
        f['db'].execute("UPDATE saas_tenants SET modules='{\"tasks\":false}'::jsonb WHERE id=%s::uuid", (A,))
    else:
        f['db'].execute("UPDATE saas_tenant_workflow_policies SET value='{\"tasks\":false}'::jsonb WHERE tenant_id=%s::uuid AND policy_key='modules'", (A,))
    f['db'].commit()
    assert not TenantPolicy(members.member(A, 41)).allows('tasks')
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, options='1', deal_id=f['deals'][0])
            assert status == 200 and {row['telegram_id'] for row in options['members']} == {11,32,42,50}
            status, rejected = await _post(http, _command(f, 41))
            assert status == 409 and rejected['error'] == 'assignee_unavailable'
            status, created = await _post(http, _command(f, 11))
            assert status == 200 and created['task']['responsible_id'] == 11
    asyncio.run(scenario())


def test_options_are_not_assignment_authority_after_target_rights_are_revoked(native):
    f = native
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, options='1', deal_id=f['deals'][0])
            assert status == 200 and 41 in {row['telegram_id'] for row in options['members']}
            f['db'].execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=41',
                             (json.dumps(['tasks', 'tasks:view']), A))
            f['db'].commit()
            status, rejected = await _post(http, _command(f, 41))
            assert status == 409 and rejected['error'] == 'assignee_unavailable'
    asyncio.run(scenario())
    _no_tasks(f)


@pytest.mark.parametrize('actor', [34, 35])
def test_manager_and_operator_can_assign_only_self_and_keep_existing_native_task_identity(native, actor):
    f = native
    selected = f['deals'][4] if actor == 35 else f['deals'][0]
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, actor=actor, options='1', deal_id=selected)
            assert status == 200 and [row['telegram_id'] for row in options['members']] == [actor]
            assert options['assignment']['scope'] == 'self'
            status, refused = await _post(http, _command(f, 41, deal=selected), actor=actor)
            assert status == 403 and refused['error'] == 'forbidden_assignment'
            command = _command(f, actor, deal=selected)
            status, created = await _post(http, command, actor=actor)
            assert status == 200, created
            task_id = created['task']['id']
            status, read = await _get(http, '/tasks/' + task_id, actor=actor)
            assert status == 200 and read['task']['id'] == task_id
            assert read['task']['deal_id'] == command['deal_id']
    asyncio.run(scenario())


@pytest.mark.parametrize('deal_index', [1, 2])
def test_manager_assignee_requires_selected_deal_pipeline_and_visible_stage(native, deal_index):
    f = native
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, options='1', deal_id=f['deals'][deal_index])
            assert status == 200 and 34 not in {row['telegram_id'] for row in options['members']}
            status, rejected = await _post(http, _command(f, 34, deal=f['deals'][deal_index]))
            assert status == 409 and rejected['error'] == 'assignee_unavailable'
    asyncio.run(scenario())
    _no_tasks(f)


@pytest.mark.parametrize('actor,deal_index', [(34, 4), (35, 0)])
def test_existing_self_task_edit_retains_its_bound_deal_when_another_employee_owns_the_deal(native, actor, deal_index):
    f = native
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, created = await _post(http, _command(f, actor, deal=f['deals'][deal_index]))
            assert status == 200, created
            task_id = created['task']['id']
            status, options = await _get(http, actor=actor, options='1', task_id=task_id)
            assert status == 200 and options['selected_deal_id'] == f['deals'][deal_index]
            assert [row['telegram_id'] for row in options['members']] == [actor]
            status, updated = await _post(http, {'action': 'update', 'text': 'Existing task reviewed',
                'responsible_id': actor}, '/tasks/' + task_id, actor=actor)
            assert status == 200, updated
            assert updated['task']['id'] == task_id and updated['task']['deal_id'] == f['deals'][deal_index]
            status, rejected = await _post(http, {'action': 'update', 'text': 'Do not relink',
                'deal_id': f['deals'][1]}, '/tasks/' + task_id, actor=actor)
            assert status == 400 and rejected['success'] is False
            status, reopened = await _get(http, '/tasks/' + task_id, actor=actor)
            assert status == 200 and reopened['task']['text'] == 'Existing task reviewed'
            assert reopened['task']['deal_id'] == f['deals'][deal_index]
    asyncio.run(scenario())


def _wait_blocked(factory, holder_pid):
    deadline = time.monotonic() + 5
    with factory() as observer:
        while time.monotonic() < deadline:
            rows = observer.execute('SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))', (holder_pid,)).fetchall()
            if rows:
                return rows[0]['pid']
            observer.commit()
            time.sleep(.01)
    raise AssertionError('Real task transaction never waited for the authority row lock')


@pytest.mark.parametrize('revoked', ['actor', 'target', 'module_policy', 'stage'])
def test_waiting_real_write_rechecks_current_authority_after_locked_revocation(native, revoked):
    f = native
    cached = members.member(A, 31)
    async def scenario():
        async with TestClient(TestServer(_app(cached=cached))) as http:
            target = 34 if revoked == 'stage' else 41
            status, options = await _get(http, actor=31, options='1', deal_id=f['deals'][0])
            assert status == 200 and target in {row['telegram_id'] for row in options['members']}
            with f['factory']() as holder:
                if revoked in {'actor', 'target'}:
                    actor = 31 if revoked == 'actor' else 41
                    holder.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE', (A, actor))
                elif revoked == 'module_policy':
                    holder.execute("SELECT 1 FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='modules' FOR UPDATE", (A,))
                else:
                    # Stage rows deliberately are not task-authority locks.
                    # Delay admission on a relevant policy, commit a stage
                    # change, then require the final fresh workflow read.
                    holder.execute("SELECT 1 FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='modules' FOR UPDATE", (A,))
                pending = asyncio.create_task(_post(http, _command(f, target), actor=31))
                await asyncio.to_thread(_wait_blocked, f['factory'], holder.info.backend_pid)
                if revoked in {'actor', 'target'}:
                    holder.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=%s',
                                   (json.dumps([] if revoked == 'actor' else ['tasks', 'tasks:view']), A, actor))
                elif revoked == 'module_policy':
                    holder.execute("UPDATE saas_tenant_workflow_policies SET value='{\"tasks\":false}'::jsonb WHERE tenant_id=%s::uuid AND policy_key='modules'", (A,))
                else:
                    holder.execute("UPDATE saas_tenant_pipeline_stages SET settings='{\"visible\":false}'::jsonb WHERE tenant_id=%s::uuid AND pipeline_id='1' AND stage_id='10'", (A,))
                holder.commit()
                status, rejected = await asyncio.wait_for(pending, 10)
                assert status == (403 if revoked in {'actor', 'module_policy'} else 409), rejected
    asyncio.run(scenario())
    _no_tasks(f)


def test_admitted_assignment_holds_target_permissions_until_the_task_transaction_commits(native):
    f = native
    finished = threading.Event()
    failures = []

    def revoke():
        try:
            with f['factory']() as conn:
                conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=41',
                             (json.dumps(['tasks', 'tasks:view']), A))
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            with f['factory']() as holder:
                # The existing contact FK delays INSERT after actual assignee
                # admission. No trigger, DDL or authority callback is replaced.
                holder.execute('SELECT 1 FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (A, f['contact']))
                pending = asyncio.create_task(_post(http, _command(f)))
                sender_pid = await asyncio.to_thread(_wait_blocked, f['factory'], holder.info.backend_pid)
                thread = threading.Thread(target=revoke)
                thread.start()
                await asyncio.to_thread(_wait_blocked, f['factory'], sender_pid)
                assert not finished.is_set()
                holder.commit()
                status, created = await asyncio.wait_for(pending, 10)
                await asyncio.to_thread(thread.join, 10)
                assert not thread.is_alive() and not failures
                assert status == 200 and created['task']['responsible_id'] == 41
                status, options = await _get(http, options='1', deal_id=f['deals'][0])
                assert status == 200 and 41 not in {row['telegram_id'] for row in options['members']}
    asyncio.run(scenario())
    assert finished.is_set()
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 1


def test_uuid_retry_creates_one_task_changed_payload_conflicts_and_old_links_survive(native):
    f = native
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            command = _command(f)
            status, first = await _post(http, command)
            assert status == 200, first
            task_id = first['task']['id']
            status, replay = await _post(http, command)
            assert status == 200 and replay['task']['id'] == task_id
            status, changed = await _post(http, {**command, 'text': 'Changed same UUID'})
            assert status in {400, 409} and changed['success'] is False
            status, updated = await _post(http, {'action': 'update', 'text': 'Reviewed task', 'responsible_id': 42}, '/tasks/' + task_id)
            assert status == 200, updated
            assert updated['task']['id'] == task_id and updated['task']['deal_id'] == f['deals'][0]
            assert updated['task']['contact_id'] == f['contact']
            assert updated['task']['channel_id'] == CA and updated['task']['conversation_id'] == 'client'
            status, reopened = await _get(http, '/tasks/' + task_id)
            assert status == 200 and reopened['task']['text'] == 'Reviewed task'
            status, denied = await _get(http, '/tasks/' + task_id, actor=22, tenant=B)
            assert status == 404 and denied['success'] is False
    asyncio.run(scenario())
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 1


@pytest.mark.parametrize('target,pipeline,stage,eligible', [
    (34, '1', '10', True), (34, '2', '20', False), (49, '1', '10', False),
    (22, '1', '10', False), (43, '1', '10', False),
])
def test_actual_enabled_stage_entry_hook_checks_deal_context_and_never_duplicates_an_event(
        native, target, pipeline, stage, eligible):
    f = native
    settings = {'native_task': {'enabled': True, 'text': 'Synthetic stage followup SKU-42',
                               'responsible_id': target, 'due_days': 2}}
    f['db'].execute('UPDATE saas_tenant_pipeline_stages SET settings=%s::jsonb WHERE tenant_id=%s::uuid AND pipeline_id=%s AND stage_id=%s',
                   (json.dumps(settings), A, pipeline, stage))
    f['db'].commit()
    deal_id = str(uuid.uuid4())
    created = native_deals.create_deal(A, 'Synthetic real stage-entry deal', pipeline, stage,
                               contact_id=f['contact'], responsible_id=11, deal_id=deal_id)
    assert str(created['id']) == deal_id
    rows = f['db'].execute('SELECT id,deal_id,contact_id,responsible_id,text,due_at,created_by,idempotency_key FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid', (A,))
    assert len(rows) == int(eligible)
    if eligible:
        assert rows[0]['responsible_id'] == 34
        assert str(rows[0]['deal_id']) == deal_id and str(rows[0]['contact_id']) == f['contact']
        assert rows[0]['text'] == settings['native_task']['text'] and rows[0]['due_at'] is not None
        assert rows[0]['created_by'] is None and rows[0]['idempotency_key'].startswith('workflow:')
    # Re-enter the actual create consumer with the same durable deal/event ID.
    # Its hook executes again but must preserve the original task and due date.
    replayed = native_deals.create_deal(A, 'Synthetic real stage-entry deal', pipeline, stage,
                                contact_id=f['contact'], responsible_id=11, deal_id=deal_id)
    assert str(replayed['id']) == deal_id
    assert f['db'].execute('SELECT id,deal_id,contact_id,responsible_id,text,due_at,created_by,idempotency_key FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid', (A,)) == rows
    assert str(native_deals.get_deal(A, deal_id)['id']) == deal_id


def test_existing_granular_admin_creator_contract_does_not_make_that_actor_an_eligible_assignee(native):
    f = native
    granular = ['tasks:view', 'tasks:create']
    f['db'].execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=31',
                   (json.dumps(granular), A))
    f['db'].commit()
    policy = TenantPolicy(members.member(A, 31))
    assert not policy.allows('tasks')
    assert policy.allows_action('tasks', 'view') and policy.allows_action('tasks', 'create')
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, actor=31, options='1', deal_id=f['deals'][0])
            assert status == 200
            eligible = {row['telegram_id'] for row in options['members']}
            assert 41 in eligible and 31 not in eligible
            status, created = await _post(http, _command(f, 41), actor=31)
            assert status == 200, created
            assert created['task']['created_by'] == 31 and created['task']['responsible_id'] == 41
            status, refused = await _post(http, _command(f, 31), actor=31)
            assert status == 409 and refused['error'] == 'assignee_unavailable'
    asyncio.run(scenario())
    assert members.member(A, 31)['permissions'] == granular
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 1


@pytest.mark.parametrize('crowd', ['other_employees', 'own_allowed'])
def test_options_apply_manager_authority_before_limit_and_preserve_an_older_explicit_selection(native, crowd):
    f = native
    own = crowd == 'own_allowed'
    f['db'].execute("""INSERT INTO saas_crm_native_deals
      (id,tenant_id,title,pipeline_id,stage_id,responsible_id,updated_at)
      SELECT gen_random_uuid(),%s::uuid,%s,%s,%s,%s,clock_timestamp()+interval '1 hour'
      FROM generate_series(1,201)""",
      (A, 'Synthetic picker crowd', '1' if own else '2', '10' if own else '20', 34 if own else 11))
    allowed = {str(row['id']) for row in f['db'].execute("""SELECT id FROM saas_crm_native_deals
      WHERE tenant_id=%s::uuid AND responsible_id=34 AND pipeline_id='1' AND stage_id='10'""", (A,))}
    f['db'].commit()
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            for selection in ({}, {'deal_id': f['deals'][0]}):
                status, options = await _get(http, actor=34, options='1', **selection)
                assert status == 200, options
                assert options['assignment']['status'] == 'ready'
                assert [row['telegram_id'] for row in options['members']] == [34]
                ids = {row['id'] for row in options['deals']}
                assert ids and ids <= allowed and len(options['deals']) <= 200
                assert options['selected_deal_id'] in ids
                if selection or not own:
                    assert options['selected_deal_id'] == f['deals'][0]
                if not own:
                    assert ids == {f['deals'][0]}
    asyncio.run(scenario())
    _no_tasks(f)


def _wait_creator_terminal_or_blocked(factory, finished, writer_pid):
    deadline = time.monotonic() + 5
    with factory() as observer:
        while time.monotonic() < deadline:
            if finished.is_set():
                return
            if observer.execute('SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))', (writer_pid,)).fetchall():
                return
            observer.commit()
            time.sleep(.01)
    raise AssertionError('Creator neither completed nor reached the real workflow writer lock')


@pytest.mark.parametrize('keys', [('modules', 'members'), ('members', 'modules'), (' modules ', ' members ')])
def test_real_workflow_policy_writer_and_task_admission_have_consistent_lock_order_for_json_keys(native, keys):
    f = native
    f['db'].execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'members','{}'::jsonb,11)", (A,))
    f['db'].commit()
    values = {'modules': {'tasks': True, 'deals': True}, 'members': {'34': {'pipeline_ids': ['1']}}}
    ordered = {key: values[key.strip()] for key in keys}
    results, failures = {}, []
    creator_finished = threading.Event()
    command = _command(f)

    def writer():
        try:
            results['workflow'] = workflow.save_tenant_workflow_config(
                tenant_id=A, owner_id=11, policies=ordered)
        except Exception as error:
            failures.append(('workflow', type(error).__name__, str(error)))

    def creator():
        try:
            results['task'] = tasks.create_task(A, command['deal_id'], command['text'], command['responsible_id'],
                due_at=command['due_at'], created_by=11, idempotency_key=command['request_id'], actor_id=11)
        except Exception as error:
            failures.append(('task', type(error).__name__, str(error)))
        finally:
            creator_finished.set()

    threads = []
    with f['factory']() as holder:
        holder.execute("SELECT 1 FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='members' FOR SHARE", (A,))
        try:
            threads.append(threading.Thread(target=writer))
            threads[-1].start()
            writer_pid = _wait_blocked(f['factory'], holder.info.backend_pid)
            threads.append(threading.Thread(target=creator))
            threads[-1].start()
            # With canonical ordering the creator may legitimately finish
            # before release. The old inversion instead reaches a real lock
            # wait here and deadlocks after the temporary holder is released.
            _wait_creator_terminal_or_blocked(f['factory'], creator_finished, writer_pid)
        finally:
            holder.commit()
            for thread in threads:
                thread.join(10)
        assert all(not thread.is_alive() for thread in threads)
    assert not failures, failures
    assert 'workflow' in results and results['task']['responsible_id'] == 41
    assert results['task']['text'] == command['text']
    saved = {row['policy_key']: row['value'] for row in f['db'].execute(
        'SELECT policy_key,value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid', (A,))}
    assert saved['modules'] == values['modules'] and saved['members'] == values['members']
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 1


@pytest.mark.parametrize('first,last,initial,final', [
    (' modules ', 'modules', False, True), ('modules', ' modules ', True, False)])
def test_sorted_workflow_policy_writes_preserve_last_insertion_for_duplicate_canonical_keys(native, first, last, initial, final):
    f = native
    workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11,
        policies={first: {'tasks': initial}, last: {'tasks': final}})
    saved = f['db'].execute('SELECT policy_key,value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid', (A,))
    assert saved == [{'policy_key': 'modules', 'value': {'tasks': final}}]
    assert TenantPolicy(members.member(A, 41)).allows('tasks') is final


@pytest.mark.parametrize('order', [(41, 31), (31, 41)])
def test_actual_workflow_member_writer_and_task_admission_share_numeric_lock_order(native, order):
    f = native
    edits = {actor: {'telegram_id': actor, 'display_name': 'Updated synthetic ' + str(actor),
        'role': 'admin' if actor == 31 else 'worker', 'permissions': ['tasks']} for actor in order}
    results, failures = {}, []
    creator_finished = threading.Event()
    command = _command(f)

    def writer():
        try:
            results['workflow'] = workflow.save_tenant_workflow_config(
                tenant_id=A, owner_id=11, members=[edits[actor] for actor in order])
        except Exception as error:
            failures.append(('workflow', type(error).__name__, str(error)))

    def creator():
        try:
            results['task'] = tasks.create_task(A, command['deal_id'], command['text'], 41,
                due_at=command['due_at'], created_by=31, idempotency_key=command['request_id'], actor_id=31)
        except Exception as error:
            failures.append(('task', type(error).__name__, str(error)))
        finally:
            creator_finished.set()

    threads = []
    with f['factory']() as holder:
        holder.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=31 FOR SHARE', (A,))
        try:
            threads.append(threading.Thread(target=writer))
            threads[-1].start()
            writer_pid = _wait_blocked(f['factory'], holder.info.backend_pid)
            threads.append(threading.Thread(target=creator))
            threads[-1].start()
            _wait_creator_terminal_or_blocked(f['factory'], creator_finished, writer_pid)
        finally:
            holder.commit()
            for thread in threads:
                thread.join(10)
        assert all(not thread.is_alive() for thread in threads)
    assert not failures, failures
    assert 'workflow' in results
    assert results['task']['created_by'] == 31 and results['task']['responsible_id'] == 41
    for actor in order:
        current = members.member(A, actor)
        assert current['display_name'] == edits[actor]['display_name']
        assert current['role'] == edits[actor]['role'] and current['permissions'] == ['tasks']
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 1


@pytest.mark.parametrize('first,last,initial,final', [('041', 41, [], ['tasks']), (41, '041', ['tasks'], [])])
def test_numeric_member_alias_sort_preserves_last_update_for_the_same_employee(native, first, last, initial, final):
    workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11, members=[
        {'telegram_id': first, 'display_name': 'Earlier synthetic edit', 'role': 'worker', 'permissions': initial},
        {'telegram_id': last, 'display_name': 'Final synthetic edit', 'role': 'worker', 'permissions': final}])
    current = members.member(A, 41)
    assert current['display_name'] == 'Final synthetic edit'
    assert current['role'] == 'worker' and current['permissions'] == final
    assert native['db'].execute('SELECT count(*) AS n FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=41', (A,))[0]['n'] == 1


@pytest.mark.parametrize('revocation', ['update', 'view', 'inactive'])
def test_native_overrides_enable_assignee_and_live_revocation_blocks_write(native, revocation):
    f = native
    settings = {'native_crm_actions': {'tasks': {'view': True, 'update': True}}}
    f['db'].execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'members',%s::jsonb,11) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",
                    (A, json.dumps({'46': settings})))
    f['db'].commit()
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, options = await _get(http, options='1', deal_id=f['deals'][0])
            assert status == 200 and 46 in {row['telegram_id'] for row in options['members']}
            status, created = await _post(http, _command(f, 46))
            assert status == 200 and created['task']['responsible_id'] == 46
            assert not TenantPolicy(members.member(A,46)).allows('tasks')
            if revocation == 'update':
                settings['native_crm_actions']['tasks']['update'] = False
            elif revocation == 'view':
                settings['native_crm_actions']['tasks']['view'] = False
            else:
                f['db'].execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=46',(A,))
            f['db'].execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",(json.dumps({'46':settings}),A))
            f['db'].commit()
            status, rejected = await _post(http, _command(f,46))
            assert status == 409 and rejected['error'] == 'assignee_unavailable'
            status, options = await _get(http, options='1', deal_id=f['deals'][0])
            assert status == 200 and 46 not in {row['telegram_id'] for row in options['members']}
            status, foreign = await _post(http, _command(f,46), tenant=B, actor=22)
            assert status in (404,409)
    asyncio.run(scenario())
    assert f['db'].execute('SELECT permissions FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=46',(A,))[0]['permissions'] == []
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n'] == 1
