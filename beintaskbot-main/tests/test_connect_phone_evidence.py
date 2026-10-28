"""Read-only identity evidence against isolated temporary PostgreSQL tables.

Set BEINTASKBOT_TEST_POSTGRES_DSN to a disposable localhost PostgreSQL server.
Only temporary tables are created; application/provider/AI code is not imported.
"""
import json
import os
import uuid

import pytest

from tenant_connect_phone_evidence import collect, lookup, normalize_phone_jid

A, B, CA, CB = [str(uuid.UUID(int=n)) for n in (101, 102, 201, 202)]
J = '994700000001@s.whatsapp.net'
P = '994700000001'
L = '80000000000001@lid'
J2 = '994700000002@s.whatsapp.net'
L2 = '80000000000002@lid'
G = '120363000000001@g.us'


@pytest.fixture
def cur():
    psycopg = pytest.importorskip('psycopg')
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    dsn = os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('A disposable localhost PostgreSQL DSN is required')
    if conninfo_to_dict(dsn).get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Only disposable localhost PostgreSQL is allowed')
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SET LOCAL search_path TO pg_temp')
            cursor.execute('''CREATE TEMP TABLE saas_connect_conversations(
              tenant_id uuid,connect_channel_id uuid,conversation_id text,lead_id bigint,
              conversation_kind text,phone text,contact_name text,
              PRIMARY KEY(tenant_id,connect_channel_id,conversation_id));
              CREATE TEMP TABLE saas_crm_messages(id uuid,tenant_id uuid,kommo_lead_id bigint,
                channel text,external_id text,raw jsonb,direction text DEFAULT 'incoming');
              CREATE TEMP TABLE saas_connect_direct_identities(tenant_id uuid,
                connect_channel_id uuid,identity_key text,canonical_conversation_id text);
              CREATE TEMP TABLE saas_connect_identity_conflicts(tenant_id uuid,
                connect_channel_id uuid,conversation_id text,evidence jsonb);
              CREATE TEMP TABLE saas_crm_native_contact_conversations(tenant_id uuid,
                provider text,channel_id uuid,conversation_id text,contact_id uuid,first_message_id text);
              CREATE TEMP TABLE saas_crm_native_contact_identities(tenant_id uuid,
                identity_type text,identity_value text,contact_id uuid)''')
            yield cursor
        conn.rollback()


def route(cur, identity, lead, *, tenant=A, channel=CA, **labels):
    cur.execute('''INSERT INTO saas_connect_conversations VALUES
      (%s::uuid,%s::uuid,%s,%s,%s,%s,%s)''',
      (tenant, channel, identity, lead, 'group' if identity.endswith('@g.us') else 'contact',
       labels.get('phone', ''), labels.get('name', '')))


def receipt(cur, identity, lead, *, tenant=A, channel=CA, raw=None, external=None):
    reference, message_id = str(uuid.uuid4()), str(uuid.uuid4())
    value = {'conversationId': identity, 'channelId': channel, **(raw or {})}
    cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,channel,external_id,raw)
                VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb)''',
                (reference, tenant, lead, 'connect:' + channel,
                 external or 'connect:' + message_id, json.dumps(value)))
    return reference, message_id


def alias(cur, a, b, *, tenant=A, channel=CA):
    cur.execute('INSERT INTO saas_connect_direct_identities VALUES(%s::uuid,%s::uuid,%s,%s)',
                (tenant, channel, a, b))


def native(cur, identity, first, phone=P, *, channel=CA):
    contact = str(uuid.uuid4())
    cur.execute("INSERT INTO saas_crm_native_contact_conversations VALUES(%s::uuid,'connect',%s::uuid,%s,%s::uuid,%s)",
                (A, channel, identity, contact, first))
    cur.execute("INSERT INTO saas_crm_native_contact_identities VALUES(%s::uuid,'whatsapp_phone',%s,%s::uuid)",
                (A, phone, contact))


@pytest.mark.parametrize('value,expected', [
    (J, P), ('+994 70 000 00 01@c.us', P), ('994-70-0000001@s.whatsapp.net', P),
    (L, ''), (P, ''), ('0700000001@c.us', ''), ('00994700000001@c.us', ''),
    ('994700000001:4@s.whatsapp.net', ''), ('994700000001@g.us', ''),
    ('1234567890123456@s.whatsapp.net', ''), ('994x700000001@c.us', ''), (None, ''),
])
def test_normalize_only_explicit_international_phone_jids(value, expected):
    assert normalize_phone_jid(value) == expected


def test_equal_phone_jids_need_no_lid_pair_and_ignore_mutable_labels(cur):
    legacy = '+994 70 000 00 01@c.us'
    route(cur, J, 1)
    route(cur, legacy, 2)
    route(cur, L, 3, phone=P, name=P)
    result = collect(cur, A, CA)
    assert result['phones_by_identity'] == {J: {P}, legacy: {P}, L: set()}
    assert len(result['pairs']) == 1
    assert result['pairs'][0]['sources'] == ['provider_phone_jid']
    assert not result['conflicts']


def test_signed_pair_uses_original_conversation_and_keeps_opaque_reference(cur):
    route(cur, J, 1)
    route(cur, L, 2)
    reference, _ = receipt(cur, J, 1, raw={'originalConversationId': L,
        'directIdentity': {'jid': J, 'lid': L}, 'text': 'MUST NOT RETURN'})
    result = collect(cur, A, CA)
    assert result['phones_by_identity'][L] == {P}
    assert result['pairs'][0]['message_refs'] == [reference]
    assert 'signed_direct_identity' in result['pairs'][0]['sources']
    assert 'MUST NOT RETURN' not in repr(result)
    assert not result['conflicts']


@pytest.mark.parametrize('fault', ['wrong_pair', 'wrong_channel', 'wrong_route', 'not_connect'])
def test_unrelated_or_untrusted_direct_receipt_does_not_link(cur, fault):
    route(cur, J, 1)
    route(cur, L, 2)
    raw = {'directIdentity': {'jid': J, 'lid': L}}
    if fault == 'wrong_pair': raw['originalConversationId'] = J2
    if fault == 'wrong_channel': raw['channelId'] = CB
    if fault == 'wrong_route': raw['conversationId'] = J2
    receipt(cur, J, 1, raw=raw, external='kommo:foreign' if fault == 'not_connect' else None)
    assert not collect(cur, A, CA)['pairs']


@pytest.mark.parametrize('fault', [None, 'participant', 'event', 'channel', 'route'])
def test_group_participant_pair_obeys_existing_history_contract(cur, fault):
    for identity, lead in ((J, 1), (L, 2), (G, 3)): route(cur, identity, lead)
    raw = {'participantIdentity': {'jid': J, 'lid': L}, 'participantJid': L,
           'externalMessageId': 'PROVIDER_EVENT_123'}
    if fault == 'participant': raw['participantJid'] = L2
    if fault == 'event': raw['externalMessageId'] = ''
    if fault == 'channel': raw['channelId'] = CB
    if fault == 'route': raw['conversationId'] = 'other@g.us'
    reference, _ = receipt(cur, G, 3, raw=raw)
    result = collect(cur, A, CA)
    assert (result['phones_by_identity'][L] == {P}) is (fault is None)
    if fault is None:
        assert result['pairs'][0]['message_refs'] == [reference]
        assert 'signed_participant_identity' in result['pairs'][0]['sources']


def test_aliases_are_scoped_and_conflicting_phone_is_not_arbitrarily_selected(cur):
    for identity, lead in ((J, 1), (L, 2), (J2, 3)): route(cur, identity, lead)
    alias(cur, J, L)
    alias(cur, J2, L, tenant=B)
    alias(cur, J2, L, channel=CB)
    assert collect(cur, A, CA)['phones_by_identity'][L] == {P}
    alias(cur, J2, L)
    result = collect(cur, A, CA, routes={J: {}, L: {}})
    assert set(result['phones_by_identity']) == {J, L}
    assert result['phones_by_identity'][L] == {P, '994700000002'}
    assert all('provider_phone_conflict' in value for value in result['conflicts'].values())
    assert result['pairs'][0]['conflicts'] == ['provider_phone_conflict']


def test_second_lid_is_ambiguous_but_domain_alias_is_not(cur):
    for identity, lead in ((J, 1), (L, 2), (L2, 3)): route(cur, identity, lead)
    alias(cur, L, J)
    alias(cur, J.replace('@s.whatsapp.net', '@c.us'), J)
    assert not collect(cur, A, CA)['conflicts']
    alias(cur, L2, J)
    assert 'provider_identity_ambiguous' in collect(cur, A, CA)['conflicts'][J]


@pytest.mark.parametrize('fault', [None, 'missing_receipt', 'wrong_route', 'wrong_phone', 'unproven_phone', 'conflict'])
def test_native_link_requires_exact_first_message_provenance(cur, fault):
    route(cur, J, 1)
    route(cur, L, 2)
    raw = {'originalConversationId': J}
    if fault == 'unproven_phone': raw = {}
    if fault == 'conflict': raw['conversationIdentityConflict'] = True
    reference, mid = receipt(cur, L if fault != 'wrong_route' else J,
                             2 if fault != 'wrong_route' else 1, raw=raw)
    native(cur, L, 'missing' if fault == 'missing_receipt' else mid,
           '994700000002' if fault == 'wrong_phone' else P)
    result = collect(cur, A, CA)
    valid = fault is None
    assert ('native_identity_provenance' in result['evidence_by_identity'][L]['sources']) is valid
    assert (result['phones_by_identity'][L] == {P}) is valid
    if valid:
        assert result['pairs'][0]['message_refs'] == [reference]


def test_ledger_retains_pair_and_conflict_without_rewriting_receipt(cur):
    route(cur, J, 1)
    route(cur, L, 2)
    proof = {'originalConversationId': L, 'pair_matches_conversation': True,
             'directIdentity': {'jid': J, 'lid': L}}
    cur.execute('INSERT INTO saas_connect_identity_conflicts VALUES(%s::uuid,%s::uuid,%s,%s::jsonb)',
                (A, CA, L, json.dumps(proof)))
    result = lookup(cur, A, CA, L)
    assert result['phones'] == {P}
    assert result['conflicts'] == ['provider_identity_conflict']
    assert result['message_refs'] == []


@pytest.mark.parametrize('fault', [None, 'other_phone', 'unrelated_canonical', 'missing_pair'])
def test_only_complete_consistent_historical_split_is_reviewable(cur, fault):
    route(cur, J, 1)
    route(cur, L, 2)
    proof = {'originalConversationId': L, 'canonicalConversationId': L,
             'pair_matches_conversation': True, 'directIdentity': {'jid': J, 'lid': L}}
    if fault == 'other_phone': alias(cur, J2, L)
    if fault == 'unrelated_canonical': proof['canonicalConversationId'] = L2
    if fault == 'missing_pair': proof['directIdentity'] = {}
    cur.execute('INSERT INTO saas_connect_identity_conflicts VALUES(%s::uuid,%s::uuid,%s,%s::jsonb)',
                (A, CA, L, json.dumps(proof)))
    result = collect(cur, A, CA)
    assert 'provider_identity_conflict' in result['conflicts'][L]
    assert (result['reviewable_conflicts'].get(L) == ['historical_route_split']) is (fault is None)


def test_inventory_adds_group_evidence_preserving_counts_and_hints(cur):
    from tenant_connect_identity import inventory
    for identity, lead in ((J, 1), (L, 2), (G, 3)): route(cur, identity, lead)
    reference, _ = receipt(cur, G, 3, raw={'participantJid': L,
        'participantIdentity': {'jid': J, 'lid': L}, 'externalMessageId': 'provider_event_123'})
    result = inventory(cur, A, CA)
    assert result['complete'] and result['scanned_routes'] == 3
    assert result['excluded_routes'] == 1 and result['unmapped_messages'] == 0
    pair = next(p for p in result['pairs'] if p['split_id'] == L)
    assert pair['verified'] and pair['conflicts'] == []
    assert 'signed_participant_identity' in pair['sources'] and pair['message_refs'] == [reference]


def test_preview_phone_search_finds_verified_lid_without_returning_phone(cur, monkeypatch):
    import tenant_ai_dialogue_merge as merge
    route(cur, J, 10238, phone=P)
    route(cur, L, 10154, name='Synthetic person')
    ref, _ = receipt(cur, L, 10154, raw={'directIdentity': {'jid': J, 'lid': L}})
    monkeypatch.setattr(merge, '_pairs', lambda *_: (A, 11, CA, []))
    result = merge.preview(cur, {'AI_LEARNING_MCP_CURSOR_SECRET': 'synthetic-test-secret'},
                           {'search': '+994 70 000 00 01'})
    rows = {row['lead_id']: row for row in result['routes']}
    assert set(rows) == {10154, 10238}
    assert rows[10154]['stored_phone_present'] is False
    assert rows[10154]['normalized_phone_matches_search'] is False
    assert rows[10154]['verified_phone_matches_search'] is True
    assert rows[10154]['provider_phone_proven'] is True
    assert rows[10154]['message_refs'] == [ref]
    assert rows[10238]['normalized_phone_matches_search'] is True
    encoded = json.dumps(result)
    assert all(value not in encoded for value in (P, J, L, 'Synthetic person'))
    named = merge.preview(cur, {'AI_LEARNING_MCP_CURSOR_SECRET': 'synthetic-test-secret'},
                          {'search': 'Synthetic person'})['routes'][0]
    assert 'normalized_phone_matches_search' not in named
    assert 'verified_phone_matches_search' not in named


def test_preview_does_not_promote_matching_mutable_phone_to_provider_proof(cur, monkeypatch):
    import tenant_ai_dialogue_merge as merge
    route(cur, J, 10238, phone=P)
    route(cur, L, 10154, phone=P, name='Synthetic person')
    monkeypatch.setattr(merge, '_pairs', lambda *_: (A, 11, CA, []))
    result = merge.preview(cur, {'AI_LEARNING_MCP_CURSOR_SECRET': 'synthetic-test-secret'}, {'search': P})
    row = next(item for item in result['routes'] if item['lead_id'] == 10154)
    assert row['stored_phone_present'] and row['normalized_phone_matches_search']
    assert not row['provider_phone_proven'] and not row['verified_phone_matches_search']


def test_collection_is_read_only_and_handles_absent_optional_tables(cur):
    route(cur, J, 1)
    cur.execute('''DROP TABLE saas_connect_direct_identities,saas_connect_identity_conflicts,
                 saas_crm_native_contact_conversations,saas_crm_native_contact_identities''')
    cur.execute('SELECT count(*) AS n FROM saas_connect_conversations')
    before = cur.fetchone()['n']
    # SET TRANSACTION READ ONLY cannot follow fixture writes; an SQL monitor
    # enforces the actual helper's entire cursor surface instead.
    class ReadOnly:
        def execute(self, sql, params=()):
            assert sql.lstrip().upper().startswith('SELECT ')
            return cur.execute(sql, params)
        def fetchone(self): return cur.fetchone()
        def fetchall(self): return cur.fetchall()
    assert lookup(ReadOnly(), A, CA, J)['phones'] == {P}
    cur.execute('SELECT count(*) AS n FROM saas_connect_conversations')
    assert cur.fetchone()['n'] == before
