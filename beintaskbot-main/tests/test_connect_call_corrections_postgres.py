"""Optional native PostgreSQL races; only disposable localhost DB fixture permitted."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid
import psycopg
from psycopg.rows import dict_row
from test_connect_call_corrections_sql import (A, CA, db, flow, channels, ingest,
    test_correction_both_orders_retries_and_source_isolation,
    test_correction_cannot_overwrite_missed,
    test_correction_after_verified_route_change_uses_persisted_proof)
from test_connect_channel_multiprocess import database
from test_connect_missed_calls import rejected
from test_connect_call_corrections import invalidated
from tenant_connect_calls import record


def test_concurrent_first_delivery_cannot_restore_rejected_status(ingest,channels):
    # Seed only synthetic tenant/route; race a new exact call ID on two DB sessions.
    assert ingest(rejected(channelId=CA))
    local=channels['db'];local.commit()
    lead=local.execute("SELECT kommo_lead_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type='call_rejected'",(A,))[0]['kommo_lead_id']
    local.commit()
    for _ in range(8):
        call_id=uuid.uuid4().hex
        barrier=Barrier(2)
        def deliver(factory):
            data=factory(channelId=CA,externalCallId=call_id)['data']
            with psycopg.connect(local.dsn,row_factory=dict_row) as conn:
                barrier.wait(10)
                with conn.cursor() as cur:
                    record(cur,A,CA,data['conversationId'],lead,data,'2026-11-15T09:00:00Z')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(deliver,[rejected,invalidated]))
        rows=local.execute("SELECT message_type,raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND raw->>'externalCallId'=%s",(A,call_id))
        assert len(rows)==1 and rows[0]['message_type']=='call_invalidated' and rows[0]['raw']['callRevision']==2
        local.commit()

# Replay the exact signed producer bytes on native PostgreSQL too.
from test_connect_call_contract import test_signed_producer_replay

from test_connect_observed_calls_sql import (test_observed_both_orders_retries_and_isolation,
    test_observed_mismatched_known_identity)
from test_connect_observed_calls import observed, observed_invalidated
from test_connect_missed_calls import call
import pytest

@pytest.mark.parametrize('factory',[call,rejected,invalidated])
@pytest.mark.parametrize('neutral',[observed,observed_invalidated])
def test_concurrent_observation_cannot_downgrade_known(ingest,channels,factory,neutral):
    assert ingest(observed(channelId=CA))
    local=channels['db'];local.commit()
    lead=local.execute("SELECT kommo_lead_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type='call_observed'",(A,))[0]['kommo_lead_id']
    local.commit()
    for _ in range(8):
        call_id=uuid.uuid4().hex;barrier=Barrier(2)
        def deliver(kind):
            data=kind(channelId=CA,externalCallId=call_id,source='evolution')['data']
            with psycopg.connect(local.dsn,row_factory=dict_row) as conn:
                barrier.wait(10)
                with conn.cursor() as cur:record(cur,A,CA,data['conversationId'],lead,data,'2026-11-15T09:00:00Z')
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(deliver,[neutral,factory]))
        rows=local.execute("SELECT message_type,raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND raw->>'externalCallId'=%s",(A,call_id))
        assert len(rows)==1 and rows[0]['message_type']=='call_'+factory()['data']['callStatus']
        local.commit()

from test_connect_observed_calls_sql import (test_observation_retraction_reordered,
    test_observation_retraction_never_erases_proven_evidence)


def test_concurrent_observation_retraction(ingest,channels):
    test_concurrent_observation_cannot_downgrade_known(ingest,channels,observed_invalidated,observed)

from test_connect_observed_calls_sql import (test_retraction_bypasses_later_route_conflict,
    test_unknown_retraction_is_durable_without_route_or_lead,
    test_wrong_retraction_scope_rolls_back_ledger,
    test_retraction_channel_and_tenant_are_authenticated,
    test_retraction_matches_original_provider_route_after_canonicalization)


def test_concurrent_ingest_retraction_cannot_resurrect(ingest,channels,monkeypatch):
    import json
    import tenant_connect_transport as transport
    local=channels['db'];local.commit()
    monkeypatch.setattr(transport,'_connect',lambda:psycopg.connect(local.dsn,row_factory=dict_row))
    for _ in range(8):
        call_id=uuid.uuid4().hex;barrier=Barrier(2)
        def deliver(factory):
            event=factory(channelId=CA,externalCallId=call_id)
            barrier.wait(10)
            return transport.ingest(json.dumps(event).encode(),'mock',event)
        with ThreadPoolExecutor(max_workers=2) as pool:assert all(pool.map(deliver,[observed,observed_invalidated]))
        rows=local.execute("SELECT message_type FROM saas_crm_messages WHERE tenant_id=%s::uuid AND raw->>'externalCallId'=%s",(A,call_id))
        assert len(rows)<=1 and all(r['message_type']=='call_invalidated' for r in rows)
        assert len(local.execute('SELECT 1 FROM saas_connect_call_retractions WHERE tenant_id=%s::uuid AND external_call_id=%s',(A,call_id)))==1
        local.commit()

from test_connect_observed_calls_sql import (test_ledger_first_known_must_match_immutable_scope,
    test_ledger_is_scoped_not_cross_channel_or_tenant_authority)

from test_connect_observed_calls_sql import test_retraction_pair_and_original_route_are_immutable
from test_connect_observed_calls_sql import test_all_call_types_lock_before_identity_resolution
