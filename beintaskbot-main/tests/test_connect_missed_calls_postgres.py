import uuid
import pytest
from unittest.mock import Mock
from test_connect_identity_ingestion_postgres import (A, B, CA, CB, PROFILE, database, db, flow, channels, identity_fixture, ingest)
from test_connect_missed_calls import call, rejected
import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError
import tenant_ai_agent_connect as ai

@pytest.mark.parametrize('factory,kind',[(call,'call_missed'),(rejected,'call_rejected')])
def test_missed_calls_deduplicate_and_never_trigger_ai(ingest,channels,monkeypatch,factory,kind):
    forbidden=Mock(side_effect=AssertionError('calls must not enqueue AI or pause operator'))
    for name in ('enqueue','mark_operator_reply','bind_unassigned_direct_agent'):
        monkeypatch.setattr(ai,name,forbidden)
    event=factory(channelId=CA)
    assert ingest(event) and ingest(event)
    assert ingest({**event,'id':str(uuid.uuid4())})
    db=channels['db']
    rows=db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type=%s",(A,kind))
    assert len(rows)==1 and rows[0]['direction']=='system'
    page=dialogs.history(PROFILE,'connect',rows[0]['kommo_lead_id'],CA,event['data']['conversationId'])
    assert len([m for m in page['messages'] if m['message_type']==kind])==1
    listing=dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)
    projected=next(r for r in listing['dialogs'] if r['lead_id']==rows[0]['kommo_lead_id'])
    if kind=='call_missed':
        assert projected['unread_count']==listing['unread_count']==1
        assert page['read_through_message_id']==str(rows[0]['id'])
        dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=rows[0]['kommo_lead_id'],channel_id=CA,
            conversation_id=event['data']['conversationId'],message_id=page['read_through_message_id']))
        assert next(r for r in dialogs.listing(PROFILE,lifecycle='all',channel='connect:'+CA)['dialogs']
                    if r['lead_id']==rows[0]['kommo_lead_id'])['unread_count']==0
    else:
        assert projected['unread_count']==listing['unread_count']==0
        assert page['read_through_message_id'] is None
        with pytest.raises(TenantLinearError,match='Сообщение не найдено'):
            dialogs.mark_read(PROFILE,dict(provider='connect',lead_id=rows[0]['kommo_lead_id'],channel_id=CA,
                conversation_id=event['data']['conversationId'],message_id=str(rows[0]['id'])))
    assert forbidden.call_count==0
    assert ingest(factory(channelId=CB))
    assert len(db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type=%s",(A,kind)))==2
    assert not db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type=%s",(B,kind))

def test_call_inactive_channel_is_not_stored(ingest,channels):
    assert ingest(call(channelId=str(uuid.uuid4()))) is False
