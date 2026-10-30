"""Direct identity journeys on disposable localhost PostgreSQL; no remote calls."""
import json
import asyncio
import hashlib
import hmac
import multiprocessing
import time
import uuid
from unittest.mock import Mock

import pytest
import psycopg
from psycopg.rows import dict_row

from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
from test_connect_channel_multiprocess import database, race, prepare_send
import tenant_connect_transport as transport
import tenant_operator_dialogs as dialogs

JID = '994700000091@s.whatsapp.net'
LEGACY = '994700000091@c.us'
LID = '80000000000091@lid'
PAIR = {'jid': JID, 'lid': LID}


@pytest.fixture(autouse=True)
def identity_fixture(channels):
    # Shared fixtures seed explicit lead IDs 41-43 without advancing identity.
    channels['db'].execute("SELECT setval(pg_get_serial_sequence('saas_connect_conversations','lead_id'),1000,true)")
    channels['db'].commit()


def event(conversation, *, pair=None, outbound=False, channel=CA, **facts):
    data = {'id': str(uuid.uuid4()), 'channelId': channel,
            'conversationId': conversation, 'type': 'text',
            'text': 'same legitimate text', 'fromMe': outbound, **facts}
    if pair is not None:
        data['directIdentity'] = pair
    return {'id': str(uuid.uuid4()),
            'type': 'message.outbound.observed' if outbound else 'message.inbound.received',
            'occurred_at': '2026-10-28T12:19:40Z', 'data': data}


@pytest.fixture
def ingest(channels, monkeypatch):
    monkeypatch.setattr(transport, '_verify_signature', lambda *args: True)
    monkeypatch.setenv('AI_AGENTS_ENABLED', 'false')
    monkeypatch.setenv('AI_SIMULATION_ENABLED', 'false')
    def no_remote(*args, **kwargs):
        raise AssertionError('Identity ingestion must not call a remote service')
    monkeypatch.setattr(transport, '_request', no_remote)
    return lambda payload: transport.ingest(json.dumps(payload).encode(), 'mock', payload)


def direct_routes(db, tenant=A, channel=CA):
    rows = db.execute('''SELECT conversation_id,lead_id,phone,contact_name,conversation_kind
      FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
      AND conversation_id=ANY(%s::text[]) ORDER BY conversation_id''',
      (tenant, channel, [JID, LID, LEGACY]))
    db.commit()
    return rows


@pytest.mark.parametrize('first,outbound', [(LID, False), (LID, True), (JID, False)])
def test_late_pair_preserves_first_route_history_and_replays(ingest, channels, first, outbound):
    db = channels['db']
    initial = event(first, contactName='Synthetic contact')
    assert ingest(initial)
    old = direct_routes(db)[0]
    opposite = JID if first == LID else LID
    linked = event(opposite, pair=PAIR, outbound=outbound)
    assert ingest(linked)
    assert ingest(initial) and ingest(linked)  # retries after identity discovery
    fresh_envelope = {**initial, 'id': str(uuid.uuid4())}
    assert ingest(fresh_envelope)  # same message ID with a new webhook event ID
    routes = direct_routes(db)
    assert len(routes) == 1
    assert (routes[0]['conversation_id'], routes[0]['lead_id']) == (first, old['lead_id'])
    listed = [r for r in dialogs.listing(PROFILE)['dialogs'] if r['lead_id'] == old['lead_id']]
    assert len(listed) == 1
    page = dialogs.history(PROFILE, 'connect', old['lead_id'], CA, first)
    assert len(page['messages']) == 2  # identical text on two real sends survives
    assert {m['external_id'] for m in page['messages']} == {
        'connect:' + initial['data']['id'], 'connect:' + linked['data']['id']}
    stored = db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                        (A, 'connect:' + linked['data']['id']))[0]['raw']
    assert stored['originalConversationId'] == opposite
    assert stored['directIdentity'] == PAIR
    db.commit()


@pytest.mark.parametrize('partial', [{'lid': LID}, {'jid': JID}, {'lid': LID, 'jid': None}])
def test_partial_pair_reuses_persisted_identity(ingest, channels, partial):
    assert ingest(event(JID, pair=PAIR))
    assert ingest(event(LID, pair=partial))
    assert len(direct_routes(channels['db'])) == 1


@pytest.mark.parametrize('first,second', [(JID, LEGACY), (LEGACY, JID)])
def test_provider_phone_domains_have_one_route(ingest, channels, first, second):
    assert ingest(event(first))
    before = direct_routes(channels['db'])[0]['lead_id']
    assert ingest(event(second, outbound=True))
    rows = direct_routes(channels['db'])
    assert len(rows) == 1 and rows[0]['lead_id'] == before


def test_provider_conflict_never_authorizes_a_new_pair(ingest, channels):
    assert ingest(event(LID, pair=PAIR, conversationIdentityConflict=True))
    db = channels['db']
    assert direct_routes(db)[0]['conversation_id'] == LID
    assert not db.execute('''SELECT 1 FROM saas_connect_direct_identities WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND identity_key=%s''', (A, CA, JID))
    db.commit()


def test_same_identity_other_channel_stays_distinct(ingest, channels):
    assert ingest(event(LID))
    assert ingest(event(JID, pair=PAIR))
    assert ingest(event(JID, pair=PAIR, channel=CB))
    a, b = direct_routes(channels['db']), direct_routes(channels['db'], channel=CB)
    assert len(a) == len(b) == 1 and a[0]['lead_id'] != b[0]['lead_id']


def test_concurrent_unpaired_receipt_and_pair_use_the_same_route(channels):
    db = channels['db']
    _, key = prepare_send(channels, CA)
    initial, linked = event(LID), event(JID, pair=PAIR, outbound=True)
    assert all(race(db, [('webhook', initial), ('webhook', linked), ('webhook', initial)], key))
    assert len(direct_routes(db)) == 1
    assert db.execute('''SELECT count(DISTINCT kommo_lead_id) AS routes,count(*) AS messages
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=ANY(%s::text[])''',
      (A, ['connect:' + p['data']['id'] for p in (initial, linked)])) == [{'routes': 1, 'messages': 2}]
    assert not db.execute('SELECT * FROM test_external_sends')
    db.commit()


def test_late_pair_preserves_contact_media_analysis_and_assignment(ingest, channels):
    db = channels['db']
    initial = event(LID, contactName='Synthetic contact', type='image')
    assert ingest(initial)
    before = direct_routes(db)[0]
    db.execute('''UPDATE saas_crm_messages SET raw=raw||%s::jsonb,media_url='private-fixture-media'
      WHERE tenant_id=%s::uuid AND external_id=%s''',
      (json.dumps({'media_analysis': {'status': 'completed', 'result': 'preserve me'}}),
       A, 'connect:' + initial['data']['id']))
    db.execute('''UPDATE saas_connect_conversations SET manual_display_name='Local name',
      manual_hold=TRUE,ai_enabled=FALSE WHERE tenant_id=%s::uuid AND lead_id=%s''', (A,before['lead_id']))
    message = db.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                         (A,'connect:' + initial['data']['id']))
    db.commit()
    assert ingest(event(JID, pair=PAIR))
    assert ingest(event(LID))  # persisted phone survives metadata-free receipts
    after = direct_routes(db)[0]
    assert after['phone'] == JID.split('@')[0] and after['conversation_kind'] == 'contact'
    assert after['lead_id'] == before['lead_id'] and after['contact_name'] == 'Synthetic contact'
    assert db.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                      (A,'connect:' + initial['data']['id'])) == message
    assert db.execute('''SELECT manual_display_name,manual_hold,ai_enabled FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND lead_id=%s''', (A,before['lead_id'])) == [
      {'manual_display_name':'Local name','manual_hold':True,'ai_enabled':False}]
    links=db.execute('''SELECT conversation_id FROM saas_crm_native_contact_conversations
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid''', (A,CA))
    assert links == [{'conversation_id': LID}]
    assert db.execute('SELECT count(*) AS n FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid',
                      (A,))[0]['n'] == 1
    db.commit()


@pytest.mark.parametrize('configured', ['phone', 'lid'])
def test_employee_recognition_survives_a_canonical_lid(ingest, channels, configured):
    db = channels['db']
    if configured == 'phone':
        db.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
          (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
          VALUES(%s::uuid,11,%s,TRUE,11)''', (A,JID.split('@')[0]))
    else:
        db.execute('''INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by)
          VALUES(%s::uuid,%s::uuid,%s,11,11)''', (A,CA,LID))
    db.commit()
    assert ingest(event(LID))
    assert ingest(event(JID, pair=PAIR))
    later = event(LID)
    assert ingest(later)
    assert db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
      (A,'connect:'+later['data']['id']))[0]['raw']['verified_employee_id'] == 11
    assert not db.execute('SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s',
                          (A,LID))
    db.commit()


def test_scoped_test_client_override_preserves_employee_provenance_without_human_handoff(ingest, channels, monkeypatch):
    import tenant_ai_pilot as pilot
    db = channels['db']
    db.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
      (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
      VALUES(%s::uuid,11,%s,TRUE,11)''', (A,JID.split('@')[0]))
    db.commit()
    monkeypatch.setattr(pilot, 'test_client_override_allowed', lambda *_: True)
    inbound = event(JID, senderName='Recognized staff member')
    assert ingest(inbound)
    stored = db.execute('''SELECT direction,raw FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND external_id=%s''', (A,'connect:'+inbound['data']['id']))[0]
    assert stored['direction'] == 'incoming'
    assert stored['raw']['verified_employee_id'] == 11
    assert stored['raw']['pilot_test_role_override'] == {
        'role':'test_client_override','verified_employee_id':11,
        'scope':'configured_scoped_live_dialog'}
    assert not db.execute('''SELECT 1 FROM saas_ai_conversation_state
      WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s
        AND state='human' AND reason='manager_replied' ''', (A,CA,JID))
    payload = db.execute('''SELECT payload FROM saas_tenant_webhook_events
      WHERE tenant_id=%s::uuid AND provider='connect' AND external_id=%s''',
      (A,inbound['data']['id']))[0]['payload']
    assert payload['source_role_override'] == 'test_client_override'
    db.commit()


@pytest.mark.parametrize('replay', ['new_message', 'same_message', 'same_event'])
def test_conflicting_pair_is_durable_even_on_replay(ingest, channels, replay):
    from tenant_connect_identity import conflicted
    import tenant_ai_agent_connect as active
    db = channels['db']
    assert ingest(event(LID))
    original = event(JID, pair=PAIR)
    assert ingest(original)
    before=db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                      (A,'connect:'+original['data']['id']))[0]['raw']
    db.commit()
    bad_phone='994700000092@s.whatsapp.net'
    bad = event(JID, pair={'jid':bad_phone,'lid':LID})
    if replay != 'new_message':
        bad['data']['id']=original['data']['id']
    if replay == 'same_event':
        bad['id']=original['id']
    assert ingest(bad)
    assert ingest(bad)
    with db.cursor() as cur:
        assert conflicted(cur,A,CA,LID)
        assert not conflicted(cur,A,CB,LID)
        assert not conflicted(cur,B,CA,LID)
    assert active.allowed({'provider':'connect','tenant_id':A,'channel_id':CA,
                          'conversation_id':LID},connection=db) is False
    assert len(direct_routes(db)) == 1
    if replay != 'new_message':
        assert db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                          (A,'connect:'+original['data']['id']))[0]['raw'] == before
    db.commit()


def test_conflicting_new_phone_is_not_linked_to_known_lid(ingest, channels):
    assert ingest(event(JID, pair=PAIR))
    other='994700000092@s.whatsapp.net'
    assert ingest(event(LID, pair={'jid':other,'lid':LID}))
    db=channels['db']
    assert not db.execute('''SELECT 1 FROM saas_connect_direct_identities WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND identity_key=%s''',(A,CA,other))
    assert len(direct_routes(db)) == 1


def test_transaction_failure_rolls_back_identity_route_message_and_receipt(ingest, channels, monkeypatch):
    import tenant_ai_agent_connect as active
    original=active.enqueue
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic queue write failure')
    monkeypatch.setattr(active,'enqueue',fail)
    payload=event(LID,pair=PAIR)
    with pytest.raises(RuntimeError,match='synthetic queue'):
        ingest(payload)
    db=channels['db']
    assert not direct_routes(db)
    assert not db.execute('SELECT 1 FROM saas_connect_direct_identities WHERE tenant_id=%s::uuid',(A,))
    assert not db.execute('SELECT 1 FROM saas_connect_webhook_events WHERE tenant_id=%s::uuid AND event_id=%s',
                          (A,payload['id']))
    db.commit()
    monkeypatch.setattr(active,'enqueue',original)
    assert ingest(payload) and ingest(payload)
    assert len(direct_routes(db)) == 1


def test_unknown_commit_outcome_is_not_automatically_retried_but_provider_replay_is_safe(ingest, channels, monkeypatch):
    original=transport._ingest_once
    calls=[]
    def lost_response(*args):
        calls.append(True)
        original(*args)
        raise ConnectionError('synthetic lost response after commit')
    monkeypatch.setattr(transport,'_ingest_once',lost_response)
    payload=event(LID,pair=PAIR)
    with pytest.raises(ConnectionError):
        ingest(payload)
    assert len(calls) == 1
    monkeypatch.setattr(transport,'_ingest_once',original)
    assert ingest(payload)
    db=channels['db']
    assert len(direct_routes(db)) == 1
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                      (A,'connect:'+payload['data']['id']))[0]['n'] == 1
    db.commit()


def test_no_provider_proof_does_not_merge_equal_names_or_photos(ingest, channels):
    assert ingest(event(LID,contactName='Same label',avatar='same.jpg'))
    assert ingest(event(JID,contactName='Same label',avatar='same.jpg'))
    db=channels['db']
    before=direct_routes(db)
    assert len(before) == 2  # deliberately insufficient evidence
    assert ingest(event(LID,pair=PAIR))
    assert [r['lead_id'] for r in direct_routes(db)] == [r['lead_id'] for r in before]
    from tenant_connect_identity import conflicted
    with db.cursor() as cur:
        assert conflicted(cur,A,CA,LID) and conflicted(cur,A,CA,JID)
    db.commit()


def _resolve_in_process(dsn, payload, ready, result):
    try:
        with psycopg.connect(dsn, row_factory=dict_row,
                             options='-c statement_timeout=10000') as conn:
            with conn.cursor() as cur:
                data=payload['data']
                metadata=transport._conversation_metadata(data,data['conversationId'])
                ready.set()
                canonical,conflict=transport._canonical_direct_conversation(
                    cur,A,CA,data['conversationId'],data,metadata)
                lead=transport._upsert_conversation(cur,A,CA,canonical,metadata)
        result.put(('ok',canonical,conflict,lead))
    except Exception as error:
        result.put(('error',type(error).__name__,str(error)))


@pytest.mark.parametrize('pair_first',[False,True])
def test_identity_reservation_blocks_competing_process_until_commit(channels,pair_first):
    db=channels['db']
    plain,paired=event(LID),event(JID,pair=PAIR)
    first,second=(paired,plain) if pair_first else (plain,paired)
    ctx=multiprocessing.get_context('spawn');ready=ctx.Event();result=ctx.Queue()
    with psycopg.connect(db.dsn,row_factory=dict_row) as held:
        with held.cursor() as cur:
            data=first['data'];metadata=transport._conversation_metadata(data,data['conversationId'])
            canonical,conflict=transport._canonical_direct_conversation(cur,A,CA,data['conversationId'],data,metadata)
            assert not conflict
            lead=transport._upsert_conversation(cur,A,CA,canonical,metadata)
        process=ctx.Process(target=_resolve_in_process,args=(db.dsn,second,ready,result))
        process.start()
        try:
            assert ready.wait(10)
            deadline=time.monotonic()+8;blocked=False
            while time.monotonic()<deadline:
                with psycopg.connect(db.dsn,autocommit=True) as monitor:
                    blocked=monitor.execute("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                      WHERE datname=current_database() AND wait_event='advisory'
                      AND pid<>pg_backend_pid())""").fetchone()[0]
                if blocked:break
                time.sleep(.02)
            assert blocked,'Contender did not wait for the uncommitted identity reservation'
            held.commit()
            assert result.get(timeout=15)==('ok',canonical,False,lead)
            process.join(5)
            assert process.exitcode==0
        finally:
            if process.is_alive():process.terminate();process.join(5)
    assert len(direct_routes(db))==1


def test_other_tenant_mapping_does_not_redirect_new_receipts(ingest,channels):
    db=channels['db']
    db.execute('''INSERT INTO saas_connect_channels(tenant_id,connect_channel_id)
      VALUES(%s::uuid,%s::uuid)''',(B,CA))
    db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id)
      VALUES(%s::uuid,%s::uuid,%s)''',(B,CA,JID))
    db.execute('''INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,
      identity_key,canonical_conversation_id) VALUES(%s::uuid,%s::uuid,%s,%s)''',(B,CA,LID,JID))
    db.commit()
    foreign=direct_routes(db,tenant=B)
    assert ingest(event(LID))
    assert direct_routes(db)[0]['conversation_id']==LID
    assert direct_routes(db,tenant=B)==foreign


def test_phone_domain_aliases_do_not_make_inventory_ambiguous(ingest,channels):
    from tenant_connect_identity import inventory
    assert ingest(event(LID))
    assert ingest(event(JID,pair=PAIR))
    assert ingest(event(LEGACY))
    with channels['db'].cursor() as cur:
        scan=inventory(cur,A,CA)
    assert scan['pairs']
    assert all('provider_identity_ambiguous' not in p['conflicts'] for p in scan['pairs'])
    channels['db'].commit()


def test_second_lid_is_quarantined_not_silently_joined(ingest,channels):
    from tenant_connect_identity import conflicted
    assert ingest(event(JID,pair=PAIR))
    other='80000000000092@lid'
    assert ingest(event(JID,pair={'jid':JID,'lid':other}))
    db=channels['db']
    assert not db.execute('''SELECT 1 FROM saas_connect_direct_identities WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND identity_key=%s''',(A,CA,other))
    with db.cursor() as cur:assert conflicted(cur,A,CA,JID)
    db.commit()


def test_conflicting_pair_quarantines_both_canonical_lids_and_listing(ingest,channels):
    from tenant_connect_identity import conflicted
    db=channels['db'];other_jid='994700000092@s.whatsapp.net';other_lid='80000000000092@lid'
    for jid,lid in ((JID,LID),(other_jid,other_lid)):
        assert ingest(event(lid))
        assert ingest(event(jid,pair={'jid':jid,'lid':lid}))
    assert ingest(event(LID,pair={'jid':other_jid,'lid':LID}))
    with db.cursor() as cur:
        assert conflicted(cur,A,CA,LID)
        assert conflicted(cur,A,CA,other_lid)
        assert not conflicted(cur,A,CB,other_lid)
    db.commit()
    rows=[r for r in dialogs.listing(PROFILE)['dialogs'] if r['conversation_id'] in {LID,other_lid}]
    assert len(rows)==2
    assert all(r['agent_status_reason']=='conversation_identity_conflict' for r in rows)


def test_conflicting_employee_aliases_do_not_admit_customer_ai(ingest,channels):
    from tenant_connect_identity import conflicted
    db=channels['db']
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,33,'worker','Other')",(A,))
    db.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
      (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
      VALUES(%s::uuid,11,%s,TRUE,11)''',(A,JID.split('@')[0]))
    db.execute('''INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by)
      VALUES(%s::uuid,%s::uuid,%s,33,11)''',(A,CA,LID))
    db.commit()
    assert ingest(event(LID))
    assert ingest(event(JID,pair=PAIR))
    with db.cursor() as cur:assert conflicted(cur,A,CA,LID)
    assert not db.execute('SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s',(A,LID))
    db.commit()


def test_signed_http_handler_to_single_list_card_and_full_history(channels,monkeypatch):
    from tenant_connect_api import webhook_handler
    db=channels['db'];secret='isolated-signing-fixture'
    encrypted=transport._fernet().encrypt(json.dumps({'api_key':'synthetic','webhook_secret':secret}).encode())
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",
               (encrypted,A));db.commit()
    monkeypatch.setenv('AI_AGENTS_ENABLED','false')
    monkeypatch.setenv('AI_SIMULATION_ENABLED','false')
    handler=webhook_handler(Mock())
    class Request:
        def __init__(self,payload,valid=True):
            self.body=json.dumps(payload).encode()
            signature=hmac.new(secret.encode(),self.body,hashlib.sha256).hexdigest()
            self.headers={'x-connect-signature-256':'sha256='+signature if valid else 'sha256=invalid'}
        async def read(self):return self.body
    first,second=event(LID,contactName='Synthetic signed contact'),event(JID,pair=PAIR,outbound=True)
    assert asyncio.run(handler(Request(first,False))).status==401
    assert not direct_routes(db)
    for payload in (first,second,first,second):
        assert asyncio.run(handler(Request(payload))).status==200
    route=direct_routes(db)[0]
    rows=[r for r in dialogs.listing(PROFILE)['dialogs'] if r['conversation_id'] in {JID,LID}]
    assert len(rows)==1 and rows[0]['lead_id']==route['lead_id']
    history=dialogs.history(PROFILE,'connect',route['lead_id'],CA,route['conversation_id'])
    assert len(history['messages'])==2


def test_outbound_replay_with_new_envelope_does_not_repeat_takeover(ingest,channels,monkeypatch):
    import tenant_ai_agent_connect as active
    mark=Mock();monkeypatch.setattr(active,'mark_operator_reply',mark)
    original=event(LID,outbound=True)
    assert ingest(original)
    assert ingest({**original,'id':str(uuid.uuid4())})
    mark.assert_called_once()
    assert ingest(event(JID,pair=PAIR,outbound=True))
    assert mark.call_count==2  # a separate legitimate same-text send still acts
    assert len(direct_routes(channels['db']))==1


def test_first_unknown_outbound_observation_saves_paused_agent_binding(ingest,channels):
    payload=event(JID,outbound=True,source='unknown')
    assert ingest(payload)
    db=channels['db']
    state=db.execute("""SELECT agent_id,state,reason FROM saas_ai_conversation_state
      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s""",(A,CA,JID))[0]
    assert {**state,'agent_id':str(state['agent_id'])}=={'agent_id':str(uuid.UUID(int=301)),'state':'paused','reason':'automatic_assignment_pending'}
    assert db.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s AND state IN ('queued','processing')",(A,CA,JID))==[]
    assert channels['send'].call_count==0 and channels['generate'].call_count==0


def test_crm_outbound_record_retry_does_not_repeat_takeover(ingest,channels,monkeypatch):
    import tenant_ai_agent_connect as active
    assert ingest(event(LID,pair=PAIR))
    route=direct_routes(channels['db'])[0]
    mark=Mock();monkeypatch.setattr(active,'mark_operator_reply',mark)
    transport_route={'channel_id':CA,'conversation_id':route['conversation_id']}
    for _ in range(2):
        transport.record_outbound(A,route['lead_id'],transport_route,'same reply','stable-client-message-id')
    mark.assert_called_once()


def owner_merge_fixture(ingest,channels):
    import tenant_connect_owner_identity as owner
    import tenant_ai_dialogue_merge as merge
    from test_connect_dialog_equivalence import cfg
    db=channels['db'];merge.ensure_schema(db);db.commit()
    originals=[event(JID),event(LID)]
    for payload in originals:assert ingest(payload)
    routes={r['conversation_id']:r for r in direct_routes(db)}
    db.execute('''UPDATE saas_connect_conversations SET manual_hold=TRUE,ai_enabled=FALSE
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s::text[])''',(A,CA,[JID,LID]))
    db.commit()
    arguments={'canonical_lead_id':routes[JID]['lead_id'],'source_lead_id':routes[LID]['lead_id'],
               'phone':'+'+JID.split('@')[0],'evidence_ref':'test:exact-owner-confirmation-ingestion'}
    with db.cursor() as cur:
        item=owner.preview(cur,cfg(),arguments)
        assert item['eligible'],item
        result=owner.execute(cur,cfg(),{**arguments,'precondition':item['precondition'],
                    'confirm':True,'request_id':str(uuid.uuid4())})
    db.commit()
    return routes,result,originals


def test_owner_redirect_never_becomes_provider_phone_or_employee_proof(ingest,channels):
    from tenant_connect_phone_evidence import collect
    from test_connect_dialog_equivalence import cfg
    import tenant_ai_dialogue_merge as merge
    db=channels['db'];routes,result,originals=owner_merge_fixture(ingest,channels)
    aliases=db.execute('SELECT * FROM saas_connect_direct_identities ORDER BY identity_key');db.commit()
    db.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
      (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
      VALUES(%s::uuid,11,%s,TRUE,11)''',(A,JID.split('@')[0]));db.commit()
    later=event(LID,type='image',_crm_provider_conversation_id=JID,
                _crm_owner_assertion_id='forged-owner-assertion')
    assert ingest(later) and ingest(later)
    for original in originals:assert ingest(original)
    stored=db.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',
                      (A,'connect:'+later['data']['id']))[0]
    assert stored['kommo_lead_id']==routes[JID]['lead_id']
    assert stored['raw']['_crm_provider_conversation_id']==LID
    assert stored['raw']['_crm_owner_assertion_id']==result['assertion_id']
    assert 'verified_employee_id' not in stored['raw']
    deal=db.execute('SELECT phone FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s',
                    (A,routes[JID]['lead_id']))[0]
    assert deal['phone']==JID.split('@')[0]  # Missing LID phone cannot erase the canonical contact's phone.
    assert not db.execute('''SELECT 1 FROM saas_crm_native_contact_conversations WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND conversation_id=%s''',(A,CA,LID))
    with db.cursor() as cur:
        assert collect(cur,A,CA)['phones_by_identity'][LID]==set()
        source=next(r for r in merge.preview(cur,cfg(),{})['routes'] if r['lead_id']==routes[LID]['lead_id'])
        assert source['provider_phone_proven'] is False
    assert db.execute('SELECT * FROM saas_connect_direct_identities ORDER BY identity_key')==aliases
    db.commit()
    listed=[r for r in dialogs.listing(PROFILE)['dialogs'] if r['lead_id'] in {x['lead_id'] for x in routes.values()}]
    assert len(listed)==1
    history=dialogs.history(PROFILE,'connect',routes[LID]['lead_id'],CA,LID)['messages']
    assert len(history)==3
    image=next(m for m in history if m['external_id']=='connect:'+later['data']['id'])
    assert image['source_conversation_id']==JID  # authorised CRM route; provider media route remains in raw
    # Resolver coverage below is exercised by the retained-media suite; the
    # server-owned provider marker is asserted here at the ingestion boundary.


def test_owner_merge_genuine_pair_then_rollback_keeps_provider_evidence_and_late_history(ingest,channels):
    import tenant_ai_dialogue_merge as merge
    from tenant_connect_phone_evidence import collect
    from test_connect_dialog_equivalence import cfg,command
    db=channels['db'];routes,result,originals=owner_merge_fixture(ingest,channels)
    later=event(LID,pair=PAIR)
    assert ingest(later)
    assert ingest(later)
    with db.cursor() as cur:
        assert collect(cur,A,CA)['phones_by_identity'][LID]=={JID.split('@')[0]}
        item=merge.preview(cur,cfg(),{'pair_id':result['pair_id']})['items'][0]
    aliases=db.execute('SELECT * FROM saas_connect_direct_identities ORDER BY identity_key')
    messages=db.execute('SELECT * FROM saas_crm_messages ORDER BY id');db.commit()
    with db.cursor() as cur:rolled=merge.execute(cur,cfg(),command(item,'rollback'))
    db.commit()
    assert rolled['state']=='rolled_back'
    assert db.execute('SELECT * FROM saas_connect_direct_identities ORDER BY identity_key')==aliases
    assert db.execute('SELECT * FROM saas_crm_messages ORDER BY id')==messages
    with db.cursor() as cur:assert collect(cur,A,CA)['phones_by_identity'][LID]=={JID.split('@')[0]}
    db.commit()


def test_retained_source_history_prevents_repeated_first_inquiry(ingest,channels,monkeypatch):
    db=channels['db'];routes,result,originals=owner_merge_fixture(ingest,channels)
    # Explicit local synthetic resume. A merge itself must never resume AI.
    db.execute("UPDATE saas_crm_messages SET direction='outgoing' WHERE tenant_id=%s::uuid AND kommo_lead_id=%s",(A,routes[JID]['lead_id']))
    db.execute('''UPDATE saas_connect_conversations SET manual_hold=FALSE,ai_enabled=TRUE
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',(A,CA,JID))
    db.commit();monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    later=event(LID)
    assert ingest(later)
    saved=db.execute("SELECT payload FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND payload->>'message_id'=%s",(A,later['data']['id']))
    assert len(saved)==1
    assert saved[0]['payload']['first_inquiry'] is False
    assert not channels['send'].called and not channels['generate'].called
    db.commit()


@pytest.mark.parametrize('replay',[False,True])
def test_owner_merge_new_provider_contradiction_quarantines_both_routes(ingest,channels,replay):
    from tenant_connect_identity import conflicted
    db=channels['db'];routes,result,originals=owner_merge_fixture(ingest,channels)
    bad=event(LID,pair={'jid':'994700000092@s.whatsapp.net','lid':LID})
    if replay:bad['data']['id']=originals[1]['data']['id'];bad['id']=originals[1]['id']
    assert ingest(bad) and ingest(bad)
    with db.cursor() as cur:
        assert conflicted(cur,A,CA,JID) and conflicted(cur,A,CA,LID)
    db.commit()
    listed=[r for r in dialogs.listing(PROFILE)['dialogs'] if r['lead_id']==routes[JID]['lead_id']]
    assert len(listed)==1 and listed[0]['agent_status_reason']=='conversation_identity_conflict'
    assert not channels['send'].called and not channels['generate'].called


@pytest.mark.parametrize('scenario',['lid_first','phone_first'])
def test_actual_connect_dispatcher_signed_fixtures_reach_one_worker_dialogue(channels,monkeypatch,scenario):
    """Fixture produced by Connect normalizer/store/WebhookDispatcher, not a
    handwritten approximation of the cross-repository protocol."""
    from pathlib import Path
    from tenant_connect_api import webhook_handler
    from tenant_connect_phone_evidence import collect
    fixture=json.loads((Path(__file__).parent/'fixtures/connect_identity_signed.json').read_text())
    journey=next(f for f in fixture['fixtures'] if f['scenario']==scenario)
    db=channels['db'];channel=journey['channelId'];pair=fixture['pair']
    db.execute('INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,%s)',
               (A,channel,'Synthetic Connect fixture'))
    secret=transport._fernet().encrypt(json.dumps({'api_key':'synthetic','webhook_secret':'synthetic-fixture-secret'}).encode())
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",(secret,A));db.commit()
    monkeypatch.setenv('AI_AGENTS_ENABLED','false');monkeypatch.setenv('AI_SIMULATION_ENABLED','false')
    handler=webhook_handler(Mock())
    class Request:
        def __init__(self,signed):
            self.body=signed['body'].encode();self.headers={'x-connect-signature-256':signed['signature']}
        async def read(self):return self.body
    for signed in journey['signed']:
        assert asyncio.run(handler(Request(signed))).status==200
    routes=db.execute('''SELECT conversation_id,lead_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid''',(A,channel))
    assert len(routes)==1 and routes[0]['conversation_id']==journey['canonical']
    with db.cursor() as cur:assert collect(cur,A,channel)['phones_by_identity'][journey['canonical']]=={pair['jid'].split('@')[0]}
    db.commit()
    listed=[r for r in dialogs.listing(PROFILE)['dialogs'] if r['channel_id']==channel]
    assert len(listed)==1
    history=dialogs.history(PROFILE,'connect',routes[0]['lead_id'],channel,journey['canonical'])['messages']
    unique={s['event']['data']['id'] for s in journey['signed']}
    assert len(history)==len(unique)==4
    assert {m['external_id'] for m in history}=={'connect:'+i for i in unique}
    rows=db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s',(A,'connect:'+channel))
    assert any(r['raw'].get('directIdentitySource')=='persisted_provider_alias' for r in rows)
    assert all(not r['raw'].get('conversationIdentityConflict') for r in rows)
    assert not channels['send'].called and not channels['generate'].called
    db.commit()
