"""Native PostgreSQL draft races; disposable localhost DB, synthetic AI only."""
import json
import multiprocessing
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A, CA, AGENT, PROFILE
import tenant_dialog_consultations as consultations


def child(dsn, data, behavior, barrier, results, dispatched=None, release=None):
    """Independent processes exercise the real admission ledger and locks."""
    import requests
    import tenant_developer_question_draft as draft
    import tenant_operator_copilot as copilot
    import tenant_episode_media as media
    import tenant_chat_ai_schema as chat_schema
    import tenant_ai_agents_schema as agents_schema
    from tenant_linear_policy import TenantLinearError

    factory = lambda: psycopg.connect(dsn, row_factory=dict_row)
    for module in (draft, copilot, media, consultations):
        module._connect = factory
    chat_schema._ready = True
    agents_schema._ready = True
    copilot._fernet = lambda: SimpleNamespace(decrypt=lambda _: json.dumps({
        'tenant_id': A, 'provider': 'chat_ai', 'api_key': 'synthetic-test-key'
    }).encode())

    def forbidden(*args, **kwargs):
        raise AssertionError('External HTTP is forbidden in the native draft tests')
    requests.sessions.Session.request = forbidden

    def generate(*args, **kwargs):
        # The real provider invokes this after serialization, before dispatch.
        kwargs['before_dispatch']({'synthetic': True})
        with factory() as conn:
            conn.execute('INSERT INTO test_external_sends VALUES(%s,%s)',
                         ('mock-developer-question-ai', data['request_id']))
        if dispatched is not None:
            dispatched.set()
        if release is not None:
            assert release.wait(20), 'Timed out waiting for test source mutation'
        if behavior == 'timeout':
            raise requests.Timeout('Synthetic uncertain provider outcome')
        return 'Which saved diagnostic should we check next?', {'total_tokens': 10}
    copilot.generate = generate
    try:
        barrier.wait(20)
        value = draft.question(PROFILE, data)
        results.put(('ok', value))
    except TenantLinearError as error:
        results.put(('error', error.status, str(error)))
    except Exception as error:
        results.put(('unexpected', type(error).__name__, str(error)))


def run_processes(db, operations, during_dispatch=None):
    ctx = multiprocessing.get_context('spawn')
    barrier = ctx.Barrier(len(operations))
    results = ctx.Queue()
    dispatched = ctx.Event() if during_dispatch else None
    release = ctx.Event() if during_dispatch else None
    workers = [ctx.Process(target=child, args=(
        db.dsn, data, behavior, barrier, results, dispatched, release
    )) for behavior, data in operations]
    for worker in workers:
        worker.start()
    try:
        if during_dispatch:
            assert dispatched.wait(30), 'Mock AI did not reach dispatch'
            during_dispatch()
            release.set()
        values = [results.get(timeout=40) for _ in workers]
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        assert all(value[0] != 'unexpected' for value in values), values
        return values
    finally:
        if release is not None:
            release.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        results.close()
        results.join_thread()


@pytest.fixture
def prepared(database, monkeypatch):
    db = database
    db.execute('TRUNCATE saas_tenants CASCADE')
    db.execute('TRUNCATE test_external_sends')
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Company','active',11)", (A, A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')", (A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')", (A, CA))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'client',41,'Test',now())", (A, CA))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,model) VALUES(%s::uuid,%s::uuid,'Support',11,TRUE,'agent-model')", (A, AGENT))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'client',%s::uuid,'human')", (A, CA, AGENT))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)", (A, b'synthetic-cipher', json.dumps({
        'enabled': True, 'consent': True, 'copilot_enabled': True, 'daily_limit': 100
    })))
    message = str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,'source',%s,'incoming','Original problem')", (message, A, 'connect:' + CA))
    db.commit()
    monkeypatch.setattr(consultations, '_connect', lambda: psycopg.connect(db.dsn, row_factory=dict_row))
    preview = consultations.preview(PROFILE, {'channel_id': CA, 'conversation_id': 'client'})
    versions = {row['id']: row['source_version'] for row in preview['messages']}
    assert set(versions) == {message}
    return db, {'request_id': str(uuid.uuid4()), 'channel_id': CA,
                'conversation_id': 'client', 'message_ids': [message], 'media_ids': [],
                'source_versions': versions, 'summary': 'Please help diagnose this issue.'}


def assert_private_only(db):
    for table in ('saas_internal_consultations', 'saas_consultation_group_deliveries',
                  'saas_public_case_shares', 'saas_operator_copilot_threads',
                  'saas_ai_agent_jobs', 'saas_support_tickets'):
        assert db.execute('SELECT count(*) AS n FROM ' + table)[0]['n'] == 0, table
    db.commit()


def attempts(db):
    rows = db.execute('SELECT * FROM test_external_sends')
    db.commit()
    assert all(row['channel'] == 'mock-developer-question-ai' for row in rows)
    return len(rows)


def test_concurrent_distinct_client_ids_reserve_one_question_and_reuse_cache(prepared):
    db, data = prepared
    second = {**data, 'request_id': str(uuid.uuid4())}
    values = run_processes(db, [('success', data), ('success', second)])
    successes = [value[1] for value in values if value[0] == 'ok']
    assert successes, values
    assert all(value[0] == 'ok' or value[:2] == ('error', 409) for value in values), values
    assert attempts(db) == 1
    cached = run_processes(db, [('success', {**data, 'request_id': str(uuid.uuid4())})])
    assert cached == [('ok', successes[0])]
    assert attempts(db) == 1
    runs = db.execute('SELECT request_id,status,result,reserved_calls FROM saas_chat_ai_runs')
    assert len(runs) == 1 and runs[0]['status'] == 'completed'
    assert runs[0]['reserved_calls'] == 1
    assert runs[0]['result']['developer_question_draft'] is True
    assert runs[0]['result']['question_binding']['selection']['source_versions'] == data['source_versions']
    assert str(runs[0]['request_id']) not in {data['request_id'], second['request_id']}
    assert_private_only(db)


def test_unknown_timeout_cannot_be_repurchased_with_new_client_uuid(prepared):
    db, data = prepared
    assert run_processes(db, [('timeout', data)])[0][:2] == ('error', 503)
    retry = {**data, 'request_id': str(uuid.uuid4())}
    assert run_processes(db, [('success', retry)])[0][:2] == ('error', 409)
    assert attempts(db) == 1
    runs = db.execute('SELECT status,result,reserved_calls FROM saas_chat_ai_runs')
    assert len(runs) == 1 and runs[0]['status'] == 'unknown'
    assert not runs[0]['result'] and runs[0]['reserved_calls'] == 1
    assert_private_only(db)


def test_source_edited_in_another_connection_during_provider_call_hides_result(prepared):
    db, data = prepared
    def edit_source():
        with psycopg.connect(db.dsn) as conn:
            conn.execute('UPDATE saas_crm_messages SET body=%s WHERE id=%s::uuid',
                         ('Changed while AI was running', data['message_ids'][0]))
    values = run_processes(db, [('success', data)], during_dispatch=edit_source)
    assert values[0][:2] == ('error', 409), values
    assert attempts(db) == 1
    retry = {**data, 'request_id': str(uuid.uuid4())}
    assert run_processes(db, [('success', retry)])[0][:2] == ('error', 409)
    assert attempts(db) == 1
    runs = db.execute('SELECT status,result FROM saas_chat_ai_runs')
    assert len(runs) == 1 and runs[0]['status'] != 'completed'
    assert not runs[0]['result']
    assert_private_only(db)


def dispatch_child(dsn, consultation_id, request_id, stage, reached, release, results):
    import requests
    import tenant_developer_question_draft as draft
    import tenant_connect_transport as transport
    from tenant_linear_policy import TenantLinearError
    factory = lambda: psycopg.connect(dsn, row_factory=dict_row)
    for module in (draft, consultations, transport):
        module._connect = factory
    def forbidden(*args, **kwargs):
        raise AssertionError('External HTTP is forbidden in native dispatch tests')
    requests.sessions.Session.request = forbidden
    def pause():
        reached.set()
        assert release.wait(20), 'Timed out waiting for concurrent source edit'
    def route(cur, tenant, lead, **kwargs):
        if stage == 'before_send':
            pause()
        return {'channel_id': CA, 'conversation_id': 'developer', 'api_key': 'synthetic'}
    def send(route, text, request):
        if stage == 'during_send':
            pause()
        with factory() as conn:
            conn.execute('INSERT INTO test_external_sends VALUES(%s,%s)', ('mock-developer-send', request))
        return {'message_id': 'mock-message', 'external_message_id': 'MOCK_EXTERNAL'}
    transport.connect_route = route
    transport.send_consultation_with_receipt = send
    try:
        results.put(('ok', consultations.dispatch(PROFILE, consultation_id, request_id)))
    except TenantLinearError as error:
        results.put(('error', error.status, str(error)))
    except Exception as error:
        results.put(('unexpected', type(error).__name__, str(error)))


@pytest.mark.parametrize('stage', ['before_send', 'during_send'])
def test_final_dispatch_revalidates_and_holds_selected_message(prepared, monkeypatch, stage):
    db, data = prepared
    draft_result = run_processes(db, [('success', data)])[0]
    assert draft_result[0] == 'ok', draft_result
    import tenant_developer_question_draft as draft
    monkeypatch.setattr(draft, '_connect', lambda: psycopg.connect(db.dsn, row_factory=dict_row))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'developer',99,'Developer',now())", (A, CA))
    db.commit()
    consultations.developer_marker(PROFILE, {'channel_id': CA, 'conversation_id': 'developer', 'enabled': True})
    command = {**data, 'request_id': str(uuid.uuid4()), 'question_draft_request_id': draft_result[1]['request_id'],
               'destination_channel_id': CA, 'destination_conversation_id': 'developer',
               'summary': 'Employee reviewed question', 'confirm': True, 'reuse_existing': True}
    prepared_send = consultations.prepare(PROFILE, command, 'https://crm.test')
    ctx = multiprocessing.get_context('spawn')
    reached, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    worker = ctx.Process(target=dispatch_child, args=(db.dsn, prepared_send['id'], command['request_id'], stage, reached, release, results))
    worker.start()
    try:
        assert reached.wait(30), 'Dispatch did not reach the controlled boundary'
        # A raw second connection proves row locking, not only cooperative advisory locking.
        with psycopg.connect(db.dsn) as mutation:
            mutation.execute("SET LOCAL lock_timeout = '1000ms'")
            if stage == 'during_send':
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    mutation.execute('UPDATE saas_crm_messages SET body=%s WHERE id=%s::uuid', ('Changed at dispatch', data['message_ids'][0]))
                mutation.rollback()
            else:
                mutation.execute('UPDATE saas_crm_messages SET body=%s WHERE id=%s::uuid', ('Changed before final validation', data['message_ids'][0]))
        release.set()
        outcome = results.get(timeout=30)
        worker.join(10)
        assert worker.exitcode == 0
        sends = db.execute("SELECT count(*) AS n FROM test_external_sends WHERE channel='mock-developer-send'")[0]['n']
        db.commit()
        if stage == 'before_send':
            assert outcome[0] == 'ok' and outcome[1]['state'] == 'blocked', outcome
            assert sends == 0
        else:
            assert outcome[0] == 'ok' and outcome[1]['state'] == 'sent', outcome
            assert sends == 1
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        results.close()
        results.join_thread()


def group_retry_child(dsn, consultation_id, request_id, name, reached, release, results):
    import requests
    import tenant_developer_question_draft as draft
    import tenant_connect_transport as transport
    from tenant_linear_policy import TenantLinearError
    def factory():
        conn = psycopg.connect(dsn, row_factory=dict_row, application_name=name)
        conn.execute("SET statement_timeout='12000ms'")
        conn.execute("SET lock_timeout='8000ms'")
        return conn
    for module in (draft, consultations, transport):
        module._connect = factory
    def forbidden(*args, **kwargs):
        raise AssertionError('External HTTP is forbidden in group retry tests')
    requests.sessions.Session.request = forbidden
    original = consultations._check_locked_question_dispatch
    count = 0
    def check_locked(*args, **kwargs):
        nonlocal count
        count += 1
        if name == 'draft-group-first' and count == 2:
            # Pause after phase-two route locks, before its consultation/delivery row locks.
            reached.set()
            assert release.wait(20), 'Concurrent retry did not enter its lock wait'
        return original(*args, **kwargs)
    consultations._check_locked_question_dispatch = check_locked
    transport.connect_route = lambda *args, **kwargs: {'channel_id': CA, 'conversation_id': 'developer', 'api_key': 'synthetic'}
    def send(route, text, request):
        with factory() as conn:
            conn.execute('INSERT INTO test_external_sends VALUES(%s,%s)', ('mock-group-developer-send', request))
        return {'message_id': 'mock-group-message', 'external_message_id': 'MOCK_GROUP_EXTERNAL'}
    transport.send_consultation_with_receipt = send
    try:
        results.put((name, 'ok', consultations.dispatch(PROFILE, consultation_id, request_id)))
    except TenantLinearError as error:
        results.put((name, 'error', error.status, str(error)))
    except Exception as error:
        results.put((name, 'unexpected', type(error).__name__, str(error)))


@pytest.mark.parametrize('invalid_request', [False, True])
def test_concurrent_group_source_retries_share_phase_lock_order_without_case(prepared, monkeypatch, invalid_request):
    import time
    db, data = prepared
    group = '120363499999000001@g.us'
    db.execute("UPDATE saas_connect_conversations SET conversation_id=%s,conversation_kind='group' WHERE tenant_id=%s::uuid AND lead_id=41", (group, A))
    db.execute("UPDATE saas_ai_conversation_state SET conversation_id=%s WHERE tenant_id=%s::uuid", (group, A))
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid', (json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]), A))
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,assignments) VALUES(%s::uuid,%s,'human',%s::jsonb)", (A, group, json.dumps([{'agent_id':AGENT,'channel_id':CA,'priority':0}])))
    db.commit()
    data = {**data, 'conversation_id': group}
    result = run_processes(db, [('success', data)])[0]
    assert result[0] == 'ok', result
    import tenant_developer_question_draft as draft
    monkeypatch.setattr(draft, '_connect', lambda: psycopg.connect(db.dsn, row_factory=dict_row))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'developer',99,'Developer',now())", (A, CA))
    db.commit()
    consultations.developer_marker(PROFILE, {'channel_id':CA, 'conversation_id':'developer', 'enabled':True})
    command = {**data, 'request_id':str(uuid.uuid4()), 'question_draft_request_id':result[1]['request_id'],
               'destination_channel_id':CA, 'destination_conversation_id':'developer', 'summary':'Reviewed group question', 'confirm':True, 'reuse_existing':True}
    prepared_send = consultations.prepare(PROFILE, command, 'https://crm.test')
    assert db.execute('SELECT case_id FROM saas_internal_consultations')[0]['case_id'] is None
    db.commit()
    ctx = multiprocessing.get_context('spawn')
    reached, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    names = ['draft-group-first', 'draft-group-retry']
    workers = [ctx.Process(target=group_retry_child, args=(db.dsn, prepared_send['id'], str(uuid.uuid4()) if invalid_request and name == names[1] else command['request_id'], name, reached, release, results)) for name in names]
    workers[0].start()
    try:
        assert reached.wait(30), 'First dispatch did not reach its second phase'
        workers[1].start()
        outcomes = []
        if invalid_request:
            # A nonexistent delivery must fail before waiting on the source group
            # or holding the consultation row needed by the first sender.
            rejected = results.get(timeout=10)
            assert rejected[0] == names[1] and rejected[1:3] == ('error',404), rejected
            outcomes.append(rejected)
        else:
            deadline = time.monotonic() + 12
            waiting = False
            while time.monotonic() < deadline:
                with psycopg.connect(db.dsn) as observer:
                    waiting = bool(observer.execute("SELECT 1 FROM pg_stat_activity WHERE application_name='draft-group-retry' AND wait_event_type='Lock'").fetchone())
                if waiting:
                    break
                time.sleep(.02)
            assert waiting, 'Retry never reached the competing database lock'
        release.set()
        outcomes.extend(results.get(timeout=25) for _ in range(2-len(outcomes)))
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        first = next(value for value in outcomes if value[0] == names[0])
        retry = next(value for value in outcomes if value[0] == names[1])
        assert first[1] == 'ok' and first[2]['state'] == 'sent', outcomes
        assert (retry[1] == 'ok' and retry[2]['state'] == 'sent') or retry[1:3] == ('error',404 if invalid_request else 409), outcomes
        assert db.execute("SELECT count(*) AS n FROM test_external_sends WHERE channel='mock-group-developer-send'")[0]['n'] == 1
        assert db.execute('SELECT state FROM saas_consultation_group_deliveries')[0]['state'] == 'sent'
        db.commit()
    finally:
        release.set()
        for worker in workers:
            if worker.pid and worker.is_alive():
                worker.terminate()
                worker.join(5)
        results.close()
        results.join_thread()


def route_drift_child(dsn, consultation_id, request_id, phase, reached, release, results):
    import requests
    import tenant_developer_question_draft as draft
    import tenant_connect_transport as transport
    import tenant_ai_group_coordination as groups
    from tenant_linear_policy import TenantLinearError
    factory = lambda: psycopg.connect(dsn, row_factory=dict_row)
    for module in (draft, consultations, transport):
        module._connect = factory
    def forbidden(*args, **kwargs):
        raise AssertionError('Provider must not be called for route drift')
    requests.sessions.Session.request = forbidden
    transport.send_consultation_with_receipt = forbidden
    transport.connect_route = lambda *args, **kwargs: {'channel_id':CA,'conversation_id':'developer','api_key':'synthetic'}
    original_locks, original_consult, original_group = consultations._lock_dispatch_routes, consultations._consult, groups.lock_group
    phases, consults, locked_groups = 0, 0, []
    def locks(*args, **kwargs):
        nonlocal phases
        phases += 1
        if phases == phase:
            reached.set()
            assert release.wait(20)
        return original_locks(*args, **kwargs)
    def consult(*args, **kwargs):
        nonlocal consults
        consults += 1
        assert consults < phase, 'Drifted route reached assignment/scope resolution before rejection'
        return original_consult(*args, **kwargs)
    def lock_group(cur, tenant, group):
        locked_groups.append(group)
        return original_group(cur, tenant, group)
    consultations._lock_dispatch_routes, consultations._consult, groups.lock_group = locks, consult, lock_group
    try:
        result = consultations.dispatch(PROFILE, consultation_id, request_id)
        results.put(('ok', result, consults, locked_groups))
    except TenantLinearError as error:
        results.put(('error', error.status, str(error), consults, locked_groups))
    except Exception as error:
        results.put(('unexpected', type(error).__name__, str(error)))


@pytest.mark.parametrize('phase', [1, 2])
def test_route_drift_rejected_before_assignment_or_new_group_lock(prepared, monkeypatch, phase):
    db, data = prepared
    generated = run_processes(db, [('success', data)])[0]
    assert generated[0] == 'ok', generated
    import tenant_developer_question_draft as draft
    monkeypatch.setattr(draft, '_connect', lambda: psycopg.connect(db.dsn, row_factory=dict_row))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'developer',99,'Developer',now())", (A,CA))
    db.commit()
    consultations.developer_marker(PROFILE, {'channel_id':CA,'conversation_id':'developer','enabled':True})
    command = {**data,'request_id':str(uuid.uuid4()),'question_draft_request_id':generated[1]['request_id'],
               'destination_channel_id':CA,'destination_conversation_id':'developer','summary':'Reviewed question','confirm':True,'reuse_existing':True}
    planned = consultations.prepare(PROFILE, command, 'https://crm.test')
    ctx = multiprocessing.get_context('spawn')
    reached, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    worker = ctx.Process(target=route_drift_child, args=(db.dsn, planned['id'], command['request_id'], phase, reached, release, results))
    worker.start()
    try:
        assert reached.wait(30)
        changed_group = '120363499999000099@g.us'
        with psycopg.connect(db.dsn) as mutation:
            mutation.execute("SET LOCAL lock_timeout='3000ms'")
            mutation.execute('UPDATE saas_internal_consultations SET conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid', (changed_group,A,planned['id']))
        release.set()
        outcome = results.get(timeout=25)
        worker.join(10)
        assert worker.exitcode == 0
        if phase == 1:
            assert outcome[:2] == ('error',409), outcome
            assert outcome[3] == 0, outcome
        else:
            assert outcome[0] == 'ok' and outcome[1]['state'] == 'blocked', outcome
            assert outcome[2] == 1, outcome
            assert 'Маршрут' in outcome[1]['error'], outcome
        assert changed_group not in outcome[-1], outcome
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate(); worker.join(5)
        results.close(); results.join_thread()
