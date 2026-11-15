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


def test_subscription_upgrade_is_additive_and_calls_are_explicit_opt_in():
    assert 'call.missed' not in transport.CONNECT_WEBHOOK_EVENTS
    assert 'call.missed' in transport.CONNECT_ACCEPTED_EVENTS
    previous=['custom.event','call.missed']
    result=transport._preserved_webhook_events({'events':previous})
    assert result[:2] == previous
    assert all(event in result for event in transport.CONNECT_WEBHOOK_EVENTS)
    with pytest.raises(ValueError): transport._preserved_webhook_events({})


def test_additive_subscription_upgrade_readback_preserves_everything(monkeypatch):
    before={'id':'hook','url':'https://crm.example/api/platform/connect/webhook','active':True,'events':['custom.event']}
    calls=[]
    def request(key,method,path,data=None):
        calls.append((method,path,data))
        return {'data':[{**before,'events':['custom.event','concurrent.event','call.missed']}]}
    monkeypatch.setattr(transport,'_request',request)
    assert transport._upgrade_missed_call_subscription('test-key',before)
    assert calls[0] == ('POST','/v1/webhooks/hook/events',{'add':['call.missed'],'expectedUrl':before['url']})
    assert len(calls)==2 and all(c[0]!='PATCH' for c in calls)


@pytest.mark.parametrize('change',[{'active':False},{'url':'https://other.example/api/platform/connect/webhook'},{'events':['call.missed']},{'id':'other'}])
def test_upgrade_never_repairs_concurrent_conflict(monkeypatch,change):
    before={'id':'hook','url':'https://crm.example/api/platform/connect/webhook','active':True,'events':['custom.event']}
    calls=[]
    def request(key,method,path,data=None):
        calls.append(method)
        return {'data':[{**before,'events':['custom.event','call.missed'],**change}]}
    monkeypatch.setattr(transport,'_request',request)
    with pytest.raises(ValueError):transport._upgrade_missed_call_subscription('test-key',before)
    assert calls==['POST','GET']


def test_old_connect_or_disabled_subscription_is_not_replaced(monkeypatch):
    def unavailable(*args,**kwargs):raise RuntimeError('404 old Connect')
    monkeypatch.setattr(transport,'_request',unavailable)
    hook={'id':'hook','url':'https://crm.example/api/platform/connect/webhook','active':True,'events':[]}
    with pytest.raises(RuntimeError):transport._upgrade_missed_call_subscription('test-key',hook)
    with pytest.raises(ValueError):transport._upgrade_missed_call_subscription('test-key',{**hook,'active':False})
