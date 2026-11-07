"""Synthetic group delivery contract; never performs a provider request."""
import uuid
import pytest
import tenant_connect_transport as transport
from tenant_platform import TenantPlatformError


def test_group_receipt_requires_confirmed_delivery_and_bounds_provider_reference(monkeypatch):
    route={'api_key':'synthetic','channel_id':str(uuid.uuid4()),'conversation_id':'120363411822115409@g.us'}
    calls=[];request_id=str(uuid.uuid4())
    def request(key,method,path,payload):
        calls.append((method,path,payload));return {'status':'sent','id':'connect-id','externalMessageId':'PROVIDER_123'}
    monkeypatch.setattr(transport,'_request',request)
    assert transport.send_group_with_receipt(route,'synthetic',request_id)=={'message_id':'connect-id','external_message_id':'PROVIDER_123'}
    assert len(calls)==1 and calls[0][2]['clientMessageId']==request_id
    monkeypatch.setattr(transport,'_request',lambda *_:{'status':'sent','id':'connect-id','externalMessageId':'unsafe id'})
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt(route,'synthetic',request_id)
    monkeypatch.setattr(transport,'_request',lambda *_:{'status':'pending','id':'connect-id'})
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt(route,'synthetic',request_id)
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt({**route,'conversation_id':'10000@s.whatsapp.net'},'synthetic',request_id)
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt(route,'synthetic','invalid')


def test_operator_group_send_uses_single_attempt_route_and_preserves_quote(monkeypatch):
    route={'api_key':'synthetic','channel_id':str(uuid.uuid4()),'conversation_id':'120363411822115409@g.us'}
    request_id=str(uuid.uuid4());quote='PROVIDER_SOURCE_123';calls=[]
    monkeypatch.setattr(transport,'_request',lambda key,method,path,payload:
        (calls.append((key,method,path,payload)) or {'status':'sent','id':'connect-id','externalMessageId':'PROVIDER_123'}))
    assert transport.send(route,'reply',request_id,quoted_message_id=quote)=='connect-id'
    assert calls==[('synthetic','POST',f"/v1/channels/{route['channel_id']}/group-messages",{
        'conversationId':route['conversation_id'],'type':'text','text':'reply','clientMessageId':request_id,
        'quotedExternalMessageId':quote})]


def test_operator_group_unknown_never_falls_back_to_retrying_messages_endpoint(monkeypatch):
    route={'api_key':'synthetic','channel_id':str(uuid.uuid4()),'conversation_id':'120363411822115409@g.us'}
    calls=[]
    def request(key,method,path,payload):
        calls.append(path)
        raise TenantPlatformError('synthetic timeout')
    monkeypatch.setattr(transport,'_request',request)
    with pytest.raises(TenantPlatformError):transport.send(route,'reply',str(uuid.uuid4()))
    assert calls==[f"/v1/channels/{route['channel_id']}/group-messages"]


def test_operator_direct_send_stays_on_existing_route(monkeypatch):
    route={'api_key':'synthetic','channel_id':str(uuid.uuid4()),'conversation_id':'994501234567@s.whatsapp.net'}
    request_id=str(uuid.uuid4());calls=[]
    monkeypatch.setattr(transport,'_request',lambda key,method,path,payload:
        (calls.append((path,payload)) or {'id':'connect-direct-id'}))
    assert transport.send(route,'hello',request_id)=='connect-direct-id'
    assert calls==[(f"/v1/channels/{route['channel_id']}/messages",{
        'conversationId':route['conversation_id'],'type':'text','text':'hello','clientMessageId':request_id})]
