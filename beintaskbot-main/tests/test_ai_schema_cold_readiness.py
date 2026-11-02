"""Cold workers certify committed AI schema on disposable native PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
import tenant_ai_agents_schema as schema
import tenant_platform_schema as platform


class ReadOnlySchemaCursor(psycopg.Cursor):
    """Observe actual SQL; an unexpected schema mutation fails the request."""
    def execute(self, query, params=None, **kwargs):
        statement = str(query).lstrip().split(None, 1)[0].upper()
        if statement in {'ALTER', 'CREATE', 'DROP', 'DO', 'COMMIT'}:
            raise AssertionError(f'Cold business request executed {statement}')
        return super().execute(query, params, **kwargs)


class ObservedConnection:
    def __init__(self, conn):
        self.conn = conn
        self.commits = 0

    def cursor(self):
        return self.conn.cursor()

    def commit(self):
        self.commits += 1
        self.conn.commit()


def _cold_worker(dsn, number, barrier, results):
    # spawn gives each worker the real default, uninitialized module state.
    try:
        assert schema._ready is False
        assert platform._schema_ready is False
        with psycopg.connect(dsn, row_factory=dict_row,
                            cursor_factory=ReadOnlySchemaCursor,
                            options='-c statement_timeout=6000 -c deadlock_timeout=100') as conn:
            conn.execute('UPDATE ai_schema_cold_probe SET value=%s WHERE id=%s',
                         (f'worker-{number}', number))
            table = 'saas_ai_agent_jobs' if number == 1 else 'saas_ai_conversation_state'
            conn.execute(sql.SQL('LOCK TABLE {} IN ROW EXCLUSIVE MODE').format(sql.Identifier(table)))
            barrier.wait(10)
            observed = ObservedConnection(conn)
            schema.ensure_schema(observed)
            locks = conn.execute("""SELECT mode FROM pg_locks
                WHERE pid=pg_backend_pid() AND (mode='AccessExclusiveLock' OR locktype='advisory')""").fetchall()
            assert not locks, locks
            assert observed.commits == 0
            conn.rollback()
        results.put(('ok', number))
    except BaseException as error:
        results.put(('error', number, type(error).__name__, str(error)))


@pytest.fixture
def probe(database):
    with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
        conn.execute('CREATE TABLE ai_schema_cold_probe(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute("INSERT INTO ai_schema_cold_probe VALUES(1,'committed'),(2,'committed')")
    yield database.dsn
    with psycopg.connect(database.dsn) as conn:
        conn.execute('DROP TABLE ai_schema_cold_probe')


@pytest.mark.parametrize('warm_hint', [False, True])
def test_cold_and_warm_business_schema_preserve_transaction_without_ddl(probe, monkeypatch, warm_hint):
    monkeypatch.setattr(schema, '_ready', warm_hint)
    monkeypatch.setattr(platform, '_schema_ready', False)
    with psycopg.connect(probe, row_factory=dict_row,
                        cursor_factory=ReadOnlySchemaCursor) as conn:
        conn.execute("UPDATE ai_schema_cold_probe SET value='uncommitted' WHERE id=1")
        observed = ObservedConnection(conn)
        schema.ensure_schema(observed)
        assert observed.commits == 0
        assert not conn.execute("""SELECT mode FROM pg_locks
            WHERE pid=pg_backend_pid() AND (mode='AccessExclusiveLock' OR locktype='advisory')""").fetchall()
        with psycopg.connect(probe, row_factory=dict_row) as observer:
            assert observer.execute('SELECT value FROM ai_schema_cold_probe WHERE id=1').fetchone()['value'] == 'committed'
        conn.rollback()
        assert conn.execute('SELECT value FROM ai_schema_cold_probe WHERE id=1').fetchone()['value'] == 'committed'


def test_genuine_cold_processes_keep_opposing_business_locks_without_deadlock(probe):
    ctx = multiprocessing.get_context('spawn')
    barrier, results = ctx.Barrier(2), ctx.Queue()
    children = [ctx.Process(target=_cold_worker, args=(probe, n, barrier, results)) for n in (1, 2)]
    for child in children:
        child.start()
    try:
        outcomes = [results.get(timeout=20) for child in children]
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        assert sorted(outcomes) == [('ok', 1), ('ok', 2)]
        with psycopg.connect(probe, row_factory=dict_row) as conn:
            assert [row['value'] for row in conn.execute('SELECT value FROM ai_schema_cold_probe ORDER BY id')] == ['committed', 'committed']
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_rolled_back_repair_cannot_certify_other_connection_or_later_request(database):
    with psycopg.connect(database.dsn) as conn:
        conn.execute('ALTER TABLE saas_ai_conversation_state DROP COLUMN agent_assignment_id')
    try:
        with psycopg.connect(database.dsn, row_factory=dict_row) as repair:
            repair.execute('ALTER TABLE saas_ai_conversation_state ADD COLUMN agent_assignment_id UUID NULL')
            assert schema._schema_current(repair)
            with psycopg.connect(database.dsn, row_factory=dict_row) as observer:
                assert not schema._schema_current(observer)
            repair.rollback()
            assert not schema._schema_current(repair)
        with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
            schema.ensure_schema(conn)
            assert schema._schema_current(conn)
    finally:
        with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
            schema.ensure_schema(conn)


def test_hidden_schema_objects_do_not_certify_shadow_table(database):
    with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
        conn.execute('CREATE SCHEMA ai_schema_shadow')
        conn.execute('CREATE TABLE ai_schema_shadow.saas_ai_agents (LIKE public.saas_ai_agents INCLUDING DEFAULTS)')
        conn.execute('SET LOCAL search_path=ai_schema_shadow,public')
        assert not schema._schema_current(conn)
        conn.rollback()
        assert schema._schema_current(conn)


def test_ready_database_hint_cannot_skip_empty_second_database(database, monkeypatch):
    name = 'test_ai_schema_cold_' + uuid.uuid4().hex
    with psycopg.connect(database.dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        monkeypatch.setattr(schema, '_ready', True)
        monkeypatch.setattr(platform, '_schema_ready', True)
        with psycopg.connect(make_conninfo(database.dsn, dbname=name), row_factory=dict_row) as conn:
            assert not schema._schema_current(conn)
            schema.ensure_schema(conn)
            assert schema._schema_current(conn)
        with psycopg.connect(database.dsn, row_factory=dict_row,
                            cursor_factory=ReadOnlySchemaCursor) as conn:
            schema.ensure_schema(conn)
    finally:
        with psycopg.connect(database.dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def test_parallel_missing_core_column_bootstraps_then_cold_requests_read_only(database, monkeypatch):
    with psycopg.connect(database.dsn) as conn:
        conn.execute('ALTER TABLE saas_ai_conversation_state DROP COLUMN agent_assignment_id')
    monkeypatch.setattr(schema, '_ready', True)
    barrier = threading.Barrier(2)

    def initialize(_):
        with psycopg.connect(database.dsn, row_factory=dict_row,
                            options='-c statement_timeout=6000 -c deadlock_timeout=100') as conn:
            barrier.wait(10)
            schema.ensure_schema(conn)
            assert schema._schema_current(conn)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(initialize, (1, 2)))
        monkeypatch.setattr(schema, '_ready', False)
        monkeypatch.setattr(platform, '_schema_ready', False)
        with psycopg.connect(database.dsn, row_factory=dict_row,
                            cursor_factory=ReadOnlySchemaCursor) as conn:
            schema.ensure_schema(conn)
    finally:
        with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
            schema.ensure_schema(conn)
