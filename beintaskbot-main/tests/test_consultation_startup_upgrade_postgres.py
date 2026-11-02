"""Exact #467 -> current startup upgrade on disposable native PostgreSQL only.

No application database URL or HTTP/provider transport is available. The old
release is extracted from its exact Git object, never simulated with fresh DDL.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import uuid

from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
BASELINE = '972528e30b9750d8b121f55a89d8b21aa20ddf5b'
TENANT, CHANNEL, CASE, CONSULT, REQUEST, SOURCE = [str(uuid.UUID(int=n))
    for n in (101, 201, 46701, 46702, 46703, 46704)]
PROFILE = {'tenant_id': TENANT, 'telegram_id': 11}
HISTORICAL = 'Historical #467 developer reply; no fresh follow-up intent'


def _output(**values):
    print(json.dumps(values, sort_keys=True), flush=True)


def _local_dsn(dsn):
    options = conninfo_to_dict(dsn)
    assert options.get('host') in {'127.0.0.1', 'localhost', '::1'}, 'Disposable loopback PostgreSQL only'
    assert not options.get('service'), 'PostgreSQL service aliases are forbidden'
    if options.get('hostaddr'):
        assert options['hostaddr'] in {'127.0.0.1', '::1'}
    return options


def _runtime(dsn, *, connection_factory=None):
    options = _local_dsn(dsn)
    assert options.get('dbname', '').startswith('test_channel_')
    assert os.environ.get('DATABASE_URL') == 'postgresql://invalid.invalid/disabled'
    import requests

    def forbidden(*_args, **_kwargs):
        raise AssertionError('External HTTP/provider/client/developer transport is forbidden')

    requests.sessions.Session.request = forbidden
    def native_connection():
        conn = psycopg.connect(dsn, row_factory=dict_row)
        event_dir = os.environ.get('CONSULTATION_UPGRADE_EVENTS')
        if event_dir:
            target = Path(event_dir) / 'connected.json'
            if not target.exists():
                target.write_text(json.dumps({'pid': conn.info.backend_pid}))
        return conn
    factory = connection_factory or native_connection
    import tenant_platform_core as core
    core._connect = factory
    import tenant_dialog_consultations as flow
    import tenant_dialog_consultations_worker as worker
    import tenant_notification_center as notifications
    for module in (flow, worker, notifications):
        module._connect = factory
    return factory, flow, worker, notifications


def _columns(conn):
    return [dict(row) for row in conn.execute("""SELECT table_name,column_name,data_type,is_nullable
      FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position""")]


def _state(factory):
    with factory() as conn:
        rows = [dict(row) for row in conn.execute("""SELECT id::text,message_id,body,draft_state,
          followup_requested_at FROM saas_dialog_consultation_replies ORDER BY message_id""")]
        return {'rows': rows,
            'jobs': conn.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs').fetchone()['n'],
            'intents': conn.execute('SELECT count(*) AS n FROM saas_dialog_consultation_replies WHERE followup_requested_at IS NOT NULL').fetchone()['n']}


def _seed_old(factory, flow):
    # Invoke the previous release's real feature bootstrap and reply writer.
    with factory() as conn:
        flow.schema(conn)
    with factory() as conn:
        conn.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,'isolated-upgrade','Synthetic upgrade tenant','active',11)", (TENANT,))
        conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Synthetic owner')", (TENANT,))
        conn.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Synthetic channel')", (TENANT, CHANNEL))
        for conversation, lead in [('client', 41), ('developer', 91)]:
            conn.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,%s,%s)", (TENANT, CHANNEL, conversation, lead, 'Synthetic ' + conversation))
        conn.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type) VALUES(%s::uuid,%s::uuid,41,'connect:old-customer-issue','incoming',%s,'Original #467 incident','text')", (SOURCE, TENANT, 'connect:' + CHANNEL))
        conn.execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,reason,status,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','manual','Historical case','new',11)", (TENANT, CASE, CHANNEL))
        conn.execute("INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client','old-request','Historical developer consultation','waiting_specialist',11)", (TENANT, CONSULT, CASE, CHANNEL))
        conn.execute("INSERT INTO saas_dialog_developer_markers(tenant_id,channel_id,conversation_id,enabled,updated_by) VALUES(%s::uuid,%s::uuid,'developer',TRUE,11)", (TENANT, CHANNEL))
        conn.execute("INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,connect_message_id,provider_message_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,'developer','Historical request',%s,'sent',11,'old-queue','OLD_PROVIDER_RECEIPT')", (TENANT, CONSULT, REQUEST, CASE, CHANNEL, hashlib.sha256(b'Historical request').hexdigest()))
        conn.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw,author_name) VALUES(%s::uuid,%s::uuid,91,'connect:old-reply','incoming',%s,%s,'text',%s::jsonb,'Synthetic developer')", (uuid.uuid4(), TENANT, 'connect:' + CHANNEL, HISTORICAL, json.dumps({'id': 'old-reply', 'quotedExternalMessageId': 'OLD_PROVIDER_RECEIPT'})))
        with conn.cursor() as cur:
            reply = flow.record_reply(cur, TENANT, CHANNEL, 'developer', 'old-reply', HISTORICAL,
                'OLD_PROVIDER_RECEIPT', 'Synthetic developer', source_external_id='connect:old-reply')
        assert reply
    with factory() as conn:
        assert not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='saas_dialog_consultation_replies' AND column_name='followup_requested_at'").fetchone()
        _output(phase='baseline', baseline=BASELINE, reply_id=reply, columns=_columns(conn))


def _phase(phase):
    dsn = os.environ['CONSULTATION_UPGRADE_TEST_DSN']
    factory, flow, worker, notifications = _runtime(dsn)
    import tenant_ai_agent_startup as startup
    if phase == 'baseline':
        startup.prepare_database()
        _seed_old(factory, flow)
        return
    if phase == 'rollback':
        # This process imports only the exact archived #467 application.
        thread = flow.inbox(PROFILE, case_id=CASE)
        assert any(reply['body'] == HISTORICAL for reply in thread['replies'])
        with factory() as conn:
            with conn.cursor() as cur:
                inserted = flow.record_reply(cur, TENANT, CHANNEL, 'developer', 'rollback-reply',
                    'Old code can still append a reply', 'OLD_PROVIDER_RECEIPT', 'Synthetic developer')
                assert inserted
                assert flow.record_reply(cur, TENANT, CHANNEL, 'developer', 'rollback-reply',
                    'Old code can still append a reply', 'OLD_PROVIDER_RECEIPT', 'Synthetic developer') is None
        assert worker.reconcile() == 0
        _output(phase=phase, old_read=True, old_upsert_id=inserted)
        return
    if phase == 'spoof':
        from tenant_linear_policy import TenantLinearError
        with factory() as conn:
            conn.execute('CREATE SCHEMA upgrade_decoy')
            conn.execute('CREATE TABLE upgrade_decoy.saas_dialog_consultation_replies (LIKE public.saas_dialog_consultation_replies INCLUDING ALL)')
            conn.execute('ALTER TABLE upgrade_decoy.saas_dialog_consultation_replies ADD COLUMN followup_requested_at TIMESTAMPTZ')
            conn.execute('CREATE TABLE upgrade_decoy.saas_public_case_shares (LIKE public.saas_public_case_shares INCLUDING ALL)')
            conn.execute("ALTER TABLE upgrade_decoy.saas_public_case_shares ADD COLUMN public_identity JSONB NOT NULL DEFAULT '{}'::jsonb")
            conn.execute('SET LOCAL search_path=public,upgrade_decoy')
            try:
                flow.require_schema(conn)
            except TenantLinearError as error:
                assert error.status == 503
            else:
                raise AssertionError('Other-schema columns/constraints cannot certify public readiness')
        startup.prepare_database()
        with factory() as conn:
            flow.require_schema(conn)
        _output(phase=phase, ready_after_real_bootstrap=True)
        return
    if phase == 'cold_business':
        _cold_business_paths(dsn, flow, worker)
        return
    if phase == 'cold':
        _cold_startup(dsn)
        return
    if phase in {'retry_recovers', 'retry_exhausted'}:
        _native_retry(factory, startup, flow, phase)
        return
    if phase == 'queue_gate':
        import asyncio
        import logging
        event_dir = Path(os.environ['CONSULTATION_UPGRADE_EVENTS'])

        async def actual_queue_entry(*_args):
            with factory() as conn:
                flow.require_schema(conn)
            (event_dir / 'worker-started.json').write_text(json.dumps({'ready': True}))

        async def launch():
            app = {}
            await startup.start_worker(app, actual_queue_entry, None, logging.getLogger(__name__), None,
                prepare_database_first=True, prepare=startup.prepare_database)
            await app['tenant_ai_agent_worker']

        asyncio.run(launch())
        _output(phase=phase, worker_started_after_ready=True)
        return
    startup.prepare_database()
    if phase == 'business':
        _business_reads(dsn, flow, worker, notifications)
        return
    with factory() as conn:
        flow.require_schema(conn)
        columns = _columns(conn)
    assert worker.reconcile() == 0
    assert worker.reconcile() == 0
    thread = flow.inbox(PROFILE, case_id=CASE)
    assert any(reply['body'] == HISTORICAL for reply in thread['replies'])
    notices = notifications.listing(PROFILE)
    assert notices['unread_count'] == 1
    _output(phase=phase, columns=columns, notifications=notices['unread_count'], **_state(factory))


def _cold_startup(dsn):
    """Observe the real session lock after a real internal bootstrap commit."""
    event_dir = Path(os.environ['CONSULTATION_UPGRADE_EVENTS'])
    release = Path(os.environ['CONSULTATION_UPGRADE_RELEASE'])
    leader = os.environ.get('CONSULTATION_UPGRADE_ROLE') == 'leader'

    class ObservedCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            text = query.as_string(self.connection) if isinstance(query, sql.Composable) else str(query)
            if re.search(r'\bpg_advisory_lock\s*\(', text):
                self.connection.bootstrap_locked = True
            return result

    class ObservedConnection(psycopg.Connection):
        bootstrap_locked = False
        held_after_commit = False
        commits_after_lock = 0

        def commit(self):
            super().commit()
            if self.bootstrap_locked:
                self.commits_after_lock += 1
            # The first commit belongs to the outer startup lock acquisition.
            # Pause only after an initializer has committed inside that lock.
            if leader and self.commits_after_lock >= 2 and not self.held_after_commit:
                self.held_after_commit = True
                (event_dir / 'committed-with-lock.json').write_text(json.dumps({'pid': self.info.backend_pid}))
                deadline = time.monotonic() + 15
                while not release.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                assert release.exists(), 'Parent did not release the observed bootstrap lock'

    def factory():
        conn = ObservedConnection.connect(dsn, row_factory=dict_row, cursor_factory=ObservedCursor)
        target = event_dir / 'connected.json'
        if not target.exists():
            target.write_text(json.dumps({'pid': conn.info.backend_pid}))
        return conn

    factory, flow, worker, _notifications = _runtime(dsn, connection_factory=factory)
    import tenant_ai_agent_startup as startup
    startup.prepare_database()
    with factory() as conn:
        flow.require_schema(conn)
    assert worker.reconcile() == 0
    _output(phase='cold', complete=True)


def _native_retry(factory, startup, flow, phase):
    from tenant_linear_policy import TenantLinearError
    error_state = None
    try:
        startup._prepare_database_with_retry(startup.prepare_database, attempts=3, delay_seconds=0)
    except psycopg.Error as error:
        error_state = error.sqlstate
    with factory() as conn:
        attempts = conn.execute('SELECT last_value FROM test_upgrade_attempts').fetchone()['last_value']
    assert attempts == 3, 'Only the configured bounded migration attempts are allowed'
    if phase == 'retry_recovers':
        assert error_state is None
    else:
        assert error_state == '40001'
        with factory() as conn:
            try:
                flow.require_schema(conn)
            except TenantLinearError as error:
                assert error.status == 503
            else:
                raise AssertionError('Failed bootstrap cannot poison schema readiness')
            assert conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='saas_dialog_consultation_replies' AND column_name='followup_requested_at'").fetchone()
            assert not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='saas_public_case_shares' AND column_name='public_identity'").fetchone()
            conn.execute('DROP EVENT TRIGGER test_upgrade_failure')
        # Same process, same already-warmed core modules: only a committed real
        # unified bootstrap may change the readiness result.
        startup.prepare_database()
    with factory() as conn:
        flow.require_schema(conn)
    _output(phase=phase, attempts=attempts, initial_sqlstate=error_state, **_state(factory))


def _cold_business_paths(dsn, flow, worker):
    from tenant_linear_policy import TenantLinearError

    class ColdCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            text = query.as_string(self.connection) if isinstance(query, sql.Composable) else str(query)
            assert not re.search(r'(^|;)\s*(CREATE|ALTER|DROP|TRUNCATE|DO|INSERT|UPDATE|DELETE|COMMIT|END)\b', text, re.I), 'Unbootstrapped business entry attempted repair or a write'
            return super().execute(query, params, **kwargs)

    class ColdConnection(psycopg.Connection):
        def commit(self):
            raise AssertionError('Unbootstrapped business entry committed a caller transaction')

    factory = lambda: ColdConnection.connect(dsn, row_factory=dict_row, cursor_factory=ColdCursor)
    flow._connect = factory
    worker._connect = factory
    calls = [lambda: flow.inbox(PROFILE, case_id=CASE), lambda: worker.reconcile(),
        lambda: flow.prepare(PROFILE, {'request_id': str(uuid.uuid4()), 'case_id': CASE,
            'channel_id': CHANNEL, 'conversation_id': 'client', 'destination_channel_id': CHANNEL,
            'destination_conversation_id': 'developer', 'message_ids': [SOURCE], 'media_ids': [],
            'summary': 'Must wait for startup', 'confirm': True}, 'https://crm.test')]
    for invoke in calls:
        try:
            invoke()
        except TenantLinearError as error:
            assert error.status == 503
        else:
            raise AssertionError('A missing committed schema must fail closed before business work')
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute('SELECT count(*) AS n FROM saas_internal_consultations').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM saas_dialog_consultation_replies').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM saas_consultation_group_deliveries').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs').fetchone()['n'] == 0
        assert not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='saas_dialog_consultation_replies' AND column_name='followup_requested_at'").fetchone()
    _output(phase='cold_business', guarded_paths=len(calls), writes=0, ddl=0, commits=0)


def _business_reads(dsn, flow, worker, notifications):
    connections = []

    class BusinessCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            text = query.as_string(self.connection) if isinstance(query, sql.Composable) else str(query)
            assert not re.search(r'(^|;)\s*(CREATE|ALTER|DROP|TRUNCATE|DO|VACUUM|REINDEX|COMMIT|END)\b', text, re.I), 'DDL/implicit transaction control in a business path'
            return super().execute(query, params, **kwargs)

    class BusinessConnection(psycopg.Connection):
        active_commits = 0

        def commit(self):
            if self.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS:
                self.active_commits += 1
            return super().commit()

    def factory():
        conn = BusinessConnection.connect(dsn, row_factory=dict_row, cursor_factory=BusinessCursor)
        connections.append(conn)
        return conn

    for module in (flow, worker, notifications):
        module._connect = factory
    with factory() as conn:
        conn.execute('SAVEPOINT readiness_must_preserve_caller_transaction')
        flow.require_schema(conn)
        conn.execute('ROLLBACK TO SAVEPOINT readiness_must_preserve_caller_transaction')
        assert conn.active_commits == 0
    assert flow.inbox(PROFILE, case_id=CASE)['replies'][0]['body'] == HISTORICAL
    assert notifications.listing(PROFILE)['unread_count'] == 1
    assert worker.reconcile() == 0
    prepared = flow.prepare(PROFILE, {'request_id': str(uuid.uuid4()), 'case_id': CASE,
        'channel_id': CHANNEL, 'conversation_id': 'client', 'destination_channel_id': CHANNEL,
        'destination_conversation_id': 'developer', 'message_ids': [SOURCE], 'media_ids': [],
        'summary': 'Explicit synthetic upgrade verification', 'confirm': True}, 'https://crm.test')
    assert prepared['state'] == 'ready'
    # Reconciliation deliberately commits delivery receipts before locking
    # derived replies, preserving consultation -> delivery lock order. Those
    # two business transactions are allowed; readiness may add none.
    assert [conn.active_commits for conn in connections] == [1, 1, 1, 2, 1], (
        'Readiness added a transaction boundary to a business path',
        [conn.active_commits for conn in connections])
    _output(phase='business', native_connections=len(connections), ddl=0, implicit_commits=0)


def _child_env(package, dsn, **changes):
    return {**os.environ, 'DATABASE_URL': 'postgresql://invalid.invalid/disabled',
        'TENANT_ENCRYPTION_KEY': Fernet.generate_key().decode(),
        'CONSULTATION_UPGRADE_TEST_DSN': dsn,
        'AI_AGENTS_ENABLED': 'false', 'AI_AGENTS_AUTO_SEND_ENABLED': 'false', 'AI_SIMULATION_ENABLED': 'false',
        'PYTHONPATH': str(ROOT / 'tests/isolated_database_guard') + os.pathsep + str(package),
        **changes}


def _run(package, dsn, phase, **changes):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--phase', phase],
        cwd=package, env=_child_env(package, dsn, **changes), text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')][-1]


@pytest.fixture(scope='module')
def prior_source(tmp_path_factory):
    target = tmp_path_factory.mktemp('consultation-pr467-source')
    archive = subprocess.run(['git', 'archive', BASELINE, 'beintaskbot-main'], cwd=REPOSITORY,
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(target, filter='data')
    return target / 'beintaskbot-main'


@pytest.fixture(scope='module')
def old_template(prior_source):
    base = os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not base:
        pytest.skip('Disposable loopback PostgreSQL DSN is required')
    _local_dsn(base)
    name = 'test_channel_upgrade_template_' + uuid.uuid4().hex
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(base, dbname=name)
    try:
        baseline = _run(prior_source, dsn, 'baseline')
        yield {'base': base, 'name': name, 'baseline': baseline, 'old_source': prior_source}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture
def upgrade_db(old_template):
    name = 'test_channel_upgrade_' + uuid.uuid4().hex
    base = old_template['base']
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(
            sql.Identifier(name), sql.Identifier(old_template['name'])))
    try:
        yield {**old_template, 'dsn': make_conninfo(base, dbname=name)}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def test_exact_pr467_upgrade_runs_real_startup_reconcile_and_notifications(upgrade_db):
    result = _run(ROOT, upgrade_db['dsn'], 'upgrade')
    assert result['notifications'] == 1 and result['jobs'] == result['intents'] == 0
    assert len(result['rows']) == 1 and result['rows'][0]['body'] == HISTORICAL
    assert result['rows'][0]['draft_state'] == 'not_requested'
    assert result['rows'][0]['followup_requested_at'] is None
    old = {(row['table_name'], row['column_name']): row for row in upgrade_db['baseline']['columns']}
    new = {(row['table_name'], row['column_name']): row for row in result['columns']}
    assert old.keys() <= new.keys(), 'Upgrade must not remove old tables or columns'
    assert all(new[key]['data_type'] == value['data_type'] for key, value in old.items())
    assert ('saas_dialog_consultation_replies', 'followup_requested_at') not in old
    assert new[('saas_dialog_consultation_replies', 'followup_requested_at')]['is_nullable'] == 'YES'
    consultation_tables = {'saas_dialog_consultation_replies', 'saas_public_case_shares',
        'saas_internal_consultations', 'saas_consultation_group_deliveries',
        'saas_consultation_group_replies'}
    assert {key for key in new.keys() - old.keys() if key[0] in consultation_tables} == {
        ('saas_dialog_consultation_replies', 'followup_requested_at'),
        ('saas_public_case_shares', 'public_identity')}


def test_exact_pr467_code_reads_and_idempotently_appends_after_upgrade(upgrade_db):
    _run(ROOT, upgrade_db['dsn'], 'upgrade')
    rollback = _run(upgrade_db['old_source'], upgrade_db['dsn'], 'rollback')
    assert rollback['old_read'] is True and rollback['old_upsert_id']
    with psycopg.connect(upgrade_db['dsn'], row_factory=dict_row) as conn:
        rows = list(conn.execute('SELECT message_id,body,followup_requested_at FROM saas_dialog_consultation_replies ORDER BY message_id'))
        assert [row['message_id'] for row in rows] == ['old-reply', 'rollback-reply']
        assert rows[0]['body'] == HISTORICAL and all(row['followup_requested_at'] is None for row in rows)
        assert conn.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs').fetchone()['n'] == 0


def test_postbootstrap_business_reads_prepare_and_queue_have_no_ddl_or_implicit_commit(upgrade_db):
    assert _run(ROOT, upgrade_db['dsn'], 'business')['ddl'] == 0


def test_other_schema_lookalikes_do_not_certify_unmigrated_public_schema(upgrade_db):
    assert _run(ROOT, upgrade_db['dsn'], 'spoof')['ready_after_real_bootstrap'] is True


def test_unbootstrapped_real_inbox_prepare_and_queue_fail_closed_without_repair(upgrade_db):
    result = _run(ROOT, upgrade_db['dsn'], 'cold_business')
    assert result['guarded_paths'] == 3 and result['writes'] == result['ddl'] == result['commits'] == 0


@pytest.mark.parametrize('recovers', [True, False])
def test_native_serialization_failure_retries_are_bounded_and_do_not_poison_readiness(upgrade_db, recovers):
    with psycopg.connect(upgrade_db['dsn']) as conn:
        conn.execute('CREATE SEQUENCE test_upgrade_attempts')
        conn.execute("""CREATE FUNCTION test_upgrade_fail() RETURNS event_trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF position(current_setting('test.upgrade_column') in current_query())>0
               AND nextval('test_upgrade_attempts')<=CAST(current_setting('test.upgrade_failures') AS int) THEN
              RAISE EXCEPTION 'Synthetic disposable startup serialization conflict' USING ERRCODE='40001';
            END IF;
          END $$""")
        conn.execute("CREATE EVENT TRIGGER test_upgrade_failure ON ddl_command_start WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION test_upgrade_fail()")
        conn.execute(sql.SQL('ALTER DATABASE {} SET test.upgrade_failures TO {}').format(
            sql.Identifier(conn.info.dbname), sql.Literal('2' if recovers else '100')))
        conn.execute(sql.SQL('ALTER DATABASE {} SET test.upgrade_column TO {}').format(
            sql.Identifier(conn.info.dbname), sql.Literal('followup_requested_at' if recovers else 'public_identity')))
    result = _run(ROOT, upgrade_db['dsn'], 'retry_recovers' if recovers else 'retry_exhausted')
    assert result['attempts'] == 3 and result['jobs'] == result['intents'] == 0


def _wait_file(path, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(.01)
    raise AssertionError('Timed out waiting for isolated startup observation: ' + path.name)


def _wait_lock(dsn, pid, *, advisory=False, timeout=15):
    deadline = time.monotonic() + timeout
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as observer:
        while time.monotonic() < deadline:
            row = observer.execute('SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()
            if row and row['wait_event_type'] == 'Lock' and (not advisory or row['wait_event'] == 'advisory'):
                return dict(row)
            time.sleep(.01)
    raise AssertionError('Actual PostgreSQL startup lock wait was not observed')


def _start_phase(dsn, phase, event_dir, **changes):
    event_dir.mkdir()
    return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--phase', phase], cwd=ROOT,
        env=_child_env(ROOT, dsn, CONSULTATION_UPGRADE_EVENTS=str(event_dir), **changes),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _finish(process):
    stdout, stderr = process.communicate(timeout=45)
    assert process.returncode == 0, (stdout, stderr)
    return [json.loads(line) for line in stdout.splitlines() if line.startswith('{')][-1]


def test_two_cold_processes_hold_one_bootstrap_lock_across_internal_commits(upgrade_db, tmp_path):
    release = tmp_path / 'release-startup'
    leader_dir, follower_dir = tmp_path / 'leader', tmp_path / 'follower'
    leader = _start_phase(upgrade_db['dsn'], 'cold', leader_dir,
        CONSULTATION_UPGRADE_ROLE='leader', CONSULTATION_UPGRADE_RELEASE=str(release))
    follower = None
    try:
        locked = _wait_file(leader_dir / 'committed-with-lock.json')
        follower = _start_phase(upgrade_db['dsn'], 'cold', follower_dir,
            CONSULTATION_UPGRADE_ROLE='follower', CONSULTATION_UPGRADE_RELEASE=str(release))
        connected = _wait_file(follower_dir / 'connected.json')
        _wait_lock(upgrade_db['dsn'], connected['pid'], advisory=True)
        with psycopg.connect(upgrade_db['dsn'], row_factory=dict_row) as observer:
            assert observer.execute("SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND granted", (locked['pid'],)).fetchone()
        assert leader.poll() is None and follower.poll() is None
        release.touch()
        assert _finish(leader)['complete'] and _finish(follower)['complete']
    finally:
        release.touch()
        for process in (leader, follower):
            if process is not None and process.poll() is None:
                process.terminate(); process.communicate(timeout=10)


@pytest.mark.parametrize('phase', ['upgrade', 'queue_gate'])
def test_old_reader_lock_order_allows_upgrade_and_queue_waits_for_completion(upgrade_db, tmp_path, phase):
    reader = psycopg.connect(upgrade_db['dsn'], row_factory=dict_row)
    process = None
    event_dir = tmp_path / 'startup'
    try:
        reader.execute("SET LOCAL statement_timeout='2000ms'")
        reader.execute('SELECT body FROM saas_dialog_consultation_replies LIMIT 1').fetchone()
        process = _start_phase(upgrade_db['dsn'], phase, event_dir)
        connected = _wait_file(event_dir / 'connected.json')
        _wait_lock(upgrade_db['dsn'], connected['pid'])
        # A legacy reader holds replies first, then reads its consultation.
        # Replaying the full legacy ALTER path would acquire these in reverse.
        assert reader.execute('SELECT reason FROM saas_internal_consultations WHERE id=%s::uuid', (CONSULT,)).fetchone()
        assert not (event_dir / 'worker-started.json').exists()
        assert process.poll() is None
        reader.commit()
        result = _finish(process)
        if phase == 'queue_gate':
            assert result['worker_started_after_ready'] is True
            assert _wait_file(event_dir / 'worker-started.json')['ready'] is True
        else:
            assert result['jobs'] == result['intents'] == 0
    finally:
        reader.close()
        if process is not None and process.poll() is None:
            process.terminate(); process.communicate(timeout=10)


if __name__ == '__main__' and sys.argv[1:2] == ['--phase']:
    _phase(sys.argv[2])
