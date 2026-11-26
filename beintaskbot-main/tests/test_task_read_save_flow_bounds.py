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
            assert status == 200 and personal['scope'] == 'kommo'
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
            assert result['tasks'] == []
            assert result['shown'] == 0 and result['has_more'] is False
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
                holder.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=82", (A,))
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

def test_customer_search_before_limit_on_both_feeds_and_status(task_flow):
    from test_task_read_save_flow_postgres import CONTACT
    from tenant_crm_native_deals import list_deals
    from tenant_crm_repository import CRMRepository
    import json
    f = task_flow
    company = str(uuid.uuid4())
    native = f['native']['a']['id']
    _seed_external(f, 205)
    _seed_native(f, 105)
    with f['factory']() as conn:
        conn.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s,%s,'Jasmine Works')", (company,A))
        conn.execute("UPDATE saas_crm_native_contacts SET phone='+994 (50) 123-45-67', display_name='Leyla Test' WHERE id=%s", (CONTACT,))
        conn.execute("UPDATE saas_crm_native_deals SET company_id=%s WHERE id=%s", (company,DEALS[0]))
        conn.execute("UPDATE saas_crm_native_tasks SET status='completed', contact_id=%s WHERE id=%s", (CONTACT,native))
        conn.execute("UPDATE saas_crm_tasks SET completed=TRUE WHERE tenant_id=%s AND kommo_task_id=501", (A,))
        conn.execute("UPDATE saas_crm_deals SET contact_name='Leyla Test',phone='0501234567',raw=%s::jsonb WHERE tenant_id=%s AND kommo_lead_id=101",
                     (json.dumps({'_embedded':{'companies':[{'id':9,'name':'Jasmine Works'}]}}),A))
        # Give the target a deterministic position outside the newest 50 deals.
        conn.execute("""INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,responsible_id,updated_at)
            SELECT gen_random_uuid(),%s::uuid,'Other ' || n,'10','101',11,TIMESTAMPTZ '2090-01-01' + n * interval '1 second'
            FROM generate_series(1,54) AS n""", (A,))
    first_page = list_deals(A, actor_id=11)
    assert len(first_page) == 50
    assert str(DEALS[0]) not in {str(row['id']) for row in first_page}
    repo = CRMRepository(f['factory'], lambda conn: None)
    cookie = f['session'].issue(A,11)
    terms = ['+994 50 123-45-67','050 1234567','123-45','eYLa','sMiNe']
    for term in terms:
        assert str(DEALS[0]) in {str(row['id']) for row in list_deals(A, term, actor_id=11)}
        rows,_ = repo.list_deals(tenant_id=A,search=term)
        assert 101 in {row['kommo_lead_id'] for row in rows}
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for source, target in [('native',str(native)),('kommo','501')]:
                for term in terms:
                    status,page=await _get(http,cookie,source,extra={'search':term,'status':'completed'})
                    assert status==200 and target in {r['id'] for r in page['tasks']}, (source, term, page)
                    assert all(r['status']=='completed' for r in page['tasks'])
            # A marker grants access to a task, but not to its linked lead.
            other=f['session'].issue(A,83)
            status,page=await _get(http,other,'kommo',actor=83,extra={'search':'Jasmine'})
            assert status==200 and not page['tasks']
    asyncio.run(scenario())

@pytest.mark.parametrize('resource', ['leads','tasks'])
def test_inflight_sync_cannot_restore_previous_stage_or_status(task_flow, resource, monkeypatch):
    import tenant_crm_sync_store as sync
    import tenant_crm_store as crm
    f=task_flow
    monkeypatch.setattr(crm,'_connect',f['factory'])
    sync.enqueue(A,resource)
    store=sync.SyncPageStore.claim()
    try:
        assert store.row['resource']==resource and store.row['page_started_at']
        # The page is in flight. A confirmed write commits in another connection.
        with f['factory']() as conn:
            if resource=='leads':
                conn.execute("UPDATE saas_crm_deals SET status_id=102,source_updated_at=to_timestamp(1000),synced_at=clock_timestamp() WHERE tenant_id=%s AND kommo_lead_id=101",(A,))
            else:
                conn.execute("UPDATE saas_crm_tasks SET completed=TRUE,synced_at=clock_timestamp() WHERE tenant_id=%s AND kommo_task_id=501",(A,))
        if resource=='leads':
            crm.update_chat_preview(tenant_id=A,lead_id=101,body='Synthetic preview',happened_at='2030-01-01T12:00:00Z',channel='fixture')
            page=[{'kommo_lead_id':101,'pipeline_id':10,'status_id':101,'source_updated_at':'1970-01-01T00:16:40Z'}]
        else:
            page=[{'kommo_task_id':501,'completed':False,'raw':{'id':501,'updated_at':1000}}]
        store.save_page(page,done=False)
        with f['factory']() as conn:
            if resource=='leads':
                record=conn.execute("SELECT status_id,last_message FROM saas_crm_deals WHERE tenant_id=%s AND kommo_lead_id=101",(A,)).fetchone()
                assert record=={'status_id':102,'last_message':'Synthetic preview'}
            else:
                assert conn.execute("SELECT completed FROM saas_crm_tasks WHERE tenant_id=%s AND kommo_task_id=501",(A,)).fetchone()['completed']
    finally:
        store.close()


def test_verified_kommo_stage_round_trip_with_real_cache_and_sync(task_flow):
    import json
    from tenant_crm_import_provenance import observe_page
    from tenant_crm_sync_data import normalize_page
    from tenant_crm_sync_store import enqueue, SyncPageStore
    import tenant_kommo_board_provenance as proof
    import tenant_kommo_deal_move as moves
    from tenant_crm_repository import CRMRepository

    f = task_flow
    remote = {'id': 101, 'pipeline_id': 10, 'status_id': 101, 'updated_at': 1000}
    calls = []
    with f['factory']() as conn, conn.cursor() as cur:
        proof.migrate(cur)
        cur.execute("UPDATE saas_tenant_pipeline_stages SET settings='{}' WHERE tenant_id=%s AND stage_id='102'", (A,))
        cur.execute("UPDATE saas_crm_deals SET source_updated_at=to_timestamp(1000),raw=%s::jsonb WHERE tenant_id=%s AND kommo_lead_id=101",
                    (json.dumps(remote), A))
        cur.execute("SELECT provider,account_domain,connected_at FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='kommo'", (A,))
        credentials = cur.fetchone()
        def observation():
            return observe_page(A, 'leads', credentials, {'_embedded': {'leads': [remote]}}).observation
        proof.record(cur, A, 'leads', [{'raw': remote}], observation())
        revision = proof.revisions(cur, A, 'leads', [101])['101']
    async def provider(binding, method, path, **kw):
        assert binding.tenant_id == str(A) and path == 'leads/101'
        calls.append(method)
        if method == 'PATCH':
            remote.update(**kw['json_body'], updated_at=1001)
        return dict(remote)

    command = dict(request_id=str(uuid.uuid4()), source_revision=revision, lead_id=101,
                   expected_pipeline_id=10, expected_status_id=101, expected_updated_at=1000, stage_id=102)
    receipt = asyncio.run(moves.move({'tenant_id':A, 'telegram_id':11}, command, provider))
    assert calls == ['GET', 'PATCH', 'GET']
    assert receipt['state'] == 'confirmed' and receipt['status_id'] == remote['status_id'] == 102
    enqueue(A, 'leads')
    store = SyncPageStore.claim()
    try:
        store.save_page(normalize_page('leads', [dict(remote)], {102:'Next'}), done=True, observation=observation())
    finally:
        store.close()
    rows, _ = CRMRepository(f['factory'], lambda conn: None).list_deals(tenant_id=A)
    assert next(row for row in rows if row['kommo_lead_id']==101)['status_id'] == receipt['status_id']

    # Earlier releases could apply a PATCH and leave a pending receipt. After a
    # later, independently synced return to the source, verify without another write.
    from tenant_task_flow import TaskFlowError
    ambiguous = {**command, 'request_id': str(uuid.uuid4()), 'source_revision': receipt['source_revision'],
                 'expected_status_id':102, 'expected_updated_at':1001, 'stage_id':101}
    async def lose_response(binding, method, path, **kw):
        result = await provider(binding, method, path, **kw)
        if method == 'PATCH':
            raise TimeoutError('synthetic lost response')
        return result
    # Sync can change the revision even when the stage stays the same.
    with f['factory']() as conn, conn.cursor() as cur:
        ambiguous['source_revision'] = proof.revisions(cur, A, 'leads', [101])['101']
    with pytest.raises(TaskFlowError, match='board_outcome_unknown'):
        asyncio.run(moves.move({'tenant_id':A,'telegram_id':11}, ambiguous, lose_response))
    remote.update(status_id=102, updated_at=1002)
    deletion_page = SyncPageStore.claim()
    try:
        assert deletion_page.row['phase'] == 'deleted'
        deletion_page.save_page([], done=True)
    finally:
        deletion_page.close()
    enqueue(A, 'leads', force_full=True)
    store = SyncPageStore.claim()
    try:
        store.save_page(normalize_page('leads',[dict(remote)],{102:'Next'}),done=True,observation=observation())
    finally:
        store.close()
    patch_count = calls.count('PATCH')
    with pytest.raises(TaskFlowError, match='board_changed_since_confirmation') as changed:
        asyncio.run(moves.move({'tenant_id':A,'telegram_id':11}, ambiguous, provider))
    assert changed.value.keep_request is False and calls.count('PATCH') == patch_count
