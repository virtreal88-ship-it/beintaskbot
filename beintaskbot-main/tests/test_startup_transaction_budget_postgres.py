"""Startup lock lifetime on disposable native PostgreSQL 17+, never PGlite.

These tests exercise the real startup timeout/retry/connection lifecycle with
small synthetic migrations. The historical upgrade test covers the full DDL.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
import pytest

import tenant_ai_agent_startup as startup


@pytest.fixture
def database(monkeypatch):
    dsn = os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Disposable localhost PostgreSQL 17+ required')
    options = conninfo_to_dict(dsn)
    loopback = {'127.0.0.1', 'localhost', '::1'}
    if (options.get('host') not in loopback or options.get('service') or
            (options.get('hostaddr') and options['hostaddr'] not in {'127.0.0.1', '::1'})):
        raise RuntimeError('Disposable loopback PostgreSQL only')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://invalid.invalid/disabled')
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    name = 'test_startup_budget_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert admin.info.server_version >= 170000, 'Startup requires PostgreSQL 17+'
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    target = make_conninfo(dsn, dbname=name)
    try:
        with psycopg.connect(target) as conn:
            conn.execute('CREATE TABLE budget_webhook(id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE budget_jobs(id INTEGER PRIMARY KEY)')
            conn.execute('CREATE TABLE budget_markers(name TEXT PRIMARY KEY)')
        yield target
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def install_bootstrap(monkeypatch, dsn, migrate):
    connections = []
    verified = []

    def connect():
        conn = psycopg.connect(dsn, row_factory=dict_row)
        connections.append(conn)
        return conn

    def verify(conn):
        # Readiness must see the final commit, even from another connection.
        with psycopg.connect(dsn) as observer:
            assert observer.execute("SELECT 1 FROM budget_markers WHERE name='ready'").fetchone()
        assert conn.execute("SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted").fetchone()
        verified.append(True)

    modules = {
        'tenant_platform_core': SimpleNamespace(_connect=connect, _fernet=lambda: None),
        'tenant_ai_agents_schema': SimpleNamespace(ensure_schema=migrate),
        'tenant_connect_health': SimpleNamespace(prepare_database=lambda conn: None),
        'tenant_dialog_consultations': SimpleNamespace(schema=lambda conn: None, require_schema=verify),
    }
    for name in ('tenant_chat_ai_schema', 'tenant_ai_simulation_schema',
                 'tenant_ai_administrator_drafts', 'tenant_ai_administrator_approvals',
                 'tenant_ai_administrator_observations'):
        modules[name] = SimpleNamespace(ensure_schema=lambda conn: None)
    modules['tenant_ai_administrator_apply'] = SimpleNamespace(
        ensure_schema=lambda conn: conn.execute("INSERT INTO budget_markers VALUES('ready') ON CONFLICT DO NOTHING"))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return connections, verified


def assert_session_released(dsn):
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                            (startup._STARTUP_SCHEMA_LOCK,)).fetchone()[0]


def test_budget_and_session_lock_survive_nested_commit_without_leaking_to_writer(database, monkeypatch):
    def migrate(conn):
        conn.execute("INSERT INTO budget_markers VALUES('committed')")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('transaction_timeout') AS transaction_timeout, current_setting('lock_timeout') AS lock_timeout")
            assert cur.fetchone() == {'transaction_timeout': '5s', 'lock_timeout': '2s'}
        assert conn.execute("SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted").fetchone()
        with psycopg.connect(database) as other:
            assert not other.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                                     (startup._STARTUP_SCHEMA_LOCK,)).fetchone()[0]
            assert other.execute('SHOW transaction_timeout').fetchone()[0] == '0'

    connections, verified = install_bootstrap(monkeypatch, database, migrate)
    startup.prepare_database()
    assert verified == [True]
    assert len(connections) == 1 and connections[0].closed
    assert_session_released(database)


def test_many_short_statements_expire_transaction_release_writer_and_retry_fresh(database, monkeypatch):
    locked = threading.Event()
    failures = []
    attempts = []

    def migrate(conn):
        attempts.append(conn)
        # Exercise real nested commit: earlier committed work is not undone.
        conn.execute("INSERT INTO budget_markers VALUES('committed') ON CONFLICT DO NOTHING")
        conn.commit()
        if len(attempts) == 1:
            conn.execute('LOCK TABLE budget_webhook IN ACCESS EXCLUSIVE MODE')
            conn.execute("INSERT INTO budget_markers VALUES('must_rollback')")
            locked.set()
            # No single statement, nor any lock acquisition, lasts five seconds.
            # Only the full transaction timeout can release the earlier lock.
            for _ in range(7):
                conn.execute('SELECT pg_sleep(1)')

    connections, verified = install_bootstrap(monkeypatch, database, migrate)

    def prepare():
        try:
            startup.prepare_database()
        except psycopg.Error as error:
            failures.append(error.sqlstate)
            assert connections[-1].closed
            assert not verified
            raise

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(startup._prepare_database_with_retry, prepare, attempts=2, delay_seconds=.1)
        assert locked.wait(5), 'Synthetic migration never acquired webhook lock'
        begin = time.monotonic()
        with psycopg.connect(database, options='-c statement_timeout=8000') as writer:
            writer.execute('INSERT INTO budget_webhook VALUES(1)')
        elapsed = time.monotonic() - begin
        future.result(timeout=10)
    assert 3 < elapsed < 8, elapsed
    assert failures == ['25P04']
    assert len(connections) == 2 and all(conn.closed for conn in connections)
    assert connections[0] is not connections[1]
    assert verified == [True]
    with psycopg.connect(database) as observer:
        assert observer.execute('SELECT name FROM budget_markers ORDER BY name').fetchall() == [('committed',), ('ready',)]
        assert observer.execute('SELECT id FROM budget_webhook').fetchall() == [(1,)]
    assert_session_released(database)


def test_later_lock_conflict_releases_earlier_lock_before_retry_and_readiness(database, monkeypatch):
    attempts = []
    errors = []
    holder = psycopg.connect(database)
    holder.execute('LOCK TABLE budget_jobs IN ACCESS EXCLUSIVE MODE')

    def migrate(conn):
        attempts.append(conn)
        conn.execute('LOCK TABLE budget_webhook IN ACCESS EXCLUSIVE MODE')
        conn.execute('LOCK TABLE budget_jobs IN ACCESS EXCLUSIVE MODE')

    connections, verified = install_bootstrap(monkeypatch, database, migrate)

    def prepare():
        try:
            startup.prepare_database()
        except psycopg.Error as error:
            errors.append(error.sqlstate)
            assert connections[-1].closed
            assert not verified
            # The old writer is usable before the next migration attempt.
            with psycopg.connect(database, options='-c statement_timeout=1000') as writer:
                writer.execute('INSERT INTO budget_webhook VALUES(1)')
            assert_session_released(database)
            holder.commit()
            raise

    try:
        startup._prepare_database_with_retry(prepare, attempts=2, delay_seconds=0)
    finally:
        holder.close()
    assert errors == ['55P03']
    assert len(attempts) == 2 and all(conn.closed for conn in connections)
    assert verified == [True]
    assert_session_released(database)
