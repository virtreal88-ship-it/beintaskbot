"""Synthetic protocol -> CRM contract, no provider/AI calls or customer data."""
import copy
import json
import uuid
import pytest
from test_whatsapp_native_inbox import capture, store, delivery
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database, db, A, B, CA, CB
from tenant_linear_policy import TenantLinearError
import tenant_whatsapp_native_projection as projection


def message(**changes):
    return {'kind':'message','chat':'15550000002@s.whatsapp.net','sender':'15550000002@s.whatsapp.net',
        'sender_alt':'188888888@lid','provider_id':'SYNTHETIC_MESSAGE_1','from_me':False,
        'timestamp':1794999600000,'text':'Synthetic test','message_type':'text',
        'push_name':'Synthetic name','proto':'PRIVATE_MEDIA_KEY', **changes}


def event(payload=None):
    value=delivery()
    return projection.message_event(value['scope'],value['event']['id'],payload or message(),str(uuid.UUID(int=400)))


def test_sealed_evidence_cannot_move_between_companies_sources_or_events(capture,monkeypatch):
    database,cipher,cfg=capture
    monkeypatch.setattr(projection,'_fernet',lambda:cipher)
    value=delivery();value['event']['payload']=message()
    store(value)
    row=database.execute('SELECT * FROM saas_whatsapp_native_inbox')[0];database.commit()
    assert projection.open_stored(row,value['scope'])==value['event']['payload']
    for field,changed in [('tenant_id',B),('channel_id',CB),('generation','other'),('event_id','c'*64),('payload_sha256','d'*64)]:
        bad={**row,field:changed}
        with pytest.raises(TenantLinearError,match='native_inbox_evidence_invalid'):
            projection.open_stored(bad,value['scope'])
    for field,changed in [('tenant',B),('source',CB),('account','199999999@s.whatsapp.net'),('generation','other')]:
        with pytest.raises(TenantLinearError):projection.open_stored(row,{**value['scope'],field:changed})


def test_private_protocol_never_enters_crm_and_names_do_not_authorize_identity():
    out=event();data=out['data']
    assert out['type']=='message.inbound.received' and data['fromMe'] is False
    assert data['directIdentity']=={'jid':'15550000002@s.whatsapp.net','lid':'188888888@lid'}
    assert data['participantJid']=='15550000002@s.whatsapp.net'
    assert 'PRIVATE_MEDIA_KEY' not in json.dumps(out) and 'proto' not in data
    spoof=message(sender='199999999@s.whatsapp.net',push_name='15550000002')
    assert 'directIdentity' not in event(spoof)['data']
    outbound=event(message(from_me=True,sender='123456789@s.whatsapp.net',sender_alt='177777777@lid',recipient_alt='188888888@lid'))['data']
    assert outbound['directIdentity']['lid']=='188888888@lid'
    assert outbound['source']=='unknown' and outbound['authorshipUncertain'] is True
    assert 'pushName' not in outbound and 'ai_authored' not in outbound


def test_group_author_and_caption_quote_are_explicit_without_merging_by_name():
    p=message(chat='120363123456789@g.us',message_type='image',media_kind='image',media_mime='image/png',media_size=23,
        file_name='image',quote_id='SYNTHETIC_QUOTE',quote_chat='120363123456789@g.us')
    out=event(p)['data']
    assert out['isGroup'] and out['participantJid']==p['sender'] and out['text']==p['text']
    assert out['conversationId']==p['chat'] and 'directIdentity' not in out
    assert out['quotedExternalMessageId']=='SYNTHETIC_QUOTE' and out['fileSize']==23
    other=event({**p,'chat':'120363999999999@g.us','quote_chat':'120363999999999@g.us'})['data']
    assert other['conversationId']!=out['conversationId']
    with pytest.raises(TenantLinearError,match='native_quote_scope_unconfirmed'):
        event({**p,'quote_chat':'120363999999999@g.us'})


@pytest.mark.parametrize('changes',[
    {'kind':'receipt'}, {'variant':'control'}, {'message_type':'unsupported'},
    {'_crm_signed_outbound_receipt':True}, {'ai_authored':True}, {'url':'https://private.invalid'},
    {'from_me':1}, {'timestamp':True}, {'timestamp':-1000}, {'timestamp':10**25},
    {'chat':'same display name'}, {'sender':'15550000002:3@s.whatsapp.net'}, {'provider_id':'../invalid'},
    {'media_kind':'document','message_type':'image'}, {'message_type':'audio','media_kind':'audio','media_size':True},
])
def test_malformed_or_unsupported_event_is_not_a_new_ai_turn(changes):
    with pytest.raises(TenantLinearError):event(message(**changes))
