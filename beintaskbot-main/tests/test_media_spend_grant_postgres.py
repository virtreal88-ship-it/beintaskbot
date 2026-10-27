"""Real multi-process grant-ledger races; disposable localhost PostgreSQL only."""
import multiprocessing
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
import tenant_ai_spend_budget as spend
import tenant_knowledge_campaign_schema as campaign_schema
import tenant_knowledge_candidates_store as candidates_store


def _reserve_child(dsn, tenant_id, campaign_id, call_key, operation, barrier, results):
    import tenant_ai_spend_budget as child_spend
    from tenant_linear_policy import TenantLinearError

    child_spend._connect = lambda: psycopg.connect(dsn, row_factory=dict_row)
    try:
        barrier.wait(20)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                if operation == 'vision':
                    reservation_id = child_spend.reserve_media(
                        cur, tenant_id, call_key, 'grant_vision', 'grant-vision-model',
                        'vision_low', 1, campaign_id=campaign_id)
                else:
                    reservation_id = child_spend.reserve(
                        cur, tenant_id, call_key, 'grant_generation', 'grant-text-model',
                        total_upper=1_000_000, campaign_id=campaign_id)
        # Retain the full reservation as an unknown provider outcome. No provider
        # call is made by this test process.
        if operation == 'vision':
            child_spend.settle_media(tenant_id, call_key, operation='vision_low',
                attempted_units=1, reserved_units=1, unknown=True)
        else:
            child_spend.settle(tenant_id, call_key, {}, unknown=True,
                campaign_id=campaign_id)
        results.put(('ok', str(reservation_id), call_key))
    except TenantLinearError as error:
        results.put(('denied', str(error), call_key))
    except Exception as error:
        results.put(('error', type(error).__name__, str(error), call_key))


def _race(db, tenant_id, campaign_id, operations):
    ctx = multiprocessing.get_context('spawn')
    barrier = ctx.Barrier(len(operations))
    results = ctx.Queue()
    processes = [ctx.Process(target=_reserve_child, args=(
        db.dsn, tenant_id, campaign_id, key, operation, barrier, results))
        for key, operation in operations]
    for process in processes:
        process.start()
    try:
        values = [results.get(timeout=45) for _ in processes]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert all(value[0] != 'error' for value in values), values
        return values
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_parallel_grant_reservations_hold_unknown_and_reject_after_revoke(database, monkeypatch):
    db = database
    tenant_id = str(uuid.uuid4())
    owner_id = 987654321
    agent_id = str(uuid.uuid4())
    campaign_id = str(uuid.uuid4())
    monkeypatch.delenv('AI_TENANT_SPEND_LIMITS_MICROUSD', raising=False)
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',
        '{"grant-text-model":{"input":6000000,"output":6000000}}')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',
        '{"grant-vision-model":{"vision_low":6000000}}')
    candidates_store.schema(db)
    campaign_schema.create_schema(db)
    spend.ensure_schema(db)
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Grant race','active',%s)",
        (tenant_id, 'grant-' + tenant_id[:8], owner_id))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,%s,'owner','Synthetic owner')",
        (tenant_id, owner_id))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,model) VALUES(%s::uuid,%s::uuid,'Synthetic grant agent',%s,'grant-text-model')",
        (tenant_id, agent_id, owner_id))
    db.execute('''INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,
       media_policy,progress,max_cost_microusd)
      VALUES(%s::uuid,%s::uuid,%s,'[]'::jsonb,%s::uuid,'running',%s,now(),10,100000,
       '{}'::jsonb,'{"mode":"case_media_batch"}'::jsonb,10000000)''',
      (tenant_id, campaign_id, owner_id, agent_id, owner_id))
    db.execute('''INSERT INTO saas_ai_media_grants
      (tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,10000000,'active',%s,now())''',
      (tenant_id, campaign_id, campaign_id, owner_id, owner_id))
    db.commit()

    # Different processes and different operation paths compete for $6 each;
    # only one may reserve against the fixed $10 cap.
    first_key, second_key = 'grant-race:' + str(uuid.uuid4()), 'grant-race:' + str(uuid.uuid4())
    outcomes = _race(db, tenant_id, campaign_id,
        [(first_key, 'generation'), (second_key, 'vision')])
    assert sorted(value[0] for value in outcomes) == ['denied', 'ok'], outcomes
    admitted = next(value for value in outcomes if value[0] == 'ok')
    assert 'ai_media_grant_budget_exhausted' in next(value[1] for value in outcomes if value[0] == 'denied')
    reservations = db.execute('''SELECT call_key,state,reserved_microusd,grant_id FROM saas_ai_spend_reservations
      WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''', (tenant_id, campaign_id))
    # Release the read transaction before workers run their settlement path.
    # That path ensures the schema (including ALTER TABLE IF NOT EXISTS), so
    # keeping an ACCESS SHARE lock here would block DDL while this process
    # waits for child results: a test-harness deadlock, not ledger contention.
    db.commit()
    assert len(reservations) == 1
    assert reservations[0]['call_key'] == admitted[2]
    assert reservations[0]['state'] == 'unknown'
    assert reservations[0]['reserved_microusd'] == 6_000_000
    assert str(reservations[0]['grant_id']) == campaign_id

    # Two restarted workers can also race the same idempotency key. Both must
    # resolve to the existing ledger row, without a duplicate or extra spend.
    operation = 'generation' if admitted[2] == first_key else 'vision'
    duplicate = _race(db, tenant_id, campaign_id,
        [(admitted[2], operation), (admitted[2], operation)])
    assert [value[0] for value in duplicate] == ['ok', 'ok'], duplicate
    assert {value[1] for value in duplicate} == {admitted[1]}, duplicate
    assert db.execute('''SELECT count(*) AS n FROM saas_ai_spend_reservations
      WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''', (tenant_id, campaign_id))[0]['n'] == 1
    db.commit()

    # A restarted worker replaying the identical key is idempotent; it does not
    # create another reservation or release the unknown hold.
    replayed = _race(db, tenant_id, campaign_id, [(admitted[2], operation)])
    assert replayed[0][0] == 'ok' and replayed[0][1] == admitted[1], replayed
    assert db.execute('''SELECT count(*) AS n FROM saas_ai_spend_reservations
      WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''', (tenant_id, campaign_id))[0]['n'] == 1
    db.commit()

    # Revocation blocks a fresh call key even though the campaign row remains
    # present and approved. Existing unknown spend remains untouched.
    db.execute("UPDATE saas_ai_media_grants SET status='revoked',revoked_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",
        (tenant_id, campaign_id))
    db.commit()
    denied = _race(db, tenant_id, campaign_id,
        [('grant-after-revoke:' + str(uuid.uuid4()), 'vision')])
    assert denied[0][0] == 'denied' and 'ai_media_grant_inactive' in denied[0][1], denied
    assert db.execute('''SELECT state,reserved_microusd FROM saas_ai_spend_reservations
      WHERE tenant_id=%s::uuid AND grant_id=%s::uuid''', (tenant_id, campaign_id))[0] == {
        'state': 'unknown', 'reserved_microusd': 6_000_000}
