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
ROUTE={'tenant_id':A,'api_key':'synthetic','channel_id':CA,'conversation_id':'client'}
PAYLOAD={'request_id':REQUEST,'lead_id':41,'text':'reply','quote_message_id':SOURCE,'quote_channel_id':CA,'quote_conversation_id':'client'}


@pytest.fixture(autouse=True)
def scoped_synthetic_channel(monkeypatch):
    monkeypatch.setattr(transport,'QUOTED_ONCE_TENANT',A)
    monkeypatch.setattr(transport,'QUOTED_ONCE_CHANNEL',CA)


def result(**changes):
    return {'id':MID,'channelId':CA,'conversationId':'client','clientMessageId':REQUEST,
      'quotedExternalMessageId':'SOURCE123','protocol':'quoted-once-v1','status':'pending','providerAccepted':False,'delivered':False,'retrySafe':False,**changes}


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
        if method=='POST':return result()
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


def test_stale_prewrite_error_never_permits_retry_after_provider_receipt(monkeypatch):
    monkeypatch.setattr(transport,'_request',Mock(return_value=result(status='failed',retrySafe=True,errorCode='quoted_source_lookup_unavailable',externalMessageId='OUT123')))
    value=transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123')
    assert value['retry_safe'] is False;assert value['provider_message_id']=='OUT123';assert not value['delivered']


def test_accepted_uuid_survives_lost_quote_and_revoked_permission(db,monkeypatch):
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_chat_send_store as store
    from tenant_chat_send_api import handler
    monkeypatch.setattr(store,'_connect',lambda:db);monkeypatch.setattr(store,'connect_route',lambda *_:ROUTE)
    monkeypatch.setattr(transport,'_connect',lambda:db)
    monkeypatch.setattr('tenant_ai_agent_connect.mark_operator_reply',Mock());monkeypatch.setattr('tenant_knowledge_live_queue.enqueue',Mock())
    source=db.execute('SELECT id::text FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))[0]['id']
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid",('{"externalMessageId":"SOURCE123"}',A));db.commit()
    calls=[]
    def request(_key,method,path,payload=None):
        calls.append(method);return result()
    monkeypatch.setattr(transport,'_request',request)
    async def scenario():
        app=web.Application();app.router.add_post('/send',handler(lambda _:PROFILE,'https://crm.test',Mock(),Mock(),logging.getLogger(__name__)))
        data={**PAYLOAD,'quote_message_id':source,'expected_tenant_id':A,'expected_user_id':11};headers={'Origin':'https://crm.test'}
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/send',json=data,headers=headers);assert response.status==200
            db.execute('DELETE FROM saas_crm_messages WHERE id=%s::uuid',(source,));db.commit()
            response=await client.post('/send',json=data,headers=headers);assert response.status==409
            assert (await response.json())['keep_request'] is True
            db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw) VALUES(%s::uuid,%s::uuid,41,'m1',%s,'incoming','source',%s::jsonb)",(source,A,'connect:'+CA,'{"externalMessageId":"SOURCE123"}'));db.commit()
            db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11',(A,));db.commit()
            response=await client.post('/send',json=data,headers=headers);assert response.status==403
            assert (await response.json())['keep_request'] is True
            db.execute('UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s::uuid AND telegram_id=11',(A,));db.commit()
            response=await client.post('/send',json=data,headers=headers);assert response.status==200
            assert (await response.json())['delivery_state']=='pending'
        assert calls.count('POST')==1;assert calls.count('GET')==2
    asyncio.run(scenario())


def test_scoped_post_has_no_legacy_fallback_on_404_timeout_or_bad_receipt(monkeypatch):
    for answer in [TenantPlatformError('404'),TenantPlatformError('timeout'),{'id':MID,'status':'sent'}]:
        request=Mock(side_effect=answer) if isinstance(answer,Exception) else Mock(return_value=answer)
        monkeypatch.setattr(transport,'_request',request)
        with pytest.raises(TenantPlatformError):transport.send(ROUTE,'reply',REQUEST,quoted_message_id='SOURCE123')
        assert request.call_count==1
        assert request.call_args.args[1]=='POST' and request.call_args.args[2].endswith('/quoted-once')


def test_unscoped_and_ordinary_routes_keep_legacy_contract(monkeypatch):
    request=Mock(return_value={'id':MID});monkeypatch.setattr(transport,'_request',request)
    for route,quote in [(ROUTE,None),({**ROUTE,'tenant_id':str(uuid.uuid4())},'SOURCE123'),({**ROUTE,'channel_id':str(uuid.uuid4())},'SOURCE123')]:
        transport.send(route,'reply',REQUEST,quoted_message_id=quote)
        assert request.call_args.args[2].endswith('/messages')


def test_timeout_recovery_reads_exact_uuid_without_post(db,monkeypatch):
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_chat_send_store as store
    from tenant_chat_send_api import handler
    monkeypatch.setattr(store,'_connect',lambda:db);monkeypatch.setattr(store,'connect_route',lambda *_:ROUTE)
    monkeypatch.setattr(transport,'_connect',lambda:db)
    monkeypatch.setattr('tenant_ai_agent_connect.mark_operator_reply',Mock());monkeypatch.setattr('tenant_knowledge_live_queue.enqueue',Mock())
    source=db.execute('SELECT id::text FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))[0]['id']
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid",('{"externalMessageId":"SOURCE123"}',A));db.commit()
    calls=[]
    def request(_key,method,path,payload=None):
        calls.append((method,path))
        if method=='POST':raise TenantPlatformError('Response lost after Connect reserved the intent')
        return result(status='sent',providerAccepted=True,externalMessageId='OUT123')
    monkeypatch.setattr(transport,'_request',request)
    async def scenario():
        app=web.Application();app.router.add_post('/send',handler(lambda _:PROFILE,'https://crm.test',Mock(),Mock(),logging.getLogger(__name__)))
        data={**PAYLOAD,'quote_message_id':source,'expected_tenant_id':A,'expected_user_id':11};headers={'Origin':'https://crm.test'}
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/send',json=data,headers=headers);assert response.status==503
            assert (await response.json())['keep_request'] is True
            response=await client.post('/send',json=data,headers=headers);assert response.status==200
            answer=await response.json();assert answer['provider_accepted'] is True and answer['message_id']==MID
            assert db.execute('SELECT state FROM saas_chat_send_receipts WHERE request_id=%s::uuid',(REQUEST,))[0]['state']=='accepted'
        assert [method for method,_ in calls]==['POST','GET']
        assert calls[0][1].endswith('/quoted-once') and '/quoted-once/requests/'+REQUEST in calls[1][1]
    asyncio.run(scenario())


def test_legacy_receipt_can_never_authorize_retry(monkeypatch):
    monkeypatch.setattr(transport,'_request',Mock(return_value=result(protocol='legacy-read-only',status='failed',retrySafe=True,errorCode='quoted_message_unavailable')))
    assert transport.quoted_delivery_status(ROUTE,MID,REQUEST,'SOURCE123')['retry_safe'] is False


def test_real_http_old_server_404_has_no_old_queue_fallback(monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    calls=[]
    class OldServer(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path);self.send_response(404);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),OldServer)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    monkeypatch.setattr(transport,'CONNECT_URL','http://127.0.0.1:'+str(server.server_port))
    try:
        with pytest.raises(TenantPlatformError):transport.send(ROUTE,'reply',REQUEST,quoted_message_id='SOURCE123')
        assert calls==['/v1/channels/'+CA+'/quoted-once']
    finally:server.shutdown();server.server_close();worker.join(timeout=2)
