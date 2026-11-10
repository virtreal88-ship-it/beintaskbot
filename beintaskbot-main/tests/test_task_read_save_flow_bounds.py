"""CRM02 bounded reads retain exact identity and existing native search semantics."""
import asyncio
import time
import uuid

from aiohttp.test_utils import TestClient, TestServer
import pytest

from test_task_read_save_flow_postgres import (
    A, DEALS, task_flow, staff, db, database, _app, _get, _identity, _state,
)
from test_contacts_directory_postgres import _observe_sql


def _seed_native(f, count, *, text='Bounded native task', prefix='window'):
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f'isolated-native-{prefix}-{index}')) for index in range(count)]
    with f['factory']() as conn:
        with conn.cursor() as cur:
            cur.executemany('''INSERT INTO saas_crm_native_tasks
              (id,tenant_id,deal_id,text,responsible_id,created_by,due_at)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,41,11,'2090-01-01T00:00:00Z')''',
              [(ident, A, DEALS[0], f'{text} {index}') for index, ident in enumerate(ids)])
    return ids


def _seed_external(f, count, *, start=20000):
    with f['factory']() as conn:
        with conn.cursor() as cur:
            cur.executemany('''INSERT INTO saas_crm_tasks
              (tenant_id,kommo_task_id,kommo_lead_id,text,responsible_id,due_at)
              VALUES(%s::uuid,%s,101,%s,999,'2090-01-01T00:00:00Z')''',
              [(A, start+index, f'Bounded external task {index}') for index in range(count)])


def test_native_read_cap_does_not_hide_exact_task_or_own_request_recovery(task_flow):
    f = task_flow
    _seed_native(f, 105)
    target = str(f['native']['a']['id'])
    with f['factory']() as conn:
        request = conn.execute('SELECT idempotency_key FROM saas_crm_native_tasks WHERE id=%s::uuid', (target,)).fetchone()['idempotency_key'][7:]
    cookie, operator_cookie = f['session'].issue(A, 11), f['session'].issue(A, 83)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie)
            assert status == 200 and page['limit'] == 100 and page['shown'] == 100
            assert page['has_more'] is True and len(page['tasks']) == 100
            assert target not in {row['id'] for row in page['tasks']}
            assert 'total' not in page and 'source_total' not in page
            status, exact = await _get(http, cookie, extra={'task_id': target})
            assert status == 200 and exact['task']['id'] == target
            status, receipt = await _get(http, cookie, extra={'request_id': request})
            assert status == 200 and receipt['command']['state'] == 'created'
            assert receipt['command']['task_id'] == target
            assert receipt['command']['task']['id'] == target
            status, personal = await _get(http, operator_cookie, actor=83)
            assert status == 200 and personal['scope'] == 'personal'
            assert [row['id'] for row in personal['tasks']] == [str(f['native']['operator']['id'])]
            assert personal['shown'] == 1 and personal['has_more'] is False
    asyncio.run(scenario())
    assert _state(f) == before
    assert f['provider'].calls == []


def test_external_cap_and_marker_scope_preserve_lossless_exact_id_outside_window(task_flow):
    f = task_flow
    _seed_external(f, 205)
    task_id, lead_id = '9007199254740993', '9007199254740995'
    with f['factory']() as conn:
        conn.execute('''INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id,name)
          VALUES(%s::uuid,%s,10,101,'Lossless cached lead')''', (A, lead_id))
        conn.execute('''INSERT INTO saas_crm_tasks(tenant_id,kommo_task_id,kommo_lead_id,text,due_at)
          VALUES(%s::uuid,%s,%s,'Lossless cached task','2099-12-01T00:00:00Z')''', (A, task_id, lead_id))
    cookie, operator_cookie = f['session'].issue(A, 11), f['session'].issue(A, 83)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, 'kommo')
            assert status == 200 and page['limit'] == 200 and page['shown'] == 200
            assert page['has_more'] is True and len(page['tasks']) == 200
            assert task_id not in {row['id'] for row in page['tasks']}
            status, exact = await _get(http, cookie, 'kommo', extra={'task_id': task_id})
            assert status == 200
            assert exact['task']['id'] == task_id
            assert exact['task']['kommo_task_id'] == task_id
            assert exact['task']['kommo_lead_id'] == lead_id
            assert exact['task']['source'] == 'kommo'
            status, personal = await _get(http, operator_cookie, 'kommo', actor=83)
            assert status == 200 and personal['scope'] == 'marker'
            assert [row['id'] for row in personal['tasks']] == ['502']
            assert personal['shown'] == 1 and personal['has_more'] is False
    asyncio.run(scenario())
    assert _state(f) == before
    assert f['provider'].calls == []


@pytest.mark.parametrize('source,limit', [('native', 100), ('kommo', 200)])
def test_has_more_requires_an_actual_authorized_sentinel_not_a_full_page(task_flow, source, limit):
    f = task_flow
    table = 'saas_crm_native_tasks' if source == 'native' else 'saas_crm_tasks'
    with f['factory']() as conn:
        current = conn.execute(f'SELECT count(*) AS count FROM {table} WHERE tenant_id=%s::uuid', (A,)).fetchone()['count']
    if source == 'native':
        _seed_native(f, limit-current, prefix='exact-limit')
    else:
        _seed_external(f, limit-current)
    cookie = f['session'].issue(A, 11)
    async def page():
        async with TestClient(TestServer(_app(f))) as http:
            status, result = await _get(http, cookie, source)
            assert status == 200 and result['shown'] == limit and len(result['tasks']) == limit
            return result
    before = _state(f)
    assert asyncio.run(page())['has_more'] is False
    assert _state(f) == before
    if source == 'native':
        _seed_native(f, 1, prefix='sentinel')
    else:
        _seed_external(f, 1, start=30000)
    before = _state(f)
    assert asyncio.run(page())['has_more'] is True
    assert _state(f) == before
    assert f['provider'].calls == []


@pytest.mark.parametrize('needle', ['Literal %', 'Literal _', 'Literal \\', 'Ordinary specific'])
def test_native_existing_search_keeps_literal_semantics_with_or_without_browser_identity(task_flow, needle):
    f = task_flow
    rows = [('percent', 'Literal % signal'), ('underscore', 'Literal _ signal'),
            ('slash', 'Literal \\ signal'), ('ordinary', 'Ordinary specific signal')]
    with f['factory']() as conn:
        for label, text in rows:
            conn.execute('''INSERT INTO saas_crm_native_tasks
              (id,tenant_id,deal_id,text,responsible_id,created_by)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,41,11)''',
              (str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-search-'+label)), A, DEALS[0], text))
    expected = [str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-search-'+label))
                for label, text in rows if needle.casefold() in text.casefold()]
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for query in ({'search': needle}, {'search': needle, **_identity()}):
                status, result = await _get(http, cookie, path='/api/platform/crm/native/tasks', flow=False, extra=query)
                assert status == 200, result
                assert [row['id'] for row in result['tasks']] == expected
    asyncio.run(scenario())
    assert _state(f) == before
    assert f['provider'].calls == []


@pytest.mark.parametrize('needle', ['Kommo % marker', 'Kommo _ marker', 'Kommo \\\\ marker', 'Kommo ordinary marker'])
def test_kommo_search_uses_tenant_scoped_literal_query(task_flow, needle):
    f = task_flow
    rows = [('percent', 'Kommo % marker'), ('underscore', 'Kommo _ marker'),
            ('slash', 'Kommo \\\\ marker'), ('ordinary', 'Kommo ordinary marker')]
    ids = {label: str(80000 + index) for index, (label, _text) in enumerate(rows)}
    with f['factory']() as conn:
        for label, text in rows:
            conn.execute('''INSERT INTO saas_crm_tasks
              (tenant_id,kommo_task_id,kommo_lead_id,text,responsible_id,due_at)
              VALUES(%s::uuid,%s,101,%s,999,'2090-01-01T00:00:00Z')''', (A, ids[label], text))
    expected = next(ids[label] for label, text in rows if needle.casefold() in text.casefold())
    before = _state(f)
    from tenant_task_flow import read as read_tasks
    result = read_tasks({'tenant_id': A, 'telegram_id': 11}, source='kommo', search=needle)
    assert [row['id'] for row in result['tasks']] == [expected]
    assert _state(f) == before
    assert f['provider'].calls == []


def test_task_reader_never_combines_old_pipeline_owner_with_new_stage_visibility(task_flow, monkeypatch):
    import tenant_task_flow as reader
    import tenant_workflow_store as workflow
    import tenant_member_store as members
    from tenant_policy import TenantPolicy
    f = task_flow
    monkeypatch.setattr(workflow, '_connect', f['factory'])
    # This race concerns pipeline ownership and stage visibility. Clear the
    # fixture's separate explicit assignment before the reader starts, so
    # ownership loss really removes scope without updating a fenced policy.
    workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11, policies={'members': {}})
    with f['factory']() as conn:
        conn.execute('''INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id,name)
          VALUES(%s::uuid,104,10,102,'FORBIDDEN hidden stage deal')''', (A,))
        conn.execute('''INSERT INTO saas_crm_tasks(tenant_id,kommo_task_id,kommo_lead_id,text)
          VALUES(%s::uuid,504,104,'FORBIDDEN hidden stage task')''', (A,))
    cookie = f['session'].issue(A, 82)
    assert TenantPolicy(members.member(A, 82)).pipeline_scope() == [{'pipeline_id': 10, 'status_ids': [101]}]
    writes = []
    def after(query, _cursor):
        if 'FROM saas_tenant_pipelines WHERE' in query and not writes:
            writes.append(True)
            workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11,
                pipelines=[{'pipeline_id': '10', 'name': 'Reassigned', 'active': True,
                            'owner_telegram_id': 11, 'settings': {}}],
                stages=[{'pipeline_id': '10', 'stage_id': '102', 'name': 'Now visible',
                         'settings': {'visible': True}}])
            assert TenantPolicy(members.member(A, 82)).pipeline_scope() == []
    monkeypatch.setattr(reader, '_connect', _observe_sql(f['factory'], after=after))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, result = await _get(http, cookie, 'kommo', actor=82)
            assert status == 200, result
            assert writes == [True]
            assert [row['id'] for row in result['tasks']] == ['501']
            assert 'FORBIDDEN' not in str(result)
    asyncio.run(scenario())
    assert f['provider'].calls == []


def test_task_reader_waits_for_real_member_revision_then_retries_current_denial(task_flow, monkeypatch):
    import tenant_task_flow as reader
    f = task_flow
    cookie = f['session'].issue(A, 82)
    reader_pids = []
    def factory():
        conn = f['factory']()
        reader_pids.append(conn.info.backend_pid)
        return conn
    monkeypatch.setattr(reader, '_connect', factory)
    holder = f['factory']()
    holder.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=82 FOR UPDATE', (A,))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            request = asyncio.create_task(_get(http, cookie, 'kommo', actor=82))
            try:
                deadline = time.monotonic()+8
                blocked = False
                while time.monotonic() < deadline:
                    if reader_pids:
                        with f['factory']() as observer:
                            blockers = observer.execute('SELECT pg_blocking_pids(%s) AS blockers', (reader_pids[0],)).fetchone()['blockers']
                            blocked = holder.info.backend_pid in blockers
                    if blocked:
                        break
                    await asyncio.sleep(.02)
                assert blocked, 'The real RR reader must wait at the held membership SHARE fence'
                holder.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=82", (A,))
                holder.commit()
                status, result = await asyncio.wait_for(request, 8)
                assert status == 403 and result['error'] == 'task_forbidden', result
                assert len(reader_pids) == 2 and len(set(reader_pids)) == 2
                assert 'tasks' not in result and 'task' not in result and 'command' not in result
            finally:
                holder.rollback()
                if not request.done():
                    request.cancel()
    try:
        asyncio.run(scenario())
    finally:
        holder.close()
    assert f['provider'].calls == []
