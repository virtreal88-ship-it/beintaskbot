import uuid
import pytest
from unittest.mock import Mock
from test_connect_identity_ingestion_postgres import (A, B, CA, CB, PROFILE, database, db, flow, channels, identity_fixture, ingest)
from test_connect_missed_calls import call, rejected
import tenant_operator_dialogs as dialogs
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
    assert forbidden.call_count==0
    assert ingest(factory(channelId=CB))
    assert len(db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type=%s",(A,kind)))==2
    assert not db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type=%s",(B,kind))

def test_call_inactive_channel_is_not_stored(ingest,channels):
    assert ingest(call(channelId=str(uuid.uuid4()))) is False
