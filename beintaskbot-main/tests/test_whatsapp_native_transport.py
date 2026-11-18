import json
import uuid
from unittest.mock import Mock
import pytest
from test_whatsapp_native_inbox import capture
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database, db, A, B, CA, CB
import tenant_whatsapp_native_transport as native
import tenant_connect_transport as transport
import tenant_call_sources as sources
from tenant_platform import TenantPlatformError


@pytest.fixture
def configured(capture,monkeypatch):
    db,_,_=capture
    monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_SEND','1')
    monkeypatch.setattr(native,'_connect',lambda:db)
    monkeypatch.setattr(sources,'service',lambda:('https://gateway.invalid','synthetic'))
    with db.cursor() as cur:native.migrate(cur)
    db.execute("INSERT INTO saas_whatsapp_native_routes(tenant_id,channel_id,generation,enabled) VALUES(%s,%s,'test-generation-1',TRUE)",(A,CA));db.commit()
    route={'tenant_id':A,'channel_id':CA,'conversation_id':'15550000002@s.whatsapp.net','api_key':'legacy-key'}
    with db.cursor() as cur:route=native.bind(cur,route)
    db.commit()
    yield db,route
    db.rollback();db.execute('DELETE FROM saas_whatsapp_native_routes');db.commit()


def accepted(monkeypatch):
    response=Mock(status_code=200)
    response.iter_content.return_value=[json.dumps({'confirmed':True,'result':{'state':'accepted','provider_id':'PROVIDER_12345'}}).encode()]
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    call=Mock(return_value=response);monkeypatch.setattr(native.requests,'post',call)
    return call


def test_app_text_dispatch_uses_exact_native_source(configured,monkeypatch):
    db,route=configured;call=accepted(monkeypatch)
    legacy=Mock(side_effect=AssertionError('legacy called'))
    monkeypatch.setattr(transport,'_request',legacy)
    operation=str(uuid.uuid4())
    result=transport.send(route,'synthetic',operation,return_receipt=True)
    from tenant_whatsapp_native_identity import message_id
    assert result['message_id']==message_id(A,CA,route['conversation_id'],'PROVIDER_12345',True)
    assert result['receipt']['externalMessageId']=='PROVIDER_12345'
    assert call.call_args.kwargs['json']['scope']==route['native']['scope']
    assert call.call_args.kwargs['json']['chat']==route['conversation_id']
    assert call.call_args.kwargs['allow_redirects'] is False
    assert call.call_count==1
    legacy.assert_not_called()


def test_disable_native_does_not_fall_back(configured,monkeypatch):
    db,route=configured;call=accepted(monkeypatch)
    monkeypatch.delenv('BEINTASKBOT_UNIFIED_WHATSAPP_SEND')
    with pytest.raises(TenantPlatformError):native.submit(route,str(uuid.uuid4()),text='synthetic')
    call.assert_not_called()


def test_removed_binding_rejects_stale_route(configured,monkeypatch):
    db,route=configured;call=accepted(monkeypatch)
    db.execute('DELETE FROM saas_whatsapp_native_routes');db.commit()
    with pytest.raises(TenantPlatformError):native.submit(route,str(uuid.uuid4()),text='synthetic')
    call.assert_not_called()


def test_other_tenant_cannot_use_binding(configured,monkeypatch):
    db,route=configured;call=accepted(monkeypatch)
    with pytest.raises(TenantPlatformError):native.submit({**route,'tenant_id':B},str(uuid.uuid4()),text='synthetic')
    call.assert_not_called()


def test_media_uses_existing_receipt_contract(configured,monkeypatch):
    _,route=configured;call=accepted(monkeypatch)
    from tenant_customer_media_service import _submit
    route={**route,'author_id':11,'crm_conversation_id':41,'input_hash':'a'*64,'media_type':'document'}
    result=_submit(route,str(uuid.uuid4()),{'type':'file','mime':'text/plain','name':'test.txt'},'caption',b'synthetic')
    assert result['provider_accepted'] and result['delivery_state']=='sent' and not result['delivered']
    assert call.call_args.kwargs['json']['kind']=='document'


def test_native_credentials_cannot_reach_legacy_connect():
    with pytest.raises(TenantPlatformError,match='native_operation_not_enabled'):
        transport._request(native.NATIVE_KEY,'POST','/v1/channels/test/messages',{})


def test_native_direct_receipt_is_persisted_without_group_forgery(configured,monkeypatch):
    db,route=configured
    db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND connect_channel_id=%s',
        (route['conversation_id'],A,CA));db.commit()
    monkeypatch.setattr(transport,'_connect',lambda:db)
    accepted(monkeypatch)
    result=transport.send(route,'synthetic',str(uuid.uuid4()),return_receipt=True)
    assert result['receipt'].get('_crm_group_send_receipt') is None
    for _ in range(2):
        transport.record_outbound(A,41,route,'synthetic',result['message_id'],actor=11,receipt=result['receipt'])
    rows=db.execute("SELECT raw FROM saas_crm_messages WHERE tenant_id=%s AND external_id=%s",(A,'connect:'+result['message_id']))
    assert len(rows)==1 and rows[0]['raw']['externalMessageId']=='PROVIDER_12345'
    db.commit()


def test_prepared_legacy_dispatch_is_blocked_after_native_activation(configured):
    db,route=configured
    old={k:v for k,v in route.items() if k not in {'native','legacy_media_key'}}
    old['api_key']='legacy-key'
    with db.cursor() as cur:
        with pytest.raises(TenantPlatformError,match='native_source_changed'):
            native.guard_dispatch(cur,A,old)
    db.rollback()
