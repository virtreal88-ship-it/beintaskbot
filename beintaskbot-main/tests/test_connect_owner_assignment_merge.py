"""Owner-only assignment compatibility on disposable native PostgreSQL.

Synthetic routes and data only; no providers or customer conversations are used.
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, AGENT
from test_connect_dependent_reconcile import JID, LID
from test_connect_dialog_equivalence import snapshot_rows, candidate, command, apply, cfg
from test_connect_owner_identity import setup, preview, payload, execute
import tenant_connect_dialog_equivalence as equivalence
import tenant_connect_owner_identity as owner
import tenant_ai_dialogue_merge as merge
from tenant_linear_policy import TenantLinearError


ASSIGNMENTS = {JID: str(uuid.UUID(int=901)), LID: str(uuid.UUID(int=902))}
TABLES = ('saas_ai_conversation_state', 'saas_ai_agent_jobs', 'saas_ai_agent_drafts',
          'saas_crm_messages', 'saas_connect_conversations', 'saas_operator_dialog_reads',
          'saas_connect_direct_identities')


def seed_assignments(db, monkeypatch, *, source_human=False, assigned_to=None):
    setup(db, monkeypatch)
    for identity in (JID, LID):
        human = (identity == LID) if source_human else (identity == JID)
        db.execute('''UPDATE saas_ai_conversation_state SET agent_assignment_id=%s::uuid,
          assigned_to=%s,state=%s,reason=%s,revision=%s WHERE tenant_id=%s::uuid
          AND provider='connect' AND channel_id=%s AND conversation_id=%s''',
          (ASSIGNMENTS[identity], assigned_to, 'human' if human else 'paused',
           'manager_replied' if human else 'operator_pause', 13 if human else 1, A, CA, identity))
    db.commit()


def snapshots(db):
    return {table: snapshot_rows(db, table) for table in TABLES}


def state_rows(db):
    return {r['conversation_id']: r for r in snapshot_rows(db, 'saas_ai_conversation_state')}


def assert_no_merge(db):
    assert not db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE active')
    assert not db.execute('SELECT 1 FROM saas_connect_owner_identity_assertions')
    assert not db.execute('SELECT 1 FROM saas_ai_dialogue_merge_requests')
    assert not db.execute('SELECT 1 FROM test_external_sends')
    db.commit()


@pytest.mark.parametrize('source_human', [False, True])
@pytest.mark.parametrize('assigned_to', [None, 11])
def test_distinct_operations_keep_canonical_assignment_strictest_stop_and_rollback(
        db, monkeypatch, source_human, assigned_to):
    seed_assignments(db, monkeypatch, source_human=source_human, assigned_to=assigned_to)
    # The source's stricter route controls cannot enable the canonical route.
    db.execute('''UPDATE saas_connect_conversations SET ai_enabled=FALSE,manual_hold=TRUE
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''', (A, CA, LID))
    db.commit()
    before = snapshots(db)
    states_before = state_rows(db)
    item = preview(db)
    assert item['eligible'], item['conflicts']
    data = payload(db)
    result = execute(db, data)
    assert result['state'] == 'merged'
    after = state_rows(db)
    assert after[JID]['agent_id'] == AGENT
    assert after[JID]['assigned_to'] == assigned_to
    assert after[JID]['agent_assignment_id'] == ASSIGNMENTS[JID]
    assert after[JID]['state'] == 'human' and after[JID]['reason'] == 'manager_replied'
    assert after[JID]['revision'] > states_before[JID]['revision']
    assert after[LID] == states_before[LID]
    route = next(r for r in snapshot_rows(db, 'saas_connect_conversations') if r['conversation_id'] == JID)
    assert route['ai_enabled'] is False and route['manual_hold'] is True
    saved = db.execute('SELECT snapshot FROM saas_connect_dialog_links WHERE active')[0]['snapshot']
    assert {r['conversation_id']: r for r in saved['states_before']} == {
        identity: states_before[identity] for identity in (JID, LID)}
    db.commit()
    for table in TABLES:
        if table not in {'saas_ai_conversation_state', 'saas_connect_conversations'}:
            assert snapshot_rows(db, table) == before[table]
    assert execute(db, data)['idempotent_replay']
    assert state_rows(db) == after
    apply(db, command(candidate(db), 'rollback'))
    rolled_back = state_rows(db)
    for identity in (JID, LID):
        for field in ('agent_id', 'assigned_to', 'agent_assignment_id', 'state', 'reason'):
            assert rolled_back[identity][field] == states_before[identity][field]
    assert rolled_back[LID] == states_before[LID]
    assert not db.execute('SELECT 1 FROM test_external_sends')
    db.commit()


@pytest.mark.parametrize('change', [
    "agent_id=NULL", "agent_assignment_id=NULL", "assigned_to=99", "state='active'",
])
def test_owner_override_rejects_incomplete_or_different_assignment(db, monkeypatch, change):
    seed_assignments(db, monkeypatch)
    db.execute("UPDATE saas_ai_conversation_state SET " + change +
               " WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s", (A, CA, LID))
    db.commit()
    before = snapshots(db)
    item = preview(db)
    assert not item['eligible'] and item['conflicts']
    with pytest.raises(TenantLinearError):
        execute(db, payload(db))
    db.rollback()
    assert snapshots(db) == before
    assert_no_merge(db)


def test_provider_evidence_alone_does_not_relax_assignment_guard(db, monkeypatch):
    seed_assignments(db, monkeypatch)
    db.execute('''UPDATE saas_crm_messages SET raw=raw||%s::jsonb WHERE tenant_id=%s::uuid''',
               (json.dumps({'directIdentity': {'jid': JID, 'lid': LID}}), A))
    db.commit()
    item = candidate(db)
    assert not item['eligible']
    assert 'conflicting_state_assignment:agent_assignment_id' in item['conflicts']
    assert preview(db)['eligible']
    with pytest.raises(TenantLinearError):
        apply(db, command(item))
    db.rollback()
    assert_no_merge(db)


@pytest.mark.parametrize('change', [
    "UPDATE saas_ai_conversation_state SET agent_assignment_id='00000000-0000-0000-0000-000000000999',revision=revision+1 WHERE conversation_id='" + LID + "'",
    "UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE conversation_id='" + JID + "'",
    "UPDATE saas_ai_agent_jobs SET state='queued' WHERE lead_id=8102",
    "UPDATE saas_ai_agent_jobs SET lease_token='00000000-0000-0000-0000-000000000999',lease_until=now()+interval '1 minute' WHERE lead_id=8102",
])
def test_stale_assignment_or_job_preview_rejected_without_writes(db, monkeypatch, change):
    seed_assignments(db, monkeypatch)
    data = payload(db)
    db.execute(change)
    db.commit()
    before = snapshots(db)
    with pytest.raises(TenantLinearError, match='preview changed'):
        execute(db, data)
    db.rollback()
    assert snapshots(db) == before
    assert_no_merge(db)


def test_owner_assignment_merge_audit_failure_is_atomic(db, monkeypatch):
    seed_assignments(db, monkeypatch)
    data = payload(db)
    before = snapshots(db)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(merge, '_audit', fail)
    with pytest.raises(RuntimeError, match='synthetic audit failure'):
        execute(db, data)
    db.rollback()
    assert snapshots(db) == before
    assert_no_merge(db)


@pytest.mark.parametrize('change', [
    "UPDATE saas_ai_conversation_state SET agent_assignment_id='00000000-0000-0000-0000-000000000999',revision=revision+1 WHERE conversation_id='" + LID + "'",
    "UPDATE saas_ai_agent_jobs SET state='queued' WHERE lead_id=8102",
    "UPDATE saas_ai_agent_jobs SET lease_token='00000000-0000-0000-0000-000000000999',lease_until=now()+interval '1 minute' WHERE lead_id=8102",
])
def test_locked_concurrent_mutation_invalidates_owner_preview(db, monkeypatch, change):
    seed_assignments(db, monkeypatch)
    data = payload(db)
    started = threading.Event()
    app = 'synthetic-owner-merge-' + uuid.uuid4().hex
    def competing_merge():
        with psycopg.connect(db.dsn, row_factory=dict_row, application_name=app,
                            options='-c statement_timeout=15000') as conn:
            started.set()
            with conn.cursor() as cur:
                return owner.execute(cur, cfg(), data)
    with psycopg.connect(db.dsn, row_factory=dict_row) as mutation:
        with mutation.cursor() as cur:
            equivalence.lock(cur, A, CA, [JID, LID])
            cur.execute(change)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(competing_merge)
            try:
                assert started.wait(5)
                # Observe an actual advisory-lock wait, not a timing-only race.
                blocked = False
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    blocked = bool(db.execute('''SELECT 1 FROM pg_stat_activity
                      WHERE application_name=%s AND wait_event_type='Lock' AND wait_event='advisory' ''', (app,)))
                    db.commit()
                    if blocked:
                        break
                    time.sleep(.02)
                assert blocked, 'Competing merge did not reach the held dialogue lock'
                mutation.commit()
                with pytest.raises(TenantLinearError, match='preview changed'):
                    future.result(timeout=10)
            finally:
                mutation.rollback()
    assert_no_merge(db)
    if 'saas_ai_conversation_state' in change:
        assert state_rows(db)[LID]['agent_assignment_id'] == '00000000-0000-0000-0000-000000000999'
    else:
        row = db.execute('SELECT state,lease_token FROM saas_ai_agent_jobs WHERE lead_id=8102')[0]
        assert row['state'] == 'queued' if "state='queued'" in change else row['lease_token'] is not None
        db.commit()


@pytest.mark.parametrize('phase', ['merge', 'rollback'])
@pytest.mark.parametrize('target', ['canonical', 'source', 'agent'])
def test_non_advisory_writer_is_fenced_through_owner_control_write(db, monkeypatch, phase, target):
    seed_assignments(db, monkeypatch, source_human=True)
    if phase == 'rollback':
        execute(db, payload(db))
        data = command(candidate(db), 'rollback')
    else:
        data = payload(db)
    before = state_rows(db)
    observed = []
    if target == 'agent':
        sql = "UPDATE saas_ai_agents SET name='Concurrent synthetic edit' WHERE tenant_id=%s::uuid AND id=%s::uuid"
        params = (A, AGENT)
    else:
        sql = "UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s"
        params = (A, CA, JID if target == 'canonical' else LID)
    def writer():
        # No advisory lock: models archive/transfer's direct row writer.
        with psycopg.connect(db.dsn, options='-c lock_timeout=300ms -c statement_timeout=5000') as conn:
            conn.execute(sql, params)
    def verify_fence():
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                pool.submit(writer).result(timeout=7)
        observed.append(target)
    if phase == 'merge':
        original = equivalence.activate
        def activate(*args, **kwargs):
            # Fresh precondition and snapshot have been checked, before writes.
            verify_fence()
            return original(*args, **kwargs)
        monkeypatch.setattr(equivalence, 'activate', activate)
        execute(db, data)
    else:
        original = equivalence.lock_owner_controls
        def lock_controls(*args, **kwargs):
            original(*args, **kwargs)
            # Transaction row locks must cover the rollback check and restore.
            verify_fence()
        monkeypatch.setattr(equivalence, 'lock_owner_controls', lock_controls)
        apply(db, data)
    assert observed == [target]
    assert state_rows(db)[LID] == before[LID]
    assert not db.execute('SELECT 1 FROM test_external_sends')
    db.commit()


def test_identical_agent_names_do_not_make_distinct_agents_compatible(db, monkeypatch):
    seed_assignments(db, monkeypatch)
    other = str(uuid.UUID(int=903))
    db.execute('''INSERT INTO saas_ai_agents(id,tenant_id,name,created_by)
      SELECT %s::uuid,tenant_id,name,created_by FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid''',
      (other, A, AGENT))
    db.execute('''UPDATE saas_ai_conversation_state SET agent_id=%s::uuid
      WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s''', (other, A, CA, LID))
    db.commit()
    before = snapshots(db)
    item = preview(db)
    assert not item['eligible']
    assert 'conflicting_state_assignment:agent_id' in item['conflicts']
    with pytest.raises(TenantLinearError):
        execute(db, payload(db))
    db.rollback()
    assert snapshots(db) == before
    assert_no_merge(db)
