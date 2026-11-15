import json
import uuid
import pytest
from tenant_connect_calls import validate
import tenant_connect_transport as transport
from tenant_operator_dialogs import _message

def call(**changes):
    return {'id':str(uuid.uuid4()),'type':'call.missed','occurred_at':'2026-11-15T09:00:00Z',
      'data':{'id':'call:example','externalCallId':'ABC123','channelId':str(uuid.uuid4()),
      'conversationId':'994700000091@s.whatsapp.net','type':'call','direction':'incoming',
      'callStatus':'missed','isVideo':False,'isGroup':False,**changes}}

def test_strict_call_contract():
    assert validate(call()) == '2026-11-15T09:00:00+00:00'
    assert validate(call(isVideo=True,conversationId='80000000000091@lid'))

@pytest.mark.parametrize('facts',[
    {'callStatus':'offer'},{'callStatus':'reject'},{'callStatus':'terminate'},
    {'direction':'outgoing'},{'fromMe':True},{'isVideo':'false'},
    {'isGroup':True},{'conversationType':'group'},{'chatType':'group'},{'conversationId':'12345@g.us'},{'conversationId':'unknown'},
    {'externalCallId':''},{'externalCallId':'<script>'},{'type':'text'}])
def test_invalid_call_rejected_before_ingest(facts):
    payload=call(**facts)
    with pytest.raises(transport.ConnectWebhookError) as caught:
        transport.ingest(json.dumps(payload).encode(),'invalid',payload)
    assert caught.value.code == 'invalid_call'

@pytest.mark.parametrize('stamp',[None,'bad','2026-11-15T09:00:00'])
def test_timestamp_required(stamp):
    event=call();event['occurred_at']=stamp
    with pytest.raises(ValueError):validate(event)

def test_projection_is_service_item_not_media_or_receipt():
    item=_message({'id':'test','channel':'connect:channel','message_type':'call_missed',
      'direction':'system','body':'','raw':{'isVideo':True,'status':'read','private':'secret'}})
    assert item['call_is_video'] is True
    assert item['author_type']=='system'
    assert 'raw' not in item and 'delivery_state' not in item and 'media_version' not in item


def test_call_service_events_never_enter_ai_context_or_consume_history_limit():
    from tenant_chat_ai_policy import history
    rows=[{'message_type':'text','direction':'incoming','body':'Hello'}]
    rows += [{'message_type':'call_missed','direction':'system','body':'','has_attachment':True} for _ in range(40)]
    lines,missing=history(rows,{})
    assert missing == 0
    assert len(lines) == 1 and lines[0]['text'] == 'Hello'
