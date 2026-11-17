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
