"""Persisted call correlation and cursor history; isolated PostgreSQL only."""
import json
import uuid
import pytest
from test_browser_calls import gateway, snapshot, provider, command
from test_call_recordings import pilot, bundle, PEER, CONVERSATION
from test_tenant_support_inbox import database, db, PROFILE, A, B, CA, CB
import tenant_browser_calls as calls
import tenant_operator_dialogs as dialogs


@pytest.fixture
def recorded(gateway, monkeypatch):
    db, key, cfg = gateway
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    calls.sync(cfg, snapshot(2, active=[provider()]))
    call = calls.status(PROFILE)['calls'][0]
    calls.action(PROFILE, command('accept', call_id=call['id']))
    work = calls.sync(cfg, snapshot(3))['commands'][0]
    calls.sync(cfg, snapshot(4, ended=[provider('ended')], results=[{
        'id': work['id'], 'ok': True, 'provider_id': 'provider-test-1'}]))
    saved = calls.save_recording(cfg, call['id'], bundle(key, call_id='provider-test-1')[0])
    return db, call['id'], saved['id']


def observation(db, *, status='observed', call_id='provider-test-1', conversation=CONVERSATION,
                video=False, age=0):
    message_id = str(uuid.uuid4())
    raw = json.dumps({'externalCallId': call_id, 'conversationId': conversation,
                      'callStatus': status, 'isVideo': video})
    db.execute('''INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,happened_at,raw)
      VALUES(%s::uuid,%s::uuid,41,%s,'system',%s,'WhatsApp','',%s,
        now()-(%s*interval '1 second'),%s::jsonb)''',
      (message_id, A, 'connect:call:'+message_id, 'connect:'+CA, 'call_'+status, age, raw))
    db.commit()
    return message_id


def history(**kwargs):
    return dialogs.history(PROFILE, 'connect', 41, CA, CONVERSATION, **kwargs)


def test_only_exact_recorded_observation_is_replaced(recorded):
    db, call_id, record_id = recorded
    duplicate = observation(db)
    keep = {observation(db, status='missed'), observation(db, status='rejected'),
            observation(db, call_id='another-call'), observation(db, conversation=PEER),
            observation(db, video=True)}
    page = history()
    ids = {m['id'] for m in page['messages']}
    assert duplicate not in ids and keep <= ids
    assert len([m for m in page['messages'] if m['message_type']=='call_recording']) == 1
    # Repeated refresh never deletes the original evidence or changes the call.
    assert ids == {m['id'] for m in history()['messages']}
    assert db.execute('SELECT id FROM saas_crm_messages WHERE id=%s::uuid', (duplicate,))
    db.commit()


def test_recording_replacement_requires_every_persisted_binding(recorded):
    db, call_id, record_id = recorded
    observed = observation(db)
    assert observed not in {m['id'] for m in history()['messages']}
    # A caller ID, lead, name or timestamp alone cannot suppress an event. No
    # recording from another tenant/channel/dialog (or missing timeline) counts.
    mutations = [
        ("UPDATE saas_browser_calls SET recording_id=NULL WHERE id=%s::uuid", (call_id,)),
        ("UPDATE saas_browser_calls SET provider_id='different' WHERE id=%s::uuid", (call_id,)),
        ("UPDATE saas_test_call_recordings SET browser_call_id=NULL WHERE id=%s::uuid", (record_id,)),
        ("UPDATE saas_test_call_recordings SET tenant_id=%s::uuid WHERE id=%s::uuid", (B, record_id)),
        ("UPDATE saas_test_call_recordings SET channel_id=%s::uuid WHERE id=%s::uuid", (CB, record_id)),
        ("UPDATE saas_test_call_recordings SET lead_id=42 WHERE id=%s::uuid", (record_id,)),
        ("UPDATE saas_test_call_recordings SET conversation_id='another' WHERE id=%s::uuid", (record_id,)),
        ("UPDATE saas_crm_messages SET raw=raw||'{\"conversationId\":\"another\"}'::jsonb WHERE message_type='call_recording'", ()),
        ("UPDATE saas_crm_messages SET raw=raw||'{\"recording_id\":\"wrong\"}'::jsonb WHERE message_type='call_recording'", ()),
        ("UPDATE saas_crm_messages SET external_id='unbound' WHERE message_type='call_recording'", ()),
        ("UPDATE saas_crm_messages SET channel=%s WHERE message_type='call_recording'", ('connect:'+CB,)),
        ("UPDATE saas_crm_messages SET kommo_lead_id=42 WHERE message_type='call_recording'", ()),
    ]
    # Query the same production SQL predicate inside savepoints so each negative
    # case remains independent without mutating the authorized route itself.
    for sql, args in mutations:
        db.execute('SAVEPOINT binding_case')
        db.execute(sql, args)
        rows = db.execute('SELECT m.id FROM saas_crm_messages m WHERE m.id=%s::uuid'
                          + dialogs._RECORDED_CALL_OBSERVATION_FILTER, (observed,))
        assert rows, sql
        db.execute('ROLLBACK TO SAVEPOINT binding_case')
    db.commit()


def test_replacement_is_independent_of_cursor_page(recorded):
    db, call_id, record_id = recorded
    original = {m['id'] for m in history()['messages']}
    duplicate = observation(db, age=120)
    keep = {observation(db, call_id='unrecorded-'+str(i), age=i+2) for i in range(60)}
    keep.add(observation(db, call_id='older-unrecorded', age=121))
    db.execute("""UPDATE saas_crm_messages SET happened_at=now()-interval '30 seconds'
      WHERE raw->>'externalCallId' LIKE 'unrecorded-%%'""")
    db.commit()  # equal timestamps still paginate by the message UUID
    seen = []; before = None
    while True:
        page = history(before=before)
        seen.extend(m['id'] for m in page['messages'])
        before = page['next_before']
        if not before: break
        assert len(seen) <= 100
    assert len(seen) == len(set(seen))
    assert duplicate not in seen and keep <= set(seen)
    assert set(seen) == keep | original  # recording survives on a different page
