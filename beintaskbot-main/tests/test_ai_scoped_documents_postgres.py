"""Actual concurrent AI scope fences; disposable localhost PostgreSQL only.

PGlite cannot run this suite. Final CI must supply BEINTASKBOT_TEST_POSTGRES_DSN.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A
from test_ai_scoped_documents import scoped, grants, ACTOR
import tenant_ai_agents_store as store
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def concurrent(scoped, monkeypatch):
    database, ids = scoped
    grants(database, {'view': 'self', 'update': 'self'})
    def factory():
        connection = psycopg.connect(database.dsn, row_factory=dict_row)
        connection.execute("SET statement_timeout='8s'")
        connection.execute("SET lock_timeout='6s'")
        return connection
    monkeypatch.setattr(store, '_connect', factory)
    return factory, ids


def save(factory, ident, name, ready=None):
    with factory() as conn:
        if ready:
            ready(conn.info.backend_pid)
        try:
            return store.save_agent(ACTOR, {'name': name, 'expected_version': 1}, agent_id=ident, connection=conn)
        except TenantLinearError as error:
            return error.status


def wait_blocked(factory, pid, blocker):
    deadline = time.monotonic() + 4
    with factory() as observer:
        while time.monotonic() < deadline:
            row = observer.execute('SELECT %s=ANY(pg_blocking_pids(%s)) AS waiting', (blocker, pid)).fetchone()
            if row['waiting']:
                return
            time.sleep(.01)
    pytest.fail('Expected PostgreSQL lock wait was not observed')


def worker_pid():
    event = threading.Event()
    ids = []
    def ready(pid):
        ids.append(pid)
        event.set()
    return event, ids, ready


def test_concurrent_updates_serialize_without_share_upgrade(concurrent):
    factory, ids = concurrent
    barrier = threading.Barrier(2)
    def update(name):
        return save(factory, ids['self'], name, lambda _: barrier.wait(timeout=4))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, name) for name in ('first', 'second')]
        outcomes = [future.result(timeout=12) for future in futures]
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert outcomes.count(409) == 1
    with factory() as conn:
        assert conn.execute('SELECT version FROM saas_ai_agents WHERE id=%s', (ids['self'],)).fetchone()['version'] == 2


def test_configuration_mutex_precedes_agent_row_even_for_scoped_update(concurrent):
    factory, ids = concurrent
    event, pids, ready = worker_pid()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as creator:
            creator.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('ai-agent-config:' + A,))
            future = pool.submit(save, factory, ids['self'], 'updated', ready)
            assert event.wait(4)
            wait_blocked(factory, pids[0], creator.info.backend_pid)
            # Models setup recovery after acquiring the configuration mutex.
            # The updater must not already hold SHARE/UPDATE on this agent.
            creator.execute('SELECT id FROM saas_ai_agents WHERE id=%s FOR UPDATE NOWAIT', (ids['self'],))
        assert future.result(timeout=12)=={'id':ids['self'],'version':2}


def test_revocation_committed_before_admission_denies_update(concurrent):
    factory, ids = concurrent
    event, pids, ready = worker_pid()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with factory() as revoke:
            revoke.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{31,access_scopes,ai_agents,update}','\"none\"') WHERE tenant_id=%s AND policy_key='members'", (A,))
            future = pool.submit(save, factory, ids['self'], 'must not write', ready)
            assert event.wait(4)
            wait_blocked(factory, pids[0], revoke.info.backend_pid)
        assert future.result(timeout=12) in (403, 404)
    with factory() as conn:
        assert conn.execute('SELECT name,version FROM saas_ai_agents WHERE id=%s', (ids['self'],)).fetchone() == {'name':'self','version':1}


def test_admitted_update_fences_revocation_until_commit(concurrent, monkeypatch):
    factory, ids = concurrent
    admitted, release = threading.Event(), threading.Event()
    original = store._authorize
    update_pid = []
    def pause(cur, profile, **kwargs):
        value = original(cur, profile, **kwargs)
        update_pid.append(cur.connection.info.backend_pid)
        admitted.set()
        if not release.wait(4):
            raise AssertionError('Timed out waiting for revocation observer')
        return value
    monkeypatch.setattr(store, '_authorize', pause)
    event, pids, ready = worker_pid()
    def revoke():
        with factory() as conn:
            ready(conn.info.backend_pid)
            conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{31,access_scopes,ai_agents,update}','\"none\"') WHERE tenant_id=%s AND policy_key='members'", (A,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, factory, ids['self'], 'admitted')
        assert admitted.wait(4)
        second = pool.submit(revoke)
        try:
            assert event.wait(4)
            wait_blocked(factory, pids[0], update_pid[0])
        finally:
            release.set()
        assert first.result(timeout=12)=={'id':ids['self'],'version':2}
        second.result(timeout=12)
    monkeypatch.setattr(store, '_authorize', original)
    assert save(factory, ids['self'], 'revoked') in (403, 404)
