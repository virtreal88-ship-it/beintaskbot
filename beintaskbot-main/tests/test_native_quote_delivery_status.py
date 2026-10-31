"""Quoted queue acceptance, exact read receipts and no repeated provider POST."""
import asyncio
import uuid
from unittest.mock import Mock
import pytest
import tenant_connect_transport as transport
import tenant_chat_send_service as service
from tenant_platform import TenantPlatformError
from test_tenant_support_inbox import database,db,A,CA,PROFILE

MID=str(uuid.uuid4());REQUEST=str(uuid.uuid4());SOURCE=str(uuid.uuid4())
ROUTE={'api_key':'synthetic','channel_id':CA,'conversation_id':'client'}
PAYLOAD={'request_id':REQUEST,'lead_id':41,'text':'reply','quote_message_id':SOURCE,'quote_channel_id':CA,'quote_conversation_id':'client'}


def result(**changes):
    return {'id':MID,'channelId':CA,'conversationId':'client','clientMessageId':REQUEST,
      'quotedExternalMessageId':'SOURCE123','status':'pending','providerAccepted':False,'delivered':False,'retrySafe':False,**changes}


@pytest.mark.parametrize('change',[{'channelId':str(uuid.uuid4())},{'conversationId':'other'},
    {'clientMessageId':str(uuid.uuid4())},{'quotedExternalMessageId':'OTHER123'},{'id':str(uuid.uuid4())}])
def test_read_receipt_rejects_scope_mismatch_without_post(monkeypatch,change):
    request=Mock(return_value=result(**change));monkeypatch.setattr(transport,'_request',request)
    with pytest.raises(TenantPlatformError):transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123')
    assert request.call_args.args[1]=='GET';assert request.call_count==1


def test_queue_and_provider_acceptance_are_not_customer_delivery(monkeypatch):
    request=Mock(return_value=result());monkeypatch.setattr(transport,'_request',request)
    assert transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123')['provider_accepted'] is False
    request.return_value=result(status='sent',providerAccepted=True)
    assert transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123')['delivery_state']=='unknown'
    request.return_value=result(status='sent',providerAccepted=True,externalMessageId='OUT123')
    value=transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123');assert value['provider_accepted'] and not value['delivered']
    request.return_value=result(status='delivered',providerAccepted=True,externalMessageId='OUT123',delivered=True)
    assert transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123')['delivered']


def test_accepted_quote_status_check_never_runs_send_again(monkeypatch):
    item={'cached_result':{'state':'accepted','message_id':MID},'key':(A,11,REQUEST),'command':PAYLOAD,'connection':['connect',CA,'client'],'quote':'SOURCE123'}
    monkeypatch.setattr(service,'reserve',Mock(return_value=item));monkeypatch.setattr(service,'quoted_status_credentials',Mock(return_value=ROUTE))
    read=Mock(return_value={'delivery_state':'unknown','provider_accepted':False,'delivered':False,'retry_safe':False})
    monkeypatch.setattr(service,'quoted_delivery_status',read);monkeypatch.setattr(service,'record_quoted_delivery',Mock())
    send=Mock(side_effect=AssertionError('No second POST'));monkeypatch.setattr(service,'send_connect',send)
    for _ in range(2):
        value=asyncio.run(service.run(PROFILE,PAYLOAD,Mock(),Mock(),Mock()));assert value['delivery_state']=='unknown'
    assert read.call_count==2;send.assert_not_called()


def test_quote_status_preserves_history_and_rechecks_employee_authority(db,monkeypatch):
    import tenant_chat_send_store as store
    monkeypatch.setattr(store,'_connect',lambda:db);monkeypatch.setattr(store,'connect_route',lambda *_:ROUTE)
    source=db.execute('SELECT id::text FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))[0]['id']
    db.execute("UPDATE saas_crm_messages SET raw='{"+'"externalMessageId":"SOURCE123"'+"}'::jsonb WHERE tenant_id=%s::uuid",(A,));db.commit()
    data={**PAYLOAD,'quote_message_id':source};item=store.reserve(PROFILE,data)
    store.mark_sending(item,{'origin':'connect'});store.finish(item,'accepted',MID)
    assert store.quoted_status_credentials(item)==ROUTE
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw) VALUES(%s::uuid,%s::uuid,41,%s,%s,'outgoing','reply','{}'::jsonb)",(str(uuid.uuid4()),A,'connect:'+MID,'connect:'+CA));db.commit()
    store.record_quoted_delivery(item,MID,{'delivery_state':'delivered','provider_accepted':True,'delivered':True,'retry_safe':False,'provider_message_id':'OUT123'})
    store.record_quoted_delivery(item,MID,{'delivery_state':'pending','provider_accepted':False,'delivered':False,'retry_safe':False})
    row=db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+MID))[0]['raw'];assert row['status']=='delivered';assert row['externalMessageId']=='OUT123'
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11',(A,));db.commit()
    from tenant_linear_policy import TenantLinearError
    with pytest.raises(TenantLinearError):store.quoted_status_credentials(item)


def test_actual_http_send_then_read_receipts_never_resends(db,monkeypatch):
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_chat_send_store as store
    from tenant_chat_send_api import handler
    monkeypatch.setattr(store,'_connect',lambda:db);monkeypatch.setattr(store,'connect_route',lambda *_:ROUTE)
    monkeypatch.setattr(transport,'_connect',lambda:db)
    monkeypatch.setattr('tenant_ai_agent_connect.mark_operator_reply',Mock())
    monkeypatch.setattr('tenant_knowledge_live_queue.enqueue',Mock())
    source=db.execute('SELECT id::text FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))[0]['id']
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid",('{"externalMessageId":"SOURCE123"}',A));db.commit()
    state={'value':'pending'};calls=[]
    def request(_key,method,path,payload=None):
        calls.append((method,path))
        if method=='POST':return {'id':MID,'status':'pending'}
        status=state['value'];accepted=status in {'sent','delivered','read'}
        return result(status=status,providerAccepted=accepted,delivered=status in {'delivered','read'},**({'externalMessageId':'OUT123'} if accepted else {}))
    monkeypatch.setattr(transport,'_request',request)
    async def scenario():
        app=web.Application();app.router.add_post('/send',handler(lambda _:PROFILE,'https://crm.test',Mock(),Mock(),logging.getLogger(__name__)))
        data={**PAYLOAD,'quote_message_id':source,'expected_tenant_id':A,'expected_user_id':11}
        async with TestClient(TestServer(app)) as client:
            for value in ['pending','unknown','sent','delivered']:
                state['value']=value
                response=await client.post('/send',json=data,headers={'Origin':'https://crm.test'})
                assert response.status==200,await response.text()
                answer=await response.json();assert answer['delivery_state']==value
                assert answer['delivered']==(value=='delivered')
            response=await client.post('/send',json={**data,'text':'changed'},headers={'Origin':'https://crm.test'})
            assert response.status==409
        assert [method for method,_ in calls].count('POST')==1
        assert [method for method,_ in calls].count('GET')==4
        assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE direction='outgoing' AND tenant_id=%s::uuid",(A,))[0]['n']==1
        raw=db.execute('SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+MID))[0]['raw']
        assert raw['status']=='delivered';assert raw['externalMessageId']=='OUT123'
    asyncio.run(scenario())
