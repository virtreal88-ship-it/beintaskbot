"""Real localhost PostgreSQL sessions; optional when a disposable DSN exists."""
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_tenant_crm_relation_delivery_postgres import graph, enqueue, ConditionalMock
from tenant_crm_relation_delivery import drive


@pytest.mark.parametrize('mutation', [
    "UPDATE saas_tenant_members SET active=false WHERE telegram_id=81",
    "UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'",
    "UPDATE saas_crm_provider_settings SET sync_direction='off'",
    "UPDATE saas_crm_native_deals SET contact_id=NULL,updated_at=clock_timestamp()",
    "UPDATE saas_tenant_workflow_policies SET value='{}' WHERE policy_key='members'",
])
def test_dispatch_holds_revocation_and_local_change_fences_until_io_finishes(graph, mutation):
    intent = enqueue(graph); graph['db'].commit()
    factory = lambda: psycopg.connect(graph['db'].dsn, row_factory=dict_row)
    entered, release, write_started, write_finished = [threading.Event() for _ in range(4)]
    class BlockingMock(ConditionalMock):
        def compare_and_apply(self, *args):
            entered.set()
            assert release.wait(10)
            return super().compare_and_apply(*args)
    adapter = BlockingMock(graph['binding'])
    def revoke():
        with factory() as conn:
            conn.execute("SET lock_timeout='8s'")
            write_started.set()
            conn.execute(mutation)
        write_finished.set()
    with ThreadPoolExecutor(2) as pool:
        delivery = pool.submit(drive, factory, intent.event_id, adapter=adapter)
        try:
            assert entered.wait(10)
            writer = pool.submit(revoke)
            assert write_started.wait(3)
            assert not write_finished.wait(.2)
        finally:
            release.set()
        assert delivery.result(10) == 'pending'
        writer.result(10)
    assert write_finished.is_set()
    assert len(adapter.calls) == 1


def test_second_session_cannot_replay_inflight_attempt(graph):
    intent = enqueue(graph); graph['db'].commit()
    factory = lambda: psycopg.connect(graph['db'].dsn, row_factory=dict_row)
    entered, release = threading.Event(), threading.Event()
    class BlockingMock(ConditionalMock):
        def compare_and_apply(self, *args):
            entered.set()
            assert release.wait(10)
            return super().compare_and_apply(*args)
    adapter = BlockingMock(graph['binding'])
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(drive, factory, intent.event_id, adapter=adapter)
        try:
            assert entered.wait(10)
            assert drive(factory, intent.event_id, adapter=adapter) == 'busy'
        finally:
            release.set()
        assert future.result(10) == 'pending'
    assert len(adapter.calls) == 1
