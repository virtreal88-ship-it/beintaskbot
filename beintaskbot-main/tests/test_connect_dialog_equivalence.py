"""Retained direct-dialog reconciliation on disposable native PostgreSQL.

No provider calls, AI work, production connections or destructive history merge.
"""
import json
import multiprocessing
import uuid

import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, CA, CB, PROFILE
from test_connect_dependent_reconcile import seed, JID, LID, LEADS
import tenant_ai_dialogue_merge as merge
import tenant_connect_dialog_equivalence as equivalence
from tenant_linear_policy import TenantLinearError


def cfg(tenant=A, channel=CA, actor=11):
    return {'AI_LEARNING_MCP_TENANT_ID': tenant, 'AI_LEARNING_MCP_CHANNEL_ID': channel,
            'AI_LEARNING_MCP_CURSOR_SECRET': 'synthetic-equivalence-secret', 'actor_id': actor}


def prepare(db, monkeypatch):
    seed(db, monkeypatch)
    merge.ensure_schema(db)
    with db.cursor() as cur: equivalence.schema(cur)
    db.commit()


def candidate(db, left=8101, right=8102):
    with db.cursor() as cur:
        items = merge.preview(cur, cfg(), {})['items']
    db.commit()
    return next(item for item in items if item['canonical_lead_id'] == left
                and item['split_lead_id'] == right)


def command(item, operation='merge'):
    return {'operation': operation, 'pair_id': item['pair_id'],
            'precondition': item['precondition'], 'request_id': str(uuid.uuid4())}


def apply(db, payload, config=None):
    with db.cursor() as cur: result = merge.execute(cur, config or cfg(), payload)
    db.commit()
    return result


def snapshot_rows(db, table):
    # Only test-owned, fixed table names are passed here.
    rows = db.execute('SELECT row_to_json(r) AS row FROM '+table+' r WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    return sorted((row['row'] for row in rows), key=lambda row: json.dumps(row, sort_keys=True))


def native_dependencies(db):
    contact = str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone) VALUES(%s::uuid,%s::uuid,'Synthetic','editable display value')", (contact, A))
    db.execute("INSERT INTO saas_crm_native_contact_identities(tenant_id,identity_type,identity_value,contact_id) VALUES(%s::uuid,'whatsapp_phone',%s,%s::uuid)", (A, JID.split('@')[0], contact))
    db.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,'retained-test','Synthetic')", (A,))
    db.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'retained-test','open','Synthetic')", (A,))
    for identity, first in ((JID, 'synthetic248-0'), (LID, 'synthetic248-51')):
        deal, task, ticket = (str(uuid.uuid4()) for _ in range(3))
        db.execute("""INSERT INTO saas_crm_native_contact_conversations
          (tenant_id,provider,channel_id,conversation_id,contact_id,first_message_id)
          VALUES(%s::uuid,'connect',%s::uuid,%s,%s::uuid,%s)""", (A, CA, identity, contact, first))
        db.execute("""INSERT INTO saas_crm_native_deals(id,tenant_id,contact_id,title,pipeline_id,stage_id,channel_id,conversation_id)
          VALUES(%s::uuid,%s::uuid,%s::uuid,'Synthetic','retained-test','open',%s::uuid,%s)""", (deal, A, contact, CA, identity))
        db.execute("""INSERT INTO saas_crm_native_tasks(id,tenant_id,deal_id,contact_id,channel_id,conversation_id,text,responsible_id)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,'Synthetic task',11)""", (task, A, deal, contact, CA, identity))
        db.execute("""INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,created_by)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,'manual',11)""", (A, ticket, CA, identity))
    db.commit()


def test_retained_merge_keeps_every_history_crm_dependency_and_source_row(db, monkeypatch):
    prepare(db, monkeypatch)
    native_dependencies(db)
    immutable = ['saas_connect_conversations', 'saas_crm_messages', 'saas_crm_native_contacts',
                 'saas_crm_native_contact_identities', 'saas_crm_native_contact_conversations',
                 'saas_crm_native_deals', 'saas_crm_native_tasks', 'saas_support_tickets',
                 'saas_ai_agent_jobs', 'saas_ai_agent_drafts', 'saas_operator_dialog_reads']
    before = {table: snapshot_rows(db, table) for table in immutable}
    item = candidate(db)
    assert item['eligible'], item['conflicts']
    payload = command(item)
    result = apply(db, payload)
    assert result['retained_route_merge'] and result['preserved_source_rows']
    assert result['combined_messages'] == 85
    verified=candidate(db)['verification']
    assert verified['combined_messages']==verified['verified_original_messages']==85
    assert verified['original_messages_preserved'] and verified['source_rows_retained']
    assert verified['old_link_redirect_preserved']
    assert verified['reference_counts_before']==verified['reference_counts_current']
    assert {table: snapshot_rows(db, table) for table in immutable} == before
    with db.cursor() as cur:
        assert equivalence.members(cur, A, CA, JID) == dict(zip((JID, LID), LEADS))
        assert equivalence.members(cur, A, CA, LID) == dict(zip((JID, LID), LEADS))
        assert equivalence.canonical(cur, A, CA, LID) == JID
        assert not equivalence.members(cur, B, CA, LID)
        assert not equivalence.members(cur, A, CB, LID)
    db.commit()
    assert apply(db, payload)['idempotent_replay']
    assert {table: snapshot_rows(db, table) for table in immutable} == before
    apply(db, command(candidate(db), 'rollback'))
    with db.cursor() as cur:
        assert equivalence.members(cur, A, CA, JID) == {JID: 8101}
        assert equivalence.members(cur, A, CA, LID) == {LID: 8102}
    db.commit()
    assert {table: snapshot_rows(db, table) for table in immutable} == before
    assert len(db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND NOT active', (A,))) == 1


def test_operator_reader_full_journey_retains_cases_media_reads_and_later_message(db, monkeypatch):
    import tenant_operator_dialogs as dialogs
    import tenant_support_inbox_store as support
    from tenant_operator_access import assert_route
    prepare(db, monkeypatch)
    native_dependencies(db)
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    monkeypatch.setattr(support, '_connect', lambda: db)
    original = {row['id']: row for row in snapshot_rows(db, 'saas_crm_messages')}
    case = db.execute('SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid AND conversation_id=%s', (A, LID))[0]['id']
    db.commit()
    dependencies = {table: snapshot_rows(db, table) for table in
        ('saas_crm_native_contact_conversations', 'saas_crm_native_deals', 'saas_crm_native_tasks', 'saas_support_tickets')}
    apply(db, command(candidate(db)))

    listed = [row for row in dialogs.listing(PROFILE)['dialogs'] if row['lead_id'] in LEADS]
    assert len(listed) == 1 and listed[0]['lead_id'] == 8101
    first = dialogs.history(PROFILE, 'connect', 8102, CA, LID)
    assert first['dialog']['lead_id'] == 8101
    assert first['next_before']
    second = dialogs.history(PROFILE, 'connect', 8101, CA, JID, before=first['next_before'])
    combined = first['messages'] + second['messages']
    expected_ids = {key for key, row in original.items() if row['kommo_lead_id'] in LEADS}
    assert len(combined) == 85 and {row['id'] for row in combined} == expected_ids
    assert second['next_before'] is None
    for message in combined:
        persisted = original[message['id']]
        assert message['source_lead_id'] == persisted['kommo_lead_id']
        assert message['source_channel_id'] == CA
        assert message['source_conversation_id'] == (JID if persisted['kommo_lead_id'] == 8101 else LID)
        assert message['direction'] == persisted['direction']
        assert message['external_id'] == persisted['external_id']
        if persisted['media_url']:
            assert message['message_type'] == 'image'
            assert message['media_url'] == ''  # Protected media URL is never exposed.
    anchor = next(key for key in expected_ids if original[key]['kommo_lead_id'] == 8102)
    anchored = dialogs.history(PROFILE, 'connect', 8102, CA, LID, anchor=anchor)
    assert anchored['anchor_message_id'] == anchor
    case_view = support.detail(PROFILE, str(case))
    assert case_view['ticket']['conversation_id'] == LID
    assert {row['id'] for row in case_view['messages']} == {
        key for key in expected_ids if original[key]['kommo_lead_id'] == 8102}
    with db.cursor() as cur:
        assert assert_route(cur, A, None, 'connect', LID, CA, 8102)['lead_id'] == 8102
        assert assert_route(cur, A, None, 'connect', LID, CA, 8102, allow_merged=True)['lead_id'] == 8101
    db.commit()

    latest = db.execute('''SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid
      AND kommo_lead_id=ANY(%s::bigint[]) ORDER BY created_at DESC,id DESC LIMIT 1''', (A, LEADS))[0]['id']
    db.commit()
    dialogs.mark_read(PROFILE, {'provider': 'connect', 'lead_id': 8102, 'channel_id': CA,
                               'conversation_id': LID, 'message_id': str(latest)})
    reads = db.execute('''SELECT r.lead_id,r.last_read_message_id,m.kommo_lead_id AS actual_lead
      FROM saas_operator_dialog_reads r JOIN saas_crm_messages m ON m.id=r.last_read_message_id
      WHERE r.tenant_id=%s::uuid AND r.telegram_id=11 AND r.lead_id=ANY(%s::bigint[])''', (A, LEADS))
    assert {row['lead_id'] for row in reads} == set(LEADS)
    assert all(row['lead_id'] == row['actual_lead'] for row in reads)
    db.commit()
    assert next(row for row in dialogs.listing(PROFILE)['dialogs'] if row['lead_id'] == 8101)['unread_count'] == 0

    later = str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,
      direction,body,raw,created_at) VALUES(%s::uuid,%s::uuid,8101,'connect:retained-later-reader',%s,
      'incoming','Synthetic late message',%s::jsonb,clock_timestamp())''',
      (later, A, 'connect:' + CA, json.dumps({'conversationId': JID, 'originalConversationId': LID})))
    db.commit()
    assert any(row['id'] == later for row in dialogs.history(PROFILE, 'connect', 8102, CA, LID)['messages'])
    apply(db, command(candidate(db), 'rollback'))
    after = {row['id']: row for row in snapshot_rows(db, 'saas_crm_messages')}
    assert all(after[key] == value for key, value in original.items())
    assert after[later]['kommo_lead_id'] == 8101
    assert {table: snapshot_rows(db, table) for table in dependencies} == dependencies
    assert len([row for row in dialogs.listing(PROFILE)['dialogs'] if row['lead_id'] in LEADS]) == 2
    assert len(dialogs.history(PROFILE, 'connect', 8102, CA, LID)['messages']) == 34


def test_alias_replay_and_controls_do_not_reopen_source_ai(db, monkeypatch):
    prepare(db, monkeypatch)
    apply(db, command(candidate(db)))
    from tenant_connect_transport import _canonical_direct_conversation
    with db.cursor() as cur:
        for identity, pair in ((JID, {}), (LID, {}), (LID, {'jid': JID, 'lid': LID})):
            data = {'directIdentity': pair} if pair else {}
            assert _canonical_direct_conversation(cur, A, CA, identity, data, {'kind': 'contact'}) == (JID, False)
    db.commit()
    states = db.execute("SELECT conversation_id,state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id=ANY(%s::text[])", (A, [JID, LID]))
    assert {r['conversation_id']: r['state'] for r in states} == {JID: 'human', LID: 'paused'}


def test_cross_domain_phone_merge_needs_no_lid_and_rollback_restores_routing(db, monkeypatch):
    prepare(db, monkeypatch)
    phone, legacy = '994700000099@s.whatsapp.net', '994700000099@c.us'
    for identity, lead in ((phone, 8191), (legacy, 8192)):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'contact')", (A, CA, identity, lead))
    db.commit()
    item = candidate(db, 8191, 8192)
    assert item['eligible']
    apply(db, command(item))
    with db.cursor() as cur: assert equivalence.canonical(cur, A, CA, legacy) == phone
    db.commit()
    apply(db, command(candidate(db, 8191, 8192), 'rollback'))
    from tenant_connect_transport import _canonical_direct_conversation
    with db.cursor() as cur:
        for original in (phone, legacy):
            canonical, conflict = _canonical_direct_conversation(cur, A, CA, original, {}, {'kind': 'contact'})
            assert canonical == original
            # Two retained, now independent domain routes must be quarantined,
            # not silently joined again after the owner's rollback.
            assert conflict
    db.rollback()


def test_unproven_lid_and_phone_cannot_pass_equivalence_conflicts(db, monkeypatch):
    prepare(db, monkeypatch)
    unknown = '80000000000999@lid'
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,8999)", (A, CA, unknown))
    with db.cursor() as cur:
        before = equivalence.snapshot(cur, A, CA, [JID, unknown], [8101, 8999])
        assert 'verified_phone_required' in equivalence.conflicts(cur, A, CA, [JID, unknown], before)
    db.rollback()


def test_proven_historical_split_can_be_merged_without_erasing_conflict_ledger(db, monkeypatch):
    prepare(db, monkeypatch)
    from tenant_connect_direct_identity import record_conflict
    with db.cursor() as cur:
        record_conflict(cur, A, CA, LID, LID, {'directIdentity': {'jid': JID, 'lid': LID}},
                        'synthetic-historical-split')
    db.commit()
    ledger = snapshot_rows(db, 'saas_connect_identity_conflicts')
    item = candidate(db)
    assert item['eligible'], item['conflicts']
    apply(db, command(item))
    assert snapshot_rows(db, 'saas_connect_identity_conflicts') == ledger
    from tenant_connect_identity import conflicted
    with db.cursor() as cur:
        assert not conflicted(cur, A, CA, JID)
        assert conflicted(cur, A, CA, LID)  # Source may not execute independently.
    db.commit()


def test_new_contradictory_phone_is_not_excused_by_existing_merge(db, monkeypatch):
    prepare(db, monkeypatch)
    apply(db, command(candidate(db)))
    from tenant_connect_direct_identity import record_conflict
    from tenant_connect_identity import conflicted
    other = '994700000099@s.whatsapp.net'
    with db.cursor() as cur:
        record_conflict(cur, A, CA, JID, LID, {'directIdentity': {'jid': other, 'lid': LID}},
                        'synthetic-new-contradiction')
    db.commit()
    with db.cursor() as cur:
        assert conflicted(cur, A, CA, JID)
        assert conflicted(cur, A, CA, LID)
    db.commit()


def test_new_unrelated_conflict_cannot_disappear_behind_resolved_first_evidence(db, monkeypatch):
    prepare(db, monkeypatch)
    from tenant_connect_direct_identity import record_conflict
    from tenant_connect_identity import conflicted
    with db.cursor() as cur:
        record_conflict(cur, A, CA, LID, LID, {'directIdentity': {'jid': JID, 'lid': LID}},
                        'synthetic-first-historical-split')
    db.commit()
    apply(db, command(candidate(db)))
    with db.cursor() as cur:
        assert not conflicted(cur, A, CA, JID)
        # A replay can carry newly conflicting identity evidence while its
        # existing CRM message/raw remains immutable. Only the ledger records
        # this observation; all original affected keys already have first facts.
        record_conflict(cur, A, CA, JID, JID,
                        {'directIdentity': {'jid': '994700000099@s.whatsapp.net',
                                            'lid': '80000000000099@lid'}},
                        'synthetic-later-unrelated-conflict')
    db.commit()
    with db.cursor() as cur:
        assert conflicted(cur, A, CA, JID)
        assert conflicted(cur, A, CA, LID)
    db.commit()


@pytest.mark.parametrize('case', ['stale', 'other_tenant', 'other_channel', 'audit_failure'])
def test_invalid_or_failed_activation_is_atomic(db, monkeypatch, case):
    prepare(db, monkeypatch)
    before = snapshot_rows(db, 'saas_crm_messages')
    payload = command(candidate(db))
    config = cfg()
    if case == 'stale': payload['precondition'] = '0' * 64
    if case == 'other_tenant': config = cfg(B, CB, 22)
    if case == 'other_channel': config = cfg(A, CB)
    if case == 'audit_failure':
        def fail(*args, **kwargs): raise RuntimeError('synthetic audit unavailable')
        monkeypatch.setattr(merge, '_audit', fail)
    with pytest.raises((TenantLinearError, RuntimeError)):
        apply(db, payload, config)
    db.rollback()
    assert snapshot_rows(db, 'saas_crm_messages') == before
    assert not db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND active', (A,))
    assert not db.execute('SELECT 1 FROM saas_ai_dialogue_merge_requests WHERE tenant_id=%s::uuid AND request_id=%s::uuid', (A, payload['request_id']))
    db.commit()


def test_rollback_preserves_later_messages_but_rejects_changed_controls(db, monkeypatch):
    prepare(db, monkeypatch)
    apply(db, command(candidate(db)))
    mid = str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body) VALUES(%s::uuid,%s::uuid,8101,'connect:later-retained',%s,'Synthetic later message')", (mid, A, 'connect:' + CA))
    db.commit()
    payload = command(candidate(db), 'rollback')
    db.execute("UPDATE saas_ai_conversation_state SET reason='new manual action',revision=revision+1 WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s", (A, CA, JID))
    db.commit()
    with pytest.raises(TenantLinearError): apply(db, payload)
    db.rollback()
    assert db.execute('SELECT id FROM saas_crm_messages WHERE id=%s::uuid', (mid,))
    assert db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND active', (A,))
    db.commit()


def _concurrent_apply(dsn, payload, barrier, results):
    import psycopg
    from psycopg.rows import dict_row
    try:
        with psycopg.connect(dsn, row_factory=dict_row, options='-c statement_timeout=20000') as conn:
            barrier.wait(10)
            with conn.cursor() as cur: result = merge.execute(cur, cfg(), payload)
        results.put(('ok', result))
    except Exception as error:
        results.put(('error', type(error).__name__, str(error)))


def test_concurrent_identical_merge_request_commits_once(db, monkeypatch):
    prepare(db, monkeypatch)
    payload = command(candidate(db))
    ctx = multiprocessing.get_context('spawn')
    barrier, results = ctx.Barrier(2), ctx.Queue()
    processes = [ctx.Process(target=_concurrent_apply, args=(db.dsn, payload, barrier, results)) for _ in range(2)]
    for process in processes: process.start()
    try:
        values = [results.get(timeout=30) for _ in processes]
        for process in processes: process.join(5)
        assert all(value[0] == 'ok' for value in values), values
        assert sorted(value[1]['idempotent_replay'] for value in values) == [False, True]
        assert len(db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND active', (A,))) == 1
        assert len(db.execute('SELECT 1 FROM saas_ai_dialogue_merge_requests WHERE tenant_id=%s::uuid AND request_id=%s::uuid', (A, payload['request_id']))) == 1
        assert not db.execute('SELECT * FROM test_external_sends')
        db.commit()
    finally:
        for process in processes:
            if process.is_alive(): process.terminate(); process.join(5)
