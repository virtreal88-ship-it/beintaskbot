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
    assert transport.send_group_with_receipt(route,'synthetic',request_id)['external_message_id'] is None
    monkeypatch.setattr(transport,'_request',lambda *_:{'status':'pending','id':'connect-id'})
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt(route,'synthetic',request_id)
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt({**route,'conversation_id':'10000@s.whatsapp.net'},'synthetic',request_id)
    with pytest.raises(TenantPlatformError):transport.send_group_with_receipt(route,'synthetic','invalid')
