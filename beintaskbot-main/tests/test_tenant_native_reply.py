"""Local PGlite only: reply targets cannot cross conversation scopes."""
import json
import uuid
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB
from tenant_chat_send_store import quote_target
from tenant_chat_send_policy import command
from tenant_linear_policy import TenantLinearError


def test_quote_target_is_exact_tenant_channel_and_conversation(db):
    source=db.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))[0]['id']
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(json.dumps({'externalMessageId':'provider_123'}),A,source));db.commit()
    payload=command({'request_id':str(uuid.uuid4()),'lead_id':41,'text':'reply','quote_message_id':str(source),'quote_channel_id':CA,'quote_conversation_id':'client'})
    with db.cursor() as cur:
        assert quote_target(cur,A,payload,['connect',CA,'client'])=='provider_123'
        with pytest.raises(TenantLinearError):quote_target(cur,A,payload,['connect',CA,'other'])
        with pytest.raises(TenantLinearError):quote_target(cur,B,payload,['connect',CA,'client'])
        with pytest.raises(TenantLinearError):quote_target(cur,A,{**payload,'quote_channel_id':CB},['connect',CA,'client'])


def test_history_exposes_only_valid_quote_identifiers():
    from tenant_operator_dialogs import _message
    row={'id':str(uuid.uuid4()),'external_id':'connect:abc','channel':'connect:'+CA,
         'direction':'incoming','body':'hello','message_type':'text',
         'raw':{'externalMessageId':'MESSAGE_123','quotedExternalMessageId':'OTHER_123','mediaKey':'secret'}}
    value=_message(row)
    assert value['provider_message_id']=='MESSAGE_123'
    assert value['quoted_provider_message_id']=='OTHER_123'
    assert 'raw' not in value and 'secret' not in json.dumps(value)
    row['raw']['quotedExternalMessageId']='bad quote!'
    assert 'quoted_provider_message_id' not in _message(row)


def test_outbound_transport_passes_canonical_quote(monkeypatch):
    import tenant_connect_transport as transport
    calls=[]
    def request(key,method,path,body):
        calls.append((key,method,path,body));return {'id':'connect-message'}
    monkeypatch.setattr(transport,'_request',request)
    route={'api_key':'test','channel_id':CA,'conversation_id':'client'}
    assert transport.send(route,'reply',str(uuid.uuid4()),quoted_message_id='PROVIDER_123')=='connect-message'
    assert calls[0][3]['quotedExternalMessageId']=='PROVIDER_123'
