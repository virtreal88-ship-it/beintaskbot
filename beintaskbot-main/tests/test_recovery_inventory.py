import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,B,CA,PROFILE
from scripts import audit_whatsapp_recovery as audit


def test_same_names_or_numeric_lids_are_not_evidence():
    assert audit.pairs([{'id':'one','external_id':'connect:one','raw':{'contactName':'Same name','phone':'994501234567','conversationId':'123456789@lid'}}])==[]
    assert audit.pairs([{'raw':['malformed']}])==[]


def test_only_same_event_pair_is_unambiguous_and_contradictions_are_retained():
    lid='123456789@lid';jid='994501234567@s.whatsapp.net'
    def event(id,lid,jid):return {'id':id,'external_id':'connect:'+id,'raw':{'directIdentity':{'lid':lid,'jid':jid},'conversationId':lid}}
    rows=audit.pairs([event('one',lid,jid)])
    assert rows[0]['identity_unambiguous'] and rows[0]['evidence_message_ids']==['one']
    rows=audit.pairs([event('one',lid,jid),event('two',lid,'994507654321@s.whatsapp.net')])
    assert len(rows)==2 and all(not row['identity_unambiguous'] for row in rows)
    assert not audit.pairs([event('group','123456789@g.us',jid)])


def test_inventory_uses_real_read_only_transaction_and_exact_tenant(db,monkeypatch):
    monkeypatch.setattr(audit,'PILOT_TENANT',A);monkeypatch.setattr(audit,'PILOT_CHANNEL',CA)
    db.commit()
    result=audit.inventory(db,PROFILE)
    assert result['writes_performed'] is False and result['proven_split_count']==0
    assert all(str(row['connect_channel_id'])==CA for row in result['channels'])
    db.rollback()
    with pytest.raises(ValueError):audit.inventory(db,{'tenant_id':B,'telegram_id':22})
    db.rollback()
    with pytest.raises(PermissionError):audit.inventory(db,{'tenant_id':A,'telegram_id':22})
    db.rollback()
