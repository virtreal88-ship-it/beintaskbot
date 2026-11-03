"""CRM02: native PostgreSQL, current cookies and real task command services."""
import ast
import asyncio
import copy
import importlib
import json
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
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND (tablename LIKE 'saas_crm_%' OR tablename='saas_task_commands') ORDER BY tablename").fetchall()
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
