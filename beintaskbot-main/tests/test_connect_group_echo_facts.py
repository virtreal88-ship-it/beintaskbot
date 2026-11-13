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
