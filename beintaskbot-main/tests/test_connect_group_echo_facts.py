import pytest
from tenant_connect_transport import _group_echo_facts, ConnectWebhookError

C='channel'; G='120363000000001@g.us'; P='994500000001@s.whatsapp.net'
def data(**kw):
    return dict(channelId=C,conversationId=G,externalMessageId='PROVIDER123',participantJid=P,type='text',status='sent',_crm_signed_outbound_receipt=True,fromMe=True,**kw)

def test_monotonic_status_and_local_authorship():
    for status in ('pending','sent','delivered','read'):
        facts=_group_echo_facts(data(),{'raw':{'status':status,'verified_employee_id':11,'author_name':'Owner'},'message_type':'text'},C,G)
        assert facts.get('status') == ('sent' if status=='pending' else None)
        assert 'author_name' not in facts and 'verified_employee_id' not in facts
        assert facts['participantJid']==P

@pytest.mark.parametrize('key,value',[('channelId','wrong'),('conversationId','other@g.us'),('externalMessageId','OTHER123'),('participantJid','994500000002@s.whatsapp.net'),('participantIdentity',{'lid':'999999@lid','jid':P})])
def test_conflicting_existing_identity_fails_closed(key,value):
    event=data();event['participantIdentity']={'lid':'888888@lid','jid':P}
    with pytest.raises(ConnectWebhookError):
        _group_echo_facts(event,{'raw':{key:value}},C,G)

@pytest.mark.parametrize('key,value',[('fromMe',False),('_crm_signed_outbound_receipt',False),('externalMessageId','bad')])
def test_unsigned_or_invalid_evidence_not_used(key,value):
    event=data();event[key]=value
    assert _group_echo_facts(event,{'raw':{}},C,G)=={}

def test_participant_not_inferred():
    event=data();event.pop('participantJid')
    assert 'participantJid' not in _group_echo_facts(event,{'raw':{}},C,G)

def test_message_type_conflict():
    with pytest.raises(ConnectWebhookError):
        _group_echo_facts(data(),{'raw':{},'message_type':'image'},C,G)

@pytest.mark.parametrize('field,bad',[('channelId','wrong'),('conversationId','other@g.us'),('clientMessageId','other'),('direction','incoming'),('type','image')])
def test_sync_receipt_rejects_wrong_scope(monkeypatch,field,bad):
    import uuid
    import tenant_connect_transport as transport
    route={'channel_id':C,'conversation_id':G,'api_key':'test'};request=str(uuid.uuid4())
    value={'id':'connect-id','status':'sent','externalMessageId':'PROVIDER123','channelId':C,'conversationId':G,
           'clientMessageId':request,'direction':'outgoing','type':'text','groupReceipt':{'protocol':'group-send-v1','fromMe':True,'participantJid':P}}
    value[field]=bad
    monkeypatch.setattr(transport,'_request',lambda *_:value)
    with pytest.raises(transport.TenantPlatformError):transport.send(route,'synthetic',request,return_receipt=True)


def test_sync_receipt_keeps_evidence_without_echo(monkeypatch):
    import uuid
    import tenant_connect_transport as transport
    route={'channel_id':C,'conversation_id':G,'api_key':'test'};request=str(uuid.uuid4())
    value={'id':'connect-id','status':'sent','externalMessageId':'PROVIDER123','channelId':C,'conversationId':G,
           'clientMessageId':request,'direction':'outgoing','type':'text','groupReceipt':{'protocol':'group-send-v1','fromMe':True,'participantJid':P}}
    monkeypatch.setattr(transport,'_request',lambda *_:value)
    result=transport.send(route,'synthetic',request,return_receipt=True)
    assert result['receipt']=={'channelId':C,'conversationId':G,'externalMessageId':'PROVIDER123','fromMe':True,
        'status':'sent','_crm_group_send_receipt':True,'participantJid':P}
    value['groupReceipt'].pop('participantJid')
    assert 'participantJid' not in transport.send(route,'synthetic',request,return_receipt=True)['receipt']

@pytest.mark.asyncio
async def test_manual_send_persists_receipt_then_caches_only_message_id(monkeypatch):
    from unittest.mock import Mock
    import tenant_chat_send_service as service
    item={'key':('tenant',11,'request'),'command':{'lead_id':1,'text':'synthetic','request_id':'request'},'connection':['connect',C,G]}
    receipt={'channelId':C,'conversationId':G,'externalMessageId':'PROVIDER123','fromMe':True,'status':'sent','_crm_group_send_receipt':True,'participantJid':P}
    monkeypatch.setattr(service,'command',lambda v:v)
    monkeypatch.setattr(service,'reserve',lambda *_:item)
    monkeypatch.setattr(service,'mark_sending',lambda *_:({'telegram_id':11,'display_name':'Owner'}, {'channel_id':C,'conversation_id':G}))
    dispatch=Mock(return_value={'message_id':'connect-id','external_message_id':'PROVIDER123','receipt':receipt})
    monkeypatch.setattr(service,'dispatch_guarded',dispatch)
    record=Mock();finish=Mock()
    monkeypatch.setattr(service,'record_connect_outbound',record);monkeypatch.setattr(service,'finish',finish)
    result=await service.run({},item['command'],None,None,Mock())
    assert result=={'state':'accepted','message_id':'connect-id','origin':'connect'}
    assert dispatch.call_count==1 and dispatch.call_args.kwargs['return_receipt'] is True
    assert record.call_args.kwargs['receipt']==receipt
    finish.assert_called_once_with(item,'accepted','connect-id')


def test_later_echo_alias_retains_sync_identity_and_advances_delivery():
    lid='888888@lid';pair={'lid':lid,'jid':P}
    event=data();event.update(participantJid=lid,participantIdentity=pair,status='delivered')
    facts=_group_echo_facts(event,{'raw':{'externalMessageId':'PROVIDER123','participantJid':P,'status':'sent'},'message_type':'text'},C,G)
    assert facts['participantJid']==P and facts['participantIdentity']==pair and facts['status']=='delivered'
