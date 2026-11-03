"""CRM02: native PostgreSQL, current cookies and real task command services."""
import ast
import asyncio
import copy
import importlib
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
import pytest

from test_company_employees_navigation_postgres import (
    A, B, COOKIE_NAME, ORIGIN, ROOT, LOGGER, database, db, staff, _apis, _snapshot,
)
from test_tenant_support_inbox import CA
import tenant_crm_native_task_api as native_api
import tenant_crm_native_tasks as native_tasks
import tenant_crm_store as crm
import tenant_task_api as legacy_api
import tenant_tasks as external_tasks
import tenant_task_commands as commands
import tenant_task_completion as completion
import tenant_task_approvals as approvals
import tenant_crm_sync_store as sync_store
from tenant_platform_core import TenantPlatformError
from tenant_policy import TenantPolicy


def _uuid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-crm02-' + name))


DEALS = [_uuid('deal-' + str(index)) for index in range(5)]
CONTACT = _uuid('contact-a')
DUE = '2099-11-10T10:30:00+04:00'


class ExternalTransport:
    """Only the external HTTP boundary is simulated; command/store stay real."""
    def __init__(self):
        self.calls = []
        self.tasks = {}
        self.leads = {101: {'id': 101, 'pipeline_id': 10, 'status_id': 101, 'name': 'Saved external deal'}}
        self.lose_task_response = False
        self.sequence = 9000

    async def __call__(self, tenant, method, path, **kwargs):
        self.calls.append((str(tenant), method, path, copy.deepcopy(kwargs)))
        if method == 'GET' and path == 'account': return {'current_user_id': 999}
        if method == 'GET' and path == 'leads/pipelines':
            return {'_embedded': {'pipelines': [
                {'id': 10, '_embedded': {'statuses': [{'id': 101, 'name': 'Open', 'type': 0}]}},
                {'id': 20, '_embedded': {'statuses': [{'id': 201, 'name': 'Open', 'type': 0}]}},
            ]}}
        if method == 'GET' and path.startswith('leads/'):
            return dict(self.leads.get(int(path.split('/')[1]), {}))
        if method == 'GET' and path.startswith('tasks/'):
            return dict(self.tasks.get(int(path.split('/')[1]), {}))
        if method == 'POST' and path == 'leads':
            self.sequence += 1
            row = {'id': self.sequence, **kwargs['json_body'][0]}
            self.leads[self.sequence] = row
            return {'_embedded': {'leads': [row]}}
        if method == 'POST' and path == 'tasks':
            self.sequence += 1
            row = {'id': self.sequence, 'is_completed': False, **kwargs['json_body'][0]}
            self.tasks[self.sequence] = row
            if self.lose_task_response: raise TimeoutError('Synthetic provider response lost after accepting task')
            return {'_embedded': {'tasks': [row]}}
        if method == 'PATCH' and path.startswith('tasks/'):
            ident = int(path.split('/')[1])
            self.tasks[ident] = {**self.tasks.get(ident, {'id': ident}), **kwargs.get('json_body', {})}
            return dict(self.tasks[ident])
        raise AssertionError('Unexpected external request: ' + repr((method, path)))


def _production_sync():
    """Compile the exact existing thin entry without importing bot startup."""
    tree = ast.parse((ROOT / 'bot.py').read_text(encoding='utf-8-sig'))
    node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_sync_tenant_crm')
    namespace = {'asyncio': asyncio, 'TenantPolicy': TenantPolicy, 'TenantPlatformError': TenantPlatformError,
                 'enqueue_tenant_crm_sync': sync_store.enqueue}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'bot.py'), 'exec'), namespace)
    return namespace['_sync_tenant_crm']


@pytest.fixture
def task_flow(staff, monkeypatch):
    f = staff
    modules = ('tenant_crm_native_tasks', 'tenant_crm_native_deals', 'tenant_crm_native_contacts',
               'tenant_crm_store', 'tenant_task_commands', 'tenant_tasks', 'tenant_task_completion',
               'tenant_task_approvals', 'tenant_crm_sync_store', 'tenant_task_flow')
    for name in modules:
        module = importlib.import_module(name)
        if hasattr(module, '_connect'): monkeypatch.setattr(module, '_connect', f['factory'])
    with f['factory']() as conn:
        for tenant in (A, B):
            conn.execute("UPDATE saas_tenants SET modules='{\"tasks\":true,\"deals\":true,\"customers\":true}' WHERE id=%s::uuid", (tenant,))
            for pipeline, stage, owner in (('10', '101', 82 if tenant == A else 22), ('20', '201', 11 if tenant == A else 22)):
                conn.execute('INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,owner_telegram_id) VALUES(%s::uuid,%s,%s,%s)',
                             (tenant, pipeline, 'Pipeline ' + pipeline, owner))
                conn.execute('INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,%s,%s,%s)',
                             (tenant, pipeline, stage, 'Stage ' + stage))
            conn.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at) VALUES(%s::uuid,'kommo','connected','synthetic.invalid',now())", (tenant,))
        conn.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name,settings) VALUES(%s::uuid,'10','102','Hidden stage','{\"visible\":false}')", (A,))
        for tenant, actor, role, grants, active in (
            (A, 81, 'admin', ['tasks', 'deals'], True), (A, 82, 'manager', ['tasks', 'deals'], True),
            (A, 83, 'operator', ['tasks'], True), (A, 84, 'admin', ['tasks', 'tasks:view'], True),
            (A, 85, 'admin', ['tasks'], False), (A, 86, 'admin', ['tasks:view', 'tasks:create'], True),
            (A, 87, 'admin', ['deals'], True), (A, 88, 'admin', ['tasks', 'deals'], True),
            (B, 88, 'admin', ['tasks', 'deals'], True),
        ):
            conn.execute('INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,active) VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s)',
                         (tenant, actor, 'Task actor ' + str(actor), role, json.dumps(grants), active))
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'members','{\"82\":{\"pipeline_ids\":[\"10\"]}}',11)", (A,))
        conn.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s::uuid,%s::uuid,'Synthetic task client')", (CONTACT, A))
        for ident, tenant, pipeline, stage, owner, title in (
            (DEALS[0], A, '10', '101', 82, 'A task deal'),
            (DEALS[1], A, '20', '201', 11, 'A other pipeline'),
            (DEALS[2], A, '10', '102', 82, 'A hidden stage'),
            (DEALS[3], B, '10', '101', 22, 'B PRIVATE task deal'),
            (DEALS[4], A, '10', '101', 83, 'A operator own deal'),
        ):
            conn.execute('INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,responsible_id,contact_id,channel_id,conversation_id) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s::uuid,%s::uuid,%s)',
                         (ident, tenant, title, pipeline, stage, owner, CONTACT if tenant == A else None,
                          CA if ident == DEALS[0] else None, 'client' if ident == DEALS[0] else ''))
    f['provider'] = ExternalTransport()
    f['provider'].tasks[501] = {'id': 501, 'text': 'A cached task', 'entity_id': 101,
        'entity_type': 'leads', 'responsible_user_id': 999, 'is_completed': False,
        'complete_till': int(datetime.fromisoformat(DUE).timestamp())}
    f['sync'] = _production_sync()
    f['posts'] = []
    f['native'] = {}
    for name, tenant, deal, creator, target in (
        ('a', A, DEALS[0], 11, 41), ('manager', A, DEALS[0], 82, 82),
        ('operator', A, DEALS[4], 83, 83), ('other', A, DEALS[1], 11, 41),
        ('foreign', B, DEALS[3], 22, 22),
    ):
        request_id = _uuid('seed-request-' + name)
        f['native'][name] = native_tasks.create_task(tenant, deal, ('A native task ' if tenant == A else 'B PRIVATE native task ') + name,
            target, due_at=DUE, created_by=creator, actor_id=creator, idempotency_key=request_id)
    with f['factory']() as conn:
        # Historical own tasks remain stored after their deal/stage becomes
        # inaccessible. Current list/detail authority must exclude them.
        for name, deal in (('old_hidden', DEALS[2]), ('old_other_pipeline', DEALS[1])):
            conn.execute('INSERT INTO saas_crm_native_tasks(id,tenant_id,deal_id,text,due_at,responsible_id,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::timestamptz,82,82)',
                         (_uuid(name), A, deal, 'HIDDEN historical manager task ' + name, DUE))
    marker = TenantPolicy(importlib.import_module('tenant_member_store').member(A, 83)).task_marker()
    f['provider'].tasks[502] = {'id': 502, 'text': 'Operator own cached task\n' + marker,
        'responsible_user_id': 999, 'is_completed': False,
        'complete_till': int(datetime.fromisoformat(DUE).timestamp())}
    for tenant in (A, B):
        crm.upsert_crm_deals(tenant_id=tenant, deals=[{'kommo_lead_id': 101, 'pipeline_id': 10, 'status_id': 101,
            'name': 'A external deal' if tenant == A else 'B PRIVATE external deal', 'channel': 'kommo',
            'raw': {'id': 101, 'account_id': 100}}])
        crm.upsert_crm_tasks(tenant_id=tenant, tasks=[
            {'kommo_task_id': 501, 'kommo_lead_id': 101, 'text': 'A cached task' if tenant == A else 'B PRIVATE cached task',
             'due_at': DUE, 'responsible_id': 999, 'raw': {'id': 501, 'entity_id': 101, 'entity_type': 'leads'}},
        ])
    crm.upsert_crm_tasks(tenant_id=A, tasks=[{'kommo_task_id': 502, 'text': 'Operator own cached task\n' + marker,
        'due_at': DUE, 'responsible_id': 999, 'raw': {'id': 502}}])
    yield f
    assert f['blocked'] == []


def _task_apis(f, origin=ORIGIN):
    external = legacy_api.handlers(legacy_api.TaskApiContext(
        member=f['session'].profile, policy=TenantPolicy, sync_crm=f['sync'],
        list_tasks=crm.list_crm_tasks, executor_profiles=commands.task_executor_profiles,
        list_deals=crm.list_crm_deals, create_task=external_tasks.create_task,
        complete_task=completion.complete_task, approval_page=commands.pending_task_approval_page,
        decide_approval=approvals.decide_task_approval, crm_request=f['provider'],
        origin=origin, platform_error=TenantPlatformError, pending_error=commands.TaskCommandPending, logger=LOGGER))
    native = native_api.handler(f['session'].profile, origin, LOGGER)
    return external, native


def _app(f, origin=ORIGIN):
    @web.middleware
    async def mutation_allowlist(request, handler):
        if request.method not in {'GET', 'HEAD'}:
            allowed = request.method == 'POST' and (request.path.startswith('/api/platform/crm/native/tasks') or request.path in {'/api/platform/crm/tasks', '/api/platform/crm/tasks/complete'})
            if not allowed:
                f['blocked'].append((request.method, request.path))
                return web.json_response({'success': False, 'error': 'Unexpected test mutation'}, status=405)
            f['posts'].append({'path': request.path, 'query': dict(request.query), 'body': await request.json()})
        return await handler(request)
    app = web.Application(middlewares=[mutation_allowlist])
    auth, _ = _apis(f, origin)
    external, native = _task_apis(f, origin)
    app.router.add_get('/api/platform/me', auth['me'])
    app.router.add_get('/api/platform/crm/tasks', external['tasks'])
    app.router.add_post('/api/platform/crm/tasks', external['create'])
    app.router.add_post('/api/platform/crm/tasks/complete', external['complete'])
    app.router.add_get('/api/platform/crm/tasks/options', external['options'])
    for suffix in ('', '/{id}'):
        app.router.add_get('/api/platform/crm/native/tasks' + suffix, native)
        app.router.add_post('/api/platform/crm/native/tasks' + suffix, native)
    return app


def _state(f):
    state = _snapshot(f)
    with f['factory']() as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND (tablename LIKE 'saas_crm_%' OR tablename LIKE 'saas_tenant_pipeline%' OR tablename='saas_task_commands') ORDER BY tablename").fetchall()
        for row in tables:
            values = conn.execute(psycopg.sql.SQL('SELECT to_jsonb(t) AS value FROM {} t').format(psycopg.sql.Identifier(row['tablename']))).fetchall()
            state[row['tablename']] = sorted(json.dumps(item['value'], sort_keys=True) for item in values)
    return state


def _identity(tenant=A, actor=11):
    return {'expected_tenant_id': tenant, 'expected_user_id': str(actor)}


async def _get(http, cookie, source='native', *, tenant=A, actor=11, extra=None, path='/api/platform/crm/tasks', flow=True):
    params = [('flow', '1'), ('source', source), *_identity(tenant, actor).items()] if flow else []
    if extra: params.extend(extra.items() if isinstance(extra, dict) else extra)
    response = await http.get(path, params=params, headers={'Cookie': COOKIE_NAME + '=' + cookie})
    return response.status, await response.json()


async def _post(http, cookie, data, *, path='/api/platform/crm/native/tasks', query=None):
    response = await http.post(path, params=query or {}, headers={'Cookie': COOKIE_NAME + '=' + cookie, 'Origin': ORIGIN}, json=data)
    return response.status, await response.json()


def _native_command(*, request_id=None, deal=DEALS[0], target=41, text='Isolated task command'):
    return {'deal_id': deal, 'text': text, 'responsible_id': target, 'due_at': DUE,
            'request_id': request_id or str(uuid.uuid4()), **_identity()}


def test_plain_flow_reads_and_legacy_reload_never_enqueue_sync_or_call_provider(task_flow):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for source in ('native', 'kommo', 'native', 'kommo'):
                status, page = await _get(http, cookie, source)
                assert status == 200 and page['success'] is True and page['source'] == source, page
                assert page['shown'] == len(page['tasks']) and page['state'] == 'ready'
                assert all(row['source'] == source and isinstance(row['id'], str) for row in page['tasks'])
                assert all(row['status'] in ('open', 'completed') for row in page['tasks'])
            status, legacy = await _get(http, cookie, flow=False)
            assert status == 200 and legacy['success'] is True
    asyncio.run(scenario())
    assert f['provider'].calls == [] and _state(f) == before


@pytest.mark.parametrize('actor,source,status,scope', [
    (11, 'native', 200, 'tenant'), (81, 'native', 200, 'tenant'),
    (11, 'kommo', 200, 'tenant'), (81, 'kommo', 200, 'tenant'),
    (82, 'native', 200, 'personal'), (83, 'native', 200, 'personal'),
    (84, 'native', 200, 'tenant'), (86, 'native', 200, 'tenant'),
    (86, 'kommo', 403, None), (87, 'native', 403, None),
    (82, 'kommo', 200, 'pipelines'), (83, 'kommo', 200, 'marker'),
])
def test_sources_use_real_distinct_policy_and_current_task_scope(task_flow, actor, source, status, scope):
    f = task_flow
    cookie = f['session'].issue(A, actor)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            actual, page = await _get(http, cookie, source, actor=actor)
            assert actual == status, page
            if status != 200:
                assert not page.get('tasks')
                return
            assert page['scope'] == scope
            assert 'B PRIVATE' not in json.dumps(page)
            if source == 'native' and actor in (82, 83):
                assert {row['id'] for row in page['tasks']} == {str(f['native']['manager' if actor == 82 else 'operator']['id'])}
            if actor == 84:
                own = next(row for row in page['sources'] if row['source'] == 'native')
                assert own['view'] is True and all(own[action] is False for action in ('create', 'update', 'complete', 'archive'))
    asyncio.run(scenario())
    assert f['provider'].calls == [] and _state(f) == before


def test_native_create_receipt_recovers_exact_actor_request_and_replay_keeps_one_task(task_flow):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    command = _native_command()
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, created = await _post(http, cookie, command)
            assert status == 200, created
            task = created['task']
            assert task['id'] != command['request_id']
            for _ in range(2):
                status, receipt = await _get(http, cookie, extra={'request_id': command['request_id']})
                assert status == 200 and receipt['command']['state'] == 'created', receipt
                assert receipt['command']['task_id'] == task['id']
                assert receipt['command']['task']['id'] == task['id']
            status, replay = await _post(http, cookie, command)
            assert status == 200 and replay['task']['id'] == task['id']
            status, exact = await _get(http, cookie, extra={'task_id': task['id']})
            assert status == 200 and exact['task']['id'] == task['id']
            status, changed = await _post(http, cookie, {**command, 'text': 'Changed request command'})
            assert status in (400, 409), changed
            with f['factory']() as conn:
                rows = conn.execute("SELECT id,text,created_by FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND idempotency_key=%s", (A, 'manual:' + command['request_id'])).fetchall()
                assert len(rows) == 1 and str(rows[0]['id']) == task['id']
                assert rows[0]['text'] == command['text'] and rows[0]['created_by'] == 11
    asyncio.run(scenario())
    assert f['provider'].calls == []


@pytest.mark.parametrize('intent', [
    {'source': 'native'}, {'source': 'kommo', 'task_id': '501'},
    {'source': 'kommo', 'request_id': _uuid('invalid-completion-query')},
])
def test_external_completion_rejects_wrong_source_or_lookup_query_before_any_command_or_transport(task_flow, intent):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, response = await _post(http, cookie,
                {'task_id': '501', 'request_id': str(uuid.uuid4()), 'result_text': 'Do not execute invalid intent'},
                path='/api/platform/crm/tasks/complete', query={'flow': '1', **_identity(), **intent})
            assert status == 400, response
    asyncio.run(scenario())
    assert f['provider'].calls == [] and _state(f) == before


def test_external_completion_retains_valid_kommo_and_unqualified_legacy_paths_without_duplicate_patch(task_flow):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            first = None
            for ident, query in (('501', {'flow': '1', 'source': 'kommo', **_identity()}), ('502', {})):
                command = {'task_id': ident, 'request_id': str(uuid.uuid4()), 'result_text': 'Synthetic completed result'}
                status, response = await _post(http, cookie, command,
                    path='/api/platform/crm/tasks/complete', query=query)
                assert status == 200 and response.get('completed') is True, response
                if first is None: first = (command, query)
            calls = list(f['provider'].calls)
            status, replay = await _post(http, cookie, first[0],
                path='/api/platform/crm/tasks/complete', query=first[1])
            assert status == 200 and replay['completed'] is True
            assert f['provider'].calls == calls
            with f['factory']() as conn:
                rows = conn.execute('SELECT tenant_id,kommo_task_id,kommo_lead_id,completed FROM saas_crm_tasks ORDER BY tenant_id,kommo_task_id').fetchall()
                assert all(row['completed'] is True for row in rows if str(row['tenant_id']) == A)
                assert all(row['completed'] is False for row in rows if str(row['tenant_id']) == B)
                assert next(row for row in rows if str(row['tenant_id']) == A and row['kommo_task_id'] == 501)['kommo_lead_id'] == 101
    asyncio.run(scenario())
    writes = [(method, path) for _tenant, method, path, _data in f['provider'].calls if method != 'GET']
    assert writes == [('PATCH', 'tasks/501'), ('PATCH', 'tasks/502')]


@pytest.mark.parametrize('grants', [
    {'tasks': False}, {'tasks:view': False, 'tasks:create': False, 'tasks:update': False}, 'tasks', None,
])
def test_nonlist_native_grants_never_authorize_create_edit_or_state(task_flow, grants):
    f = task_flow
    with f['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id IN (41,81)',
                     (json.dumps(grants), A))
    cookie = f['session'].issue(A, 81)
    owner_cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command = {**_native_command(), **_identity(actor=81)}
            status, result = await _post(http, cookie, command)
            assert status == 403, result
            path = '/api/platform/crm/native/tasks/' + str(f['native']['a']['id'])
            for body in ({'action': 'update', 'text': 'Must not change', 'responsible_id': 41}, {'action': 'complete'}):
                status, result = await _post(http, cookie, {**body, **_identity(actor=81)}, path=path)
                assert status == 403, result
            status, result = await _get(http, cookie, actor=81)
            assert status == 403 and not result.get('tasks'), result
            # Owner API admission is valid, so these two calls exercise the
            # real domain recipient predicate rather than the API actor gate.
            status, result = await _post(http, owner_cookie, _native_command())
            assert status == 409 and result['error'] == 'assignee_unavailable', result
            status, result = await _post(http, owner_cookie, {'action': 'update', 'text': 'Never assign malformed grants', 'responsible_id': 41, **_identity()}, path=path)
            assert status == 409 and result['error'] == 'assignee_unavailable', result
    asyncio.run(scenario())
    assert f['provider'].calls == [] and _state(f) == before


def test_native_permission_shape_guard_preserves_existing_owner_role_authority(task_flow):
    f = task_flow
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET permissions='{\"tasks\":false}' WHERE tenant_id=%s::uuid AND telegram_id=11", (A,))
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, created = await _post(http, cookie, _native_command())
            assert status == 200, created
            path = '/api/platform/crm/native/tasks/' + created['task']['id']
            status, edited = await _post(http, cookie, {'action': 'update', 'text': 'Owner preserved',
                'responsible_id': 41, 'expected_updated_at': created['task']['updated_at'], **_identity()}, path=path)
            assert status == 200 and edited['task']['text'] == 'Owner preserved', edited
            status, completed = await _post(http, cookie, {'action': 'complete',
                'expected_updated_at': edited['task']['updated_at'], **_identity()}, path=path)
            assert status == 200 and completed['task']['status'] == 'completed', completed
    asyncio.run(scenario())
    assert f['provider'].calls == []


def _wait_task_row_blocked(factory, holder_pid):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with factory() as observer:
            rows = observer.execute('''SELECT pid,query FROM pg_stat_activity
              WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))''', (holder_pid,)).fetchall()
            task_writers = [row for row in rows if 'saas_crm_native_tasks' in row['query'] and 'FOR UPDATE' in row['query']]
            if task_writers: return task_writers[0]['pid']
        time.sleep(.02)
    raise AssertionError('The actual task mutation never waited on the native task row')


@pytest.mark.parametrize('action', ['update', 'complete', 'reopen', 'archive'])
def test_waiting_actual_task_mutation_rejects_stale_version_and_preserves_newer_row(task_flow, action):
    f = task_flow
    ident = str(f['native']['a']['id'])
    if action == 'reopen': native_tasks.set_task_state(A, ident, 'completed', actor_id=11)
    cookie = f['session'].issue(A, 11)
    holder = f['factory']()
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, read = await _get(http, cookie, extra={'task_id': ident})
            assert status == 200
            original = read['task']
            holder.execute('SELECT id FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (A, ident))
            body = {'action': action, 'expected_updated_at': original['updated_at'], **_identity()}
            if action == 'update': body.update(text='Stale editor must not overwrite', responsible_id=41, due_at=DUE)
            request = asyncio.create_task(_post(http, cookie, body, path='/api/platform/crm/native/tasks/' + ident))
            try:
                await asyncio.to_thread(_wait_task_row_blocked, f['factory'], holder.info.backend_pid)
                holder.execute("UPDATE saas_crm_native_tasks SET text='Newer committed editor',updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, ident))
                holder.commit()
                actual, rejected = await asyncio.wait_for(request, 8)
                assert actual == 409 and rejected['error'] == 'task_conflict', rejected
                status, fresh = await _get(http, cookie, extra={'task_id': ident})
                assert status == 200 and fresh['task']['text'] == 'Newer committed editor'
                assert fresh['task']['updated_at'] != original['updated_at']
                assert fresh['task']['status'] == original['status']
                assert fresh['task']['deal_id'] == original['deal_id']
                assert fresh['task']['contact_id'] == CONTACT and fresh['task']['channel_id'] == CA
                with f['factory']() as conn:
                    row = conn.execute('SELECT archived_at FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, ident)).fetchone()
                    assert row['archived_at'] is None
            finally:
                holder.rollback()
                if not request.done(): request.cancel()
    try: asyncio.run(scenario())
    finally: holder.close()
    assert f['provider'].calls == []


def _external_command(*, request_id=None, executor=11, lead=101, pipeline=0, due=DUE):
    return {'request_id': request_id or str(uuid.uuid4()), 'text': 'Synthetic external task',
            'executor_id': executor, 'lead_id': lead, 'pipeline_id': pipeline, 'due_at': due}


def test_durable_provider_success_survives_real_cache_lock_timeout_without_resend(task_flow, monkeypatch):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    command = _external_command()
    holder = f['factory']()
    holder.execute('LOCK TABLE saas_crm_tasks IN SHARE MODE')
    def short_cache_connection():
        conn = f['factory']()
        conn.execute("SET lock_timeout='150ms'")
        return conn
    monkeypatch.setattr(crm, '_connect', short_cache_connection)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            try:
                status, saved = await asyncio.wait_for(_post(http, cookie, command,
                    path='/api/platform/crm/tasks', query={'flow': '1', 'source': 'kommo', **_identity()}), 8)
                assert status == 200 and saved['command']['state'] == 'created', saved
                receipt = saved['command']
                assert receipt['task_id'] and receipt['task'] is None and receipt['cache_state'] == 'unavailable'
                with f['factory']() as conn:
                    state = conn.execute('SELECT state FROM saas_task_commands WHERE tenant_id=%s::uuid AND actor_id=11 AND request_id=%s::uuid', (A, command['request_id'])).fetchone()['state']
                    assert state['step'] == 'done' and str(state['result']['task_id']) == receipt['task_id']
                    assert conn.execute('SELECT 1 FROM saas_crm_tasks WHERE tenant_id=%s::uuid AND kommo_task_id=%s', (A, int(receipt['task_id']))).fetchone() is None
            finally:
                holder.rollback()
            calls = list(f['provider'].calls)
            for _ in range(2):
                status, checked = await _get(http, cookie, 'kommo', extra={'request_id': command['request_id']})
                assert status == 200 and checked['command'] == receipt, checked
            status, replay = await _post(http, cookie, command, path='/api/platform/crm/tasks',
                query={'flow': '1', 'source': 'kommo', **_identity()})
            assert status == 200 and replay['command'] == receipt, replay
            assert f['provider'].calls == calls
            writes = [(method, path) for _tenant, method, path, _data in calls if method != 'GET']
            assert writes == [('POST', 'tasks')]
    try: asyncio.run(scenario())
    finally: holder.close()


@pytest.mark.parametrize('method,path,query', [
    ('GET', '/api/platform/crm/tasks', [('flow', '1'), ('source', 'native')]),
    ('GET', '/api/platform/crm/tasks/options', [('flow', '1'), ('source', 'bad'), ('source', 'kommo')]),
    ('POST', '/api/platform/crm/tasks', [('flow', '1'), ('source', 'kommo')]),
    ('POST', '/api/platform/crm/tasks/complete', [('flow', '1'), ('source', 'native')]),
])
def test_cookie_absent_is_forbidden_before_query_validation_or_any_task_action(task_flow, method, path, query):
    f = task_flow
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            kwargs = {'params': query, 'headers': {'Origin': ORIGIN}}
            if method == 'POST': kwargs['json'] = {'task_id': 501, 'request_id': str(uuid.uuid4()), 'result_text': 'Must not execute'}
            response = await http.request(method, path, **kwargs)
            body = await response.json()
            assert response.status == 403 and body.get('error') == 'task_forbidden', body
    asyncio.run(scenario())
    assert f['provider'].calls == [] and _state(f) == before


@pytest.mark.parametrize('mode,actor,width,language', [
    ('ready', 11, 360, 'ru'), ('ready', 81, 390, 'az'),
    ('ready', 82, 768, 'en'), ('ready', 83, 1440, 'ru'),
    ('granular', 86, 390, 'en'), ('conflict', 11, 390, 'en'),
    ('unknown_committed', 11, 390, 'en'), ('retry_original', 11, 390, 'en'),
    ('tenant_late', 88, 390, 'en'), ('object_revoke', 82, 390, 'en'),
    ('external_unknown', 81, 390, 'en'), ('approval_pending', 83, 390, 'en'),
    ('states', 81, 390, 'en'),
])
def test_actual_tasks_app_reads_and_saves_through_cookie_postgres(task_flow, mode, actor, width, language):
    """The actual built app owns rendering; no task/profile API is intercepted."""
    from tenant_web_assets import register_assets
    f = task_flow
    assert (ROOT / '.support-test-deps/node_modules/playwright').exists()
    tokens = {'initial': f['session'].issue(A, actor), 'company_b': f['session'].issue(B, 88)}
    if mode == 'external_unknown': f['provider'].lose_task_response = True
    if mode == 'approval_pending':
        with f['factory']() as conn:
            conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'task_approval','{\"creation_requires_admin\":true,\"self_created_exempt\":false}',11)", (A,))
    before = _state(f)
    state = {'hold': False, 'pending': 0, 'released': 0, 'held_paths': [], 'read_paths': [],
             'write_fault': None, 'fault_count': 0, 'changed': False, 'fail_native': mode == 'states'}
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def start():
        state['release'] = asyncio.Event()
        app = _app(f, origin)
        @web.middleware
        async def transport_observer(request, handler):
            task_path = request.path.startswith('/api/platform/crm/native/tasks') or request.path == '/api/platform/crm/tasks'
            response = await handler(request)
            if state['write_fault'] == 'after' and request.method == 'POST' and request.path == '/api/platform/crm/native/tasks' and response.status == 200:
                state['write_fault'] = None
                state['fault_count'] += 1
                # Handler really committed. Only its HTTP confirmation is lost.
                return web.json_response({})
            if task_path and request.method == 'GET':
                state['read_paths'].append(request.path_qs)
                if state['fail_native'] and request.query.get('flow') == '1' and request.query.get('source') == 'native':
                    # The real authorized database read happened above. Only
                    # its HTTP transport fails; no source page is manufactured.
                    return web.json_response({'success': False, 'error': 'task_unavailable'}, status=503)
                if state['hold'] and request.query.get('task_id') == '501':
                    state['hold'] = False
                    streamed = web.StreamResponse(status=response.status, headers={key: value for key, value in response.headers.items() if key.lower() != 'content-length'})
                    await streamed.prepare(request)
                    state['pending'] += 1
                    state['held_paths'].append(request.path_qs)
                    await state['release'].wait()
                    try:
                        await streamed.write(response.body)
                        await streamed.write_eof()
                    except ConnectionResetError:
                        state['cancelled'] = True
                    state['released'] += 1
                    return streamed
            return response
        app.middlewares.insert(0, transport_observer)
        auth, _ = _apis(f, origin)
        app.router.add_get('/api/platform/account', auth['account'])
        app.router.add_get('/api/platform/sessions', auth['sessions'])
        async def index(_request): return web.FileResponse(ROOT / 'docs/tenant-app.html')
        app.router.add_get('/app', index)
        register_assets(app)

        async def control(request):
            command = request.match_info['command']
            if command == 'session':
                token = tokens.get(request.query.get('identity', 'initial'))
                if not token: raise web.HTTPBadRequest()
                response = web.json_response({'success': True})
                response.set_cookie(COOKIE_NAME, token, httponly=True, samesite='Lax', path='/')
                return response
            if command == 'hold':
                state['hold'] = True
                state['release'].clear()
            elif command == 'release': state['release'].set()
            elif command == 'fault':
                assert mode in ('unknown_committed', 'retry_original')
                state['write_fault'] = 'after' if mode == 'unknown_committed' else None
            elif command == 'newer':
                assert mode == 'conflict'
                with f['factory']() as conn:
                    conn.execute("UPDATE saas_crm_native_tasks SET text='Newer server task version',updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, str(f['native']['a']['id'])))
                state['changed'] = True
            elif command == 'source-fault':
                assert mode == 'states'
                state['fail_native'] = True
            elif command == 'source-restore':
                assert mode == 'states'
                state['fail_native'] = False
            elif command == 'source-empty':
                assert mode == 'states'
                with f['factory']() as conn:
                    conn.execute('UPDATE saas_crm_native_tasks SET archived_at=now() WHERE tenant_id=%s::uuid', (A,))
                state['changed'] = True
            elif command == 'object-revoke':
                assert mode == 'object_revoke'
                with f['factory']() as conn:
                    conn.execute("UPDATE saas_tenant_pipeline_stages SET settings='{\"visible\":false}' WHERE tenant_id=%s::uuid AND pipeline_id='10'", (A,))
                state['changed'] = True
            elif command != 'state': raise web.HTTPNotFound()
            return web.json_response({**{key: value for key, value in state.items() if key not in {'release', 'runner'}}, 'processed_posts': len(f['posts']), 'provider_calls': len(f['provider'].calls)})
        app.router.add_get('/test/{command}', control)

        async def unrelated(request):
            # Empty unrelated startup data; every task/profile/native task
            # route above executes actual handlers and current database policy.
            assert '/crm/native/tasks' not in request.path
            assert not request.path.startswith('/api/platform/crm/tasks')
            if request.path == '/api/platform/ai/dialogs':
                return web.json_response({'success': True, 'result': {'dialogs': [], 'channels': [], 'agents': [], 'next_offset': None, 'total_count': 0, 'unread_count': 0}})
            return web.json_response({'success': True, 'result': {}, 'tasks': [], 'deals': [], 'contacts': [], 'companies': [], 'pipelines': [], 'members': [], 'integrations': [], 'notifications': [], 'unread_count': 0, 'requests': []})
        app.router.add_get('/api/{tail:.*}', unrelated)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.SockSite(runner, sock).start()
        state['runner'] = runner
        ready.set()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(10), 'Isolated task HTTP fixture did not start'
    try:
        env = {**os.environ, 'TASK_RUNTIME_ORIGIN': origin, 'TASK_RUNTIME_MODE': mode,
               'TASK_RUNTIME_ACTOR': str(actor), 'TASK_RUNTIME_WIDTH': str(width),
               'TASK_RUNTIME_LANGUAGE': language, 'TASK_RUNTIME_TENANT_A': A, 'TASK_RUNTIME_TENANT_B': B,
               'TASK_RUNTIME_TASK_ID': str(f['native']['manager' if mode == 'object_revoke' else 'a']['id']), 'TASK_RUNTIME_DEAL_ID': DEALS[0]}
        result = subprocess.run(['node', str(ROOT / 'tests/task_read_save_flow_http_browser.cjs')],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=75)
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
        assert not f['blocked']
        # Ordinary task/app reads cannot enqueue sync or mutate business rows.
        after = _state(f)
        changed = {key for key in after if before[key] != after[key]}
        if mode == 'tenant_late':
            assert changed == set() and f['posts'] == [], changed
        elif mode in ('ready', 'granular', 'unknown_committed', 'retry_original', 'conflict'):
            assert changed <= {'saas_crm_native_tasks', 'saas_crm_tasks', 'saas_task_commands', *({'saas_tenant_audit_events'} if actor == 81 and mode == 'ready' else set())}, changed
            if actor == 81 and mode == 'ready':
                with f['factory']() as conn:
                    audit = conn.execute("SELECT tenant_id,actor_telegram_id,action,entity_type FROM saas_tenant_audit_events WHERE action IN ('task_created','task_completed') ORDER BY action").fetchall()
                    assert [(str(row['tenant_id']), row['actor_telegram_id'], row['action'], row['entity_type']) for row in audit] == [(A,81,'task_completed','kommo_task'),(A,81,'task_created','kommo_task')]

        if mode == 'states':
            assert changed == {'saas_crm_native_tasks'} and not f['posts'], changed
        if mode == 'object_revoke':
            assert changed == {'saas_tenant_pipeline_stages'} and not f['posts'], changed
        if mode == 'external_unknown':
            assert changed == {'saas_task_commands'}, changed
            assert [(method, path) for _tenant, method, path, _data in f['provider'].calls if method != 'GET'] == [('POST','tasks')]
            assert len(f['posts']) == 1
        elif mode == 'approval_pending':
            assert changed == {'saas_task_commands'} and len(f['posts']) == 1, changed
            assert f['provider'].calls == []
        elif actor != 81 or mode != 'ready': assert f['provider'].calls == []
        else:
            writes = [(method, path) for _tenant, method, path, _data in f['provider'].calls if method != 'GET']
            assert writes == [('POST', 'tasks'), ('PATCH', 'tasks/501')], writes
        if mode in ('unknown_committed', 'retry_original'):
            assert state['fault_count'] == (1 if mode == 'unknown_committed' else 0)
            native_posts = [post for post in f['posts'] if post['path'] == '/api/platform/crm/native/tasks']
            assert len(native_posts) == 1, native_posts
    finally:
        loop.call_soon_threadsafe(state['release'].set)
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()
        assert not thread.is_alive()


@pytest.mark.parametrize('route', ['selected_deal', 'personal_pipeline', 'standalone'])
def test_external_existing_routes_keep_deal_or_create_personal_deal_or_marker_without_move(task_flow, route):
    f = task_flow
    actor = {'selected_deal': 11, 'personal_pipeline': 82, 'standalone': 83}[route]
    cookie = f['session'].issue(A, actor)
    command = _external_command(executor=actor, lead=101 if route == 'selected_deal' else 0,
                                pipeline=10 if route == 'personal_pipeline' else 0)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            query = {'flow': '1', 'source': 'kommo', **_identity(actor=actor)}
            status, saved = await _post(http, cookie, command, path='/api/platform/crm/tasks', query=query)
            assert status == 200 and saved['command']['state'] == 'created', saved
            calls = copy.deepcopy(f['provider'].calls)
            status, readback = await _get(http, cookie, 'kommo', actor=actor, extra={'request_id': command['request_id']})
            assert status == 200 and readback['command'] == saved['command'], readback
            status, repeated = await _post(http, cookie, command, path='/api/platform/crm/tasks', query=query)
            assert status == 200 and repeated['command'] == saved['command'], repeated
            assert f['provider'].calls == calls
            writes = [(method, path, body.get('json_body')) for _tenant, method, path, body in calls if method != 'GET']
            if route == 'personal_pipeline':
                assert [(method, path) for method, path, _body in writes] == [('POST', 'leads'), ('POST', 'tasks')]
                assert writes[0][2][0]['pipeline_id'] == 10 and writes[0][2][0]['status_id'] == 101
                assert writes[1][2][0]['entity_id'] == int(saved['lead_id'])
            else:
                assert [(method, path) for method, path, _body in writes] == [('POST', 'tasks')]
                task = writes[0][2][0]
                if route == 'selected_deal': assert task['entity_id'] == 101
                else:
                    marker = TenantPolicy(importlib.import_module('tenant_member_store').member(A, actor)).task_marker()
                    assert marker in task['text'] and not task.get('entity_id')
            assert f['provider'].leads[101]['pipeline_id'] == 10 and f['provider'].leads[101]['status_id'] == 101
    asyncio.run(scenario())


def test_external_provider_unknown_is_durable_actor_scoped_and_never_reposts(task_flow):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    other = f['session'].issue(A, 81)
    foreign = f['session'].issue(B, 22)
    f['provider'].lose_task_response = True
    command = _external_command()
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            query = {'flow': '1', 'source': 'kommo', **_identity()}
            status, unknown = await _post(http, cookie, command, path='/api/platform/crm/tasks', query=query)
            assert status == 409 and unknown['retry_same_request'] is True, unknown
            calls = copy.deepcopy(f['provider'].calls)
            assert len([call for call in calls if call[1:3] == ('POST', 'tasks')]) == 1
            for _ in range(2):
                status, result = await _get(http, cookie, 'kommo', extra={'request_id': command['request_id']})
                assert status == 200 and result['command'] == {'request_id': command['request_id'], 'source': 'kommo', 'state': 'unknown', 'task_id': None, 'task': None, 'cache_state': 'not_applicable'}, result
            status, replay = await _post(http, cookie, command, path='/api/platform/crm/tasks', query=query)
            assert status == 409 and replay['retry_same_request'] is True, replay
            for token, tenant, actor in ((other, A, 81), (foreign, B, 22)):
                status, result = await _get(http, token, 'kommo', tenant=tenant, actor=actor, extra={'request_id': command['request_id']})
                assert status == 200 and result['command']['state'] == 'not_confirmed'
                assert result['command']['task_id'] is None and command['text'] not in json.dumps(result['command'])
            assert f['provider'].calls == calls
            with f['factory']() as conn:
                row = conn.execute('SELECT state FROM saas_task_commands WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND actor_id=11', (A, command['request_id'])).fetchone()
                assert row['state']['step'] == 'task_creating'
    asyncio.run(scenario())


def test_creation_approval_is_honest_durable_pending_without_task_or_provider(task_flow):
    f = task_flow
    with f['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'task_approval','{\"creation_requires_admin\":true,\"self_created_exempt\":false}',11)", (A,))
    cookie = f['session'].issue(A, 83)
    command = _external_command(executor=83, lead=0)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            query = {'flow': '1', 'source': 'kommo', **_identity(actor=83)}
            status, saved = await _post(http, cookie, command, path='/api/platform/crm/tasks', query=query)
            assert status == 200 and saved['approval_pending'] is True and not saved.get('task_id'), saved
            receipt = {'request_id': command['request_id'], 'source': 'kommo', 'state': 'approval_pending', 'task_id': None, 'task': None, 'cache_state': 'not_applicable'}
            assert saved['command'] == receipt
            for _ in range(2):
                status, checked = await _get(http, cookie, 'kommo', actor=83, extra={'request_id': command['request_id']})
                assert status == 200 and checked['command'] == receipt, checked
            status, replay = await _post(http, cookie, command, path='/api/platform/crm/tasks', query=query)
            assert status == 200 and replay['command'] == receipt, replay
    asyncio.run(scenario())
    assert f['provider'].calls == []


@pytest.mark.parametrize('extra,expected', [
    ([('expected_user_id', '11')], 400),
    ([('source', 'kommo')], 400),
    ({'task_id': '501'}, 400),
    ({'task_id': _uuid('query-task'), 'request_id': _uuid('query-request')}, 400),
    ({'request_id': 'not-a-uuid'}, 400),
    ([('expected_tenant_id', B)], 400),
])
def test_authenticated_flow_rejects_ambiguous_or_wrong_typed_lookup_without_side_effects(task_flow, extra, expected):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, result = await _get(http, cookie, extra=extra)
            assert status == expected and result['error'] == 'task_invalid_query', result
            assert not result.get('tasks') and not result.get('command')
            response = await http.get('/api/platform/crm/tasks', params={'flow': '1', 'source': 'native', **_identity(B, 11)}, headers={'Cookie': COOKIE_NAME + '=' + cookie})
            body = await response.json()
            assert response.status == 409 and body['error'] == 'task_context_changed', body
    asyncio.run(scenario())
    assert _state(f) == before and f['provider'].calls == []


def test_confirmed_external_receipt_remains_honest_after_scope_revocation_without_private_task(task_flow):
    f = task_flow
    cookie = f['session'].issue(A, 82)
    command = _external_command(executor=82)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, saved = await _post(http, cookie, command, path='/api/platform/crm/tasks',
                query={'flow': '1', 'source': 'kommo', **_identity(actor=82)})
            assert status == 200 and saved['command']['state'] == 'created', saved
            task_id = saved['command']['task_id']
            assert saved['command']['task'] is not None
            calls = copy.deepcopy(f['provider'].calls)
            import tenant_workflow_store as workflows
            workflows.save_tenant_workflow_config(tenant_id=A, owner_id=11,
                pipelines=[{'pipeline_id': '10', 'name': 'Pipeline 10', 'owner_telegram_id': 11}], policies={'members': {}})
            status, checked = await _get(http, cookie, 'kommo', actor=82, extra={'request_id': command['request_id']})
            assert status == 200 and checked['command'] == {'request_id': command['request_id'], 'source': 'kommo', 'state': 'created', 'task_id': task_id, 'task': None, 'cache_state': 'unavailable'}, checked
            status, unavailable = await _get(http, cookie, 'kommo', actor=82, extra={'task_id': task_id})
            assert status == 404 and not unavailable.get('task'), unavailable
            with f['factory']() as conn:
                conn.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=82", (A,))
            status, denied = await _get(http, cookie, 'kommo', actor=82, extra={'request_id': command['request_id']})
            assert status == 403 and 'command' not in denied, denied
            assert f['provider'].calls == calls
    asyncio.run(scenario())


def test_same_native_request_inflight_waiters_commit_exactly_one_task(task_flow):
    f = task_flow
    cookie = f['session'].issue(A, 11)
    command = _native_command()
    holder = f['factory']()
    holder.execute('SELECT id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (A, DEALS[0]))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            attempts = [asyncio.create_task(_post(http, cookie, command)) for _ in range(2)]
            try:
                deadline = time.monotonic() + 8
                while True:
                    with f['factory']() as conn:
                        rows = conn.execute("SELECT pid,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE datname=current_database() AND cardinality(pg_blocking_pids(pid))>0 AND query LIKE '%%saas_crm_native_deals%%' AND query LIKE '%%FOR UPDATE%%'").fetchall()
                    writer_pids = {row['pid'] for row in rows}
                    # PostgreSQL can queue the second writer behind the first
                    # waiting writer instead of reporting the holder directly.
                    if len(rows) == 2 and any(holder.info.backend_pid in row['blockers'] for row in rows) and all(set(row['blockers']) <= writer_pids | {holder.info.backend_pid} for row in rows): break
                    assert time.monotonic() < deadline, 'Both real concurrent writers must wait on the existing deal row'
                    await asyncio.sleep(.02)
                holder.commit()
                responses = await asyncio.wait_for(asyncio.gather(*attempts), 8)
                assert all(status == 200 for status, _body in responses), responses
                ids = {body['task']['id'] for _status, body in responses}
                assert len(ids) == 1 and next(iter(ids)) != command['request_id']
                with f['factory']() as conn:
                    rows = conn.execute('SELECT id,text FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND idempotency_key=%s', (A, 'manual:' + command['request_id'])).fetchall()
                    assert len(rows) == 1 and str(rows[0]['id']) in ids and rows[0]['text'] == command['text']
            finally:
                holder.rollback()
                for attempt in attempts:
                    if not attempt.done(): attempt.cancel()
    try: asyncio.run(scenario())
    finally: holder.close()
    assert f['provider'].calls == []


def test_native_same_request_uuid_across_tenants_has_independent_actor_bound_receipts(task_flow):
    f = task_flow
    tokens = {tenant: f['session'].issue(tenant, 88) for tenant in (A, B)}
    owner = f['session'].issue(A, 11)
    request_id = str(uuid.uuid4())
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            ids = {}
            for tenant, deal in ((A, DEALS[0]), (B, DEALS[3])):
                command = {**_native_command(request_id=request_id), 'deal_id': deal, 'responsible_id': 88, **_identity(tenant, 88)}
                status, saved = await _post(http, tokens[tenant], command)
                assert status == 200, saved
                ids[tenant] = saved['task']['id']
                status, exact = await _get(http, tokens[tenant], tenant=tenant, actor=88, extra={'request_id': request_id})
                assert status == 200 and exact['command']['task_id'] == ids[tenant]
            assert ids[A] != ids[B]
            status, foreign = await _get(http, tokens[B], tenant=B, actor=88, extra={'task_id': ids[A]})
            assert status == 404 and not foreign.get('task'), foreign
            status, other_actor = await _get(http, owner, extra={'request_id': request_id})
            assert status == 200 and other_actor['command']['state'] == 'not_confirmed' and other_actor['command']['task_id'] is None
    asyncio.run(scenario())
    assert f['provider'].calls == []
