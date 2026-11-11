"""Real localhost PostgreSQL sessions, never production or provider requests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Barrier
import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A
from test_crm_import_provenance_postgres import source, observed, count
from tenant_crm_import_provenance import validate_observation
from tenant_crm_sync_import import import_contacts
from tenant_crm_relation_identity import current_binding


@pytest.mark.parametrize('change', ['reconnect','preferred_oauth_insert'])
def test_import_mapping_transaction_fences_reconnect_and_preferred_insert(source, change):
    rows,evidence=observed()
    if change == 'preferred_oauth_insert':
        source.execute("UPDATE saas_tenant_integrations SET provider='kommo_manual'")
        with source.cursor() as cur: binding=current_binding(cur,A)
        evidence=replace(evidence,binding=binding)
        mutation="""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at)
          VALUES(%s::uuid,'kommo','connected','other.kommo.com',clock_timestamp())"""
    else:
        mutation="UPDATE saas_tenant_integrations SET connected_at=clock_timestamp() WHERE tenant_id=%s::uuid"
    source.commit()
    entered,release,writer_started,writer_finished = [Event() for _ in range(4)]
    def factory(): return psycopg.connect(source.dsn,row_factory=dict_row)
    def importer():
        with factory() as conn, conn.cursor() as cur:
            validate_observation(cur,A,'contacts',rows,evidence)
            entered.set()
            assert release.wait(10)
            import_contacts(cur,A,rows,observation=evidence)
    def reconnect():
        with factory() as conn:
            conn.execute("SET lock_timeout='8s'")
            writer_started.set()
            conn.execute(mutation,(A,))
        writer_finished.set()
    with ThreadPoolExecutor(2) as pool:
        importer_future=pool.submit(importer)
        try:
            assert entered.wait(10)
            writer_future=pool.submit(reconnect)
            assert writer_started.wait(3)
            assert not writer_finished.wait(.2)
        finally:
            release.set()
        importer_future.result(10); writer_future.result(10)
    assert count(source,'saas_crm_relation_identities') == 1
    with source.cursor() as cur: assert current_binding(cur,A) != evidence.binding


def test_two_import_transactions_never_duplicate_record_or_identity(source):
    rows,evidence=observed()
    barrier=Barrier(2)
    def importer():
        with psycopg.connect(source.dsn,row_factory=dict_row) as conn, conn.cursor() as cur:
            conn.execute("SET lock_timeout='8s'")
            barrier.wait(5)
            return import_contacts(cur,A,rows,observation=evidence)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(importer) for _ in range(2)]
        results=[future.result(15) for future in futures]
    assert any(results)
    assert count(source,'saas_crm_native_contacts') == count(source,'saas_crm_relation_identities') == 1
