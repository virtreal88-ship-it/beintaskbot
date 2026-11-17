import json
import uuid
from unittest.mock import Mock
import pytest
from test_connect_missed_calls import call, rejected
from tenant_connect_calls import validate, record
import tenant_connect_dialog_equivalence as equivalence


def invalidated(**changes):
    event = call(callStatus='invalidated', callRevision=2, correctionOf='call.rejected', **changes)
    event['type'] = 'call.invalidated'
    return event


@pytest.mark.parametrize('event', [invalidated(), rejected(callRevision=1), call(callRevision=1)])
def test_fixed_revision_protocol(event):
    assert validate(event)


@pytest.mark.parametrize('change', [dict(callRevision=1), dict(callRevision=3), dict(callRevision=True),
    dict(callRevision='2'), dict(correctionOf='call.missed'), dict(correctionOf=None)])
def test_invalid_correction_rejected(change):
    event = invalidated(); event['data'].update(change)
    with pytest.raises(ValueError): validate(event)


@pytest.mark.parametrize('change', [dict(callRevision=2), dict(callRevision=True), dict(correctionOf='call.rejected')])
def test_rejected_cannot_claim_correction_revision(change):
    with pytest.raises(ValueError): validate(rejected(**change))


def apply(monkeypatch, previous, incoming):
    monkeypatch.setattr(equivalence, 'lead_members', lambda *args: {42})
    # Unit cases have no persisted alias proof unless explicitly supplied.
    monkeypatch.setattr('tenant_connect_direct_identity.established_canonical_conversation', lambda *args: '')
    cur = Mock(); cur.fetchone.side_effect = [None, {'channel': 'connect:channel', 'kommo_lead_id': 42, 'raw': previous}]
    result = record(cur, str(uuid.uuid4()), 'channel', incoming['conversationId'], 42, incoming, '2026-11-15T09:00:00Z')
    return cur, result


def test_late_invalidation_updates_same_locked_row_and_retains_provenance(monkeypatch):
    cur,result=apply(monkeypatch,rejected()['data'],invalidated()['data'])
    assert result is False
    assert 'FOR UPDATE' in cur.execute.call_args_list[1].args[0]
    statement, params = cur.execute.call_args.args
    assert "message_type='call_invalidated'" in statement
    raw = json.loads(params[0])
    assert raw['callRevision'] == 2 and raw['_crm_call_correction']['previousStatus'] == 'rejected'
    assert 'happened_at=' not in statement


@pytest.mark.parametrize('incoming', [rejected(), invalidated()])
def test_invalidated_tombstone_blocks_old_retry_and_duplicate_update(monkeypatch,incoming):
    cur,result=apply(monkeypatch,invalidated()['data'],incoming['data'])
    assert result is False and cur.execute.call_count == 2


def test_invalidation_before_original_inserts_tombstone():
    cur=Mock();cur.fetchone.return_value={'id':'new'}
    event=invalidated()['data']
    assert record(cur,str(uuid.uuid4()),'channel',event['conversationId'],42,event,'2026-11-15T09:00:00Z')
    assert cur.execute.call_args.args[1][5]=='call_invalidated'


@pytest.mark.parametrize('previous,incoming', [(call(),invalidated()),(invalidated(),call()),
    (rejected(isVideo=True),invalidated()),(rejected(externalCallId='other'),invalidated()),
    (rejected(conversationId='994700000099@s.whatsapp.net'),invalidated())])
def test_corrections_never_rewrite_other_status_or_identity(monkeypatch,previous,incoming):
    with pytest.raises(ValueError,match='call scope conflict'):apply(monkeypatch,previous['data'],incoming['data'])


def test_invalidated_never_enters_ai_context():
    from tenant_chat_ai_policy import history
    lines,missing=history([{'message_type':'call_invalidated','direction':'system','body':'','has_attachment':True}]*40 +
        [{'message_type':'text','direction':'incoming','body':'Hello'}],{})
    assert missing==0 and len(lines)==1 and lines[0]['text']=='Hello'


@pytest.mark.parametrize('established,canonical,expected', [
    ('994700000091@s.whatsapp.net','994700000091@s.whatsapp.net',True),
    ('80000000000091@lid','994700000091@s.whatsapp.net',True),
    ('','994700000091@s.whatsapp.net',False),
    ('other','other',False)])
def test_only_confirmed_source_local_alias_or_link_can_match(monkeypatch,established,canonical,expected):
    from tenant_connect_calls import _same_stored_route
    called=[]
    def resolve(cur,tenant,channel,previous):
        called.append((tenant,channel,previous));return established
    monkeypatch.setattr('tenant_connect_direct_identity.established_canonical_conversation',resolve)
    monkeypatch.setattr('tenant_connect_dialog_equivalence.canonical',lambda *args:canonical)
    assert _same_stored_route(Mock(),'tenant','channel','80000000000091@lid','994700000091@s.whatsapp.net') is expected
    assert called==[('tenant','channel','80000000000091@lid')]
