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


@pytest.mark.parametrize('reason,code', [
    ('native_quote_scope_denied', 'quoted_source_scope_conflict'),
    ('native_quote_evidence_missing', 'quoted_message_unavailable'),
])
def test_quote_preflight_is_refused_before_dispatch(configured, monkeypatch, reason, code):
    _, route = configured
    call = accepted(monkeypatch)
    monkeypatch.setattr(native, 'resolve_quote', Mock(side_effect=TenantPlatformError(reason)))
    with pytest.raises(transport.ConnectPreflightError) as failure:
        native.submit(route, str(uuid.uuid4()), text='synthetic', quoted_message_id='ORIGINAL')
    assert failure.value.code == code
    call.assert_not_called()


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


@pytest.fixture
def retained_photo(configured,monkeypatch,request):
    from test_whatsapp_native_projection import message
    from test_whatsapp_native_inbox import delivery,store
    import tenant_whatsapp_native_projection as projection
    import tenant_whatsapp_native_inbox as inbox
    db,route=configured
    direction=getattr(getattr(request.node,'callspec',None),'params',{}).get('direction','incoming')
    monkeypatch.setattr(projection,'_fernet',inbox._fernet)
    value=delivery();value['event']['payload']=message(sender_alt='',message_type='image',
        from_me=direction=='outgoing',media_kind='image',media_mime='image/png',media_size=9)
    store(value)
    db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND connect_channel_id=%s',(route['conversation_id'],A,CA))
    db.execute("UPDATE saas_crm_messages SET external_id='connect:photo-test',message_type='image',direction=%s,raw=%s::jsonb WHERE tenant_id=%s AND external_id='m1'",
        (direction,json.dumps({'externalMessageId':'SYNTHETIC_MESSAGE_1','nativeEventId':'a'*64,'conversationId':route['conversation_id']}),A))
    db.execute('UPDATE saas_whatsapp_native_routes SET enabled=FALSE,legacy_fallback=TRUE');db.commit()
    with db.cursor() as cur:
        route=native.bind(cur,{k:v for k,v in route.items() if k not in {'native','api_key'}}|{'api_key':'legacy-key'})
    db.commit()
    response=Mock(status_code=200,headers={'Content-Encoding':'identity'})
    response.iter_content.return_value=[b'synthetic']
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    call=Mock(return_value=response);monkeypatch.setattr(native.requests,'post',call)
    return db,route,call


@pytest.mark.parametrize('direction',['incoming','outgoing'])
def test_retained_photo_read_after_rollback_never_enables_native_send(retained_photo,direction):
    db,route,call=retained_photo
    db.execute("UPDATE saas_crm_messages SET direction=%s WHERE tenant_id=%s AND external_id='connect:photo-test'",(direction,A));db.commit()
    assert not route.get('native') and route['api_key']=='legacy-key'
    assert native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})==(b'synthetic','image/png')
    assert call.call_count==1 and call.call_args.args[0].endswith('/messages/media')
    assert call.call_args.kwargs['json']['scope']==route['native_media']['scope']
    call.reset_mock()
    with pytest.raises(TenantPlatformError,match='native_source_changed'):
        native.submit(route,str(uuid.uuid4()),text='not sent')
    call.assert_not_called()


@pytest.mark.parametrize('change',['tenant','source','dialog','generation','source_revoked','capture_disabled','route_removed'])
def test_retained_photo_read_rechecks_current_scope(retained_photo,change):
    db,route,call=retained_photo
    if change=='tenant':route={**route,'tenant_id':B}
    if change=='source':route={**route,'channel_id':CB}
    if change=='dialog':route={**route,'conversation_id':'15550000003@s.whatsapp.net'}
    if change=='generation':route={**route,'native_media':{**route['native_media'],'scope':{**route['native_media']['scope'],'generation':'other'}}}
    if change=='source_revoked':db.execute('UPDATE saas_browser_call_sources SET enabled=FALSE');db.commit()
    if change=='capture_disabled':db.execute('UPDATE saas_whatsapp_capture_generations SET enabled=FALSE');db.commit()
    if change=='route_removed':db.execute('DELETE FROM saas_whatsapp_native_routes');db.commit()
    with pytest.raises(TenantPlatformError):
        native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})
    call.assert_not_called()


def test_legacy_photo_fallback_drops_read_binding_without_loop(retained_photo,monkeypatch):
    import asyncio
    import tenant_connect_media as media
    from unittest.mock import AsyncMock
    _,route,_=retained_photo
    monkeypatch.setattr(native,'download',Mock(side_effect=TenantPlatformError('native_media_evidence_missing')))
    legacy=AsyncMock(return_value=(b'old photo','image/png'))
    original=media._download_media;monkeypatch.setattr(media,'_download_media',legacy)
    assert asyncio.run(original({'route':route,'message_id':'photo-test','row':{'message_type':'image'}}))==(b'old photo','image/png')
    assert legacy.call_count==1
    sent=legacy.call_args.args[0]['route']
    assert not sent.get('native') and not sent.get('native_media') and sent['api_key']=='legacy-key'


def test_legacy_incoming_photo_resolves_exact_retained_evidence_without_writes(retained_photo):
    db,route,call=retained_photo
    db.execute("UPDATE saas_crm_messages SET direction='incoming',raw=raw-'nativeEventId' WHERE tenant_id=%s AND external_id='connect:photo-test'",(A,));db.commit()
    before=db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s AND external_id='connect:photo-test'",(A,));db.commit()
    assert native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})==(b'synthetic','image/png')
    assert call.call_args.kwargs['json']['event_id']=='a'*64
    after=db.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s AND external_id='connect:photo-test'",(A,));db.commit()
    assert before==after


@pytest.mark.parametrize('change',['provider','direction','chat','kind'])
def test_legacy_photo_recovery_does_not_match_similar_other_message(retained_photo,change):
    db,route,call=retained_photo
    db.execute("UPDATE saas_crm_messages SET direction='incoming',raw=raw-'nativeEventId' WHERE tenant_id=%s AND external_id='connect:photo-test'",(A,))
    if change=='provider':db.execute("UPDATE saas_crm_messages SET raw=jsonb_set(raw,'{externalMessageId}','\"DIFFERENT_PROVIDER\"') WHERE tenant_id=%s",(A,))
    if change=='direction':db.execute("UPDATE saas_crm_messages SET direction='outgoing' WHERE tenant_id=%s",(A,))
    if change=='chat':db.execute("UPDATE saas_crm_messages SET raw=jsonb_set(raw,'{_crm_provider_conversation_id}','\"15550000003@s.whatsapp.net\"') WHERE tenant_id=%s",(A,))
    if change=='kind':db.execute("UPDATE saas_crm_messages SET message_type='audio' WHERE tenant_id=%s",(A,))
    db.commit()
    with pytest.raises(TenantPlatformError,match='native_media_evidence_missing'):
        native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})
    call.assert_not_called()


def test_legacy_photo_lookup_is_bounded_and_rejects_ambiguity(retained_photo,monkeypatch):
    from test_whatsapp_native_projection import message
    from test_whatsapp_native_inbox import delivery,store
    db,route,call=retained_photo
    db.execute("UPDATE saas_crm_messages SET raw=raw-'nativeEventId' WHERE tenant_id=%s AND external_id='connect:photo-test'",(A,));db.commit()
    value=delivery(after=2,event={'id':'b'*64,'sequence':3,'payload':message(sender_alt='',message_type='image',media_kind='image',media_mime='image/png',media_size=9)})
    store(value)
    with pytest.raises(TenantPlatformError,match='native_media_evidence_ambiguous'):
        native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})
    monkeypatch.setattr(native,'MEDIA_EVIDENCE_BYTES',1)
    with pytest.raises(TenantPlatformError,match='native_media_evidence_missing'):
        native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})
    monkeypatch.setattr(native,'MEDIA_EVIDENCE_BYTES',16*1024*1024)
    monkeypatch.setattr(native,'MEDIA_EVIDENCE_EVENTS',0)
    with pytest.raises(TenantPlatformError,match='native_media_evidence_missing'):
        native.download(route,'photo-test',max_bytes=100,allowed_types={'image/png'})
    call.assert_not_called()


def test_native_credentials_cannot_reach_legacy_connect():
    with pytest.raises(TenantPlatformError,match='native_operation_not_enabled'):
        transport._request(native.NATIVE_KEY,'POST','/v1/channels/test/messages',{})


def test_case_attachment_native_read_never_uses_legacy_on_scope_failure(configured,monkeypatch):
    _,route=configured
    read=Mock(return_value=(b'synthetic','text/plain'))
    monkeypatch.setattr(native,'download',read)
    legacy=Mock(side_effect=AssertionError('legacy called'))
    monkeypatch.setattr(transport.requests,'get',legacy)
    result=transport.download_connect_message_media(route,'MESSAGE_123','document',route['conversation_id'])
    assert result['mimeType']=='text/plain'
    read.side_effect=TenantPlatformError('native_source_changed')
    with pytest.raises(TenantPlatformError,match='native_source_changed'):
        transport.download_connect_message_media(route,'MESSAGE_123','document',route['conversation_id'])
    legacy.assert_not_called()


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


def test_quote_requires_exact_existing_dialog_evidence(configured,monkeypatch):
    from test_whatsapp_native_projection import message
    from test_whatsapp_native_inbox import delivery,store
    import tenant_whatsapp_native_projection as projection
    import tenant_whatsapp_native_inbox as inbox
    db,route=configured;call=accepted(monkeypatch)
    monkeypatch.setattr(projection,'_fernet',inbox._fernet)
    value=delivery();value['event']['payload']=message(sender_alt='');store(value)
    db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND connect_channel_id=%s',(route['conversation_id'],A,CA))
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s AND external_id='m1'",(json.dumps({'externalMessageId':'SYNTHETIC_MESSAGE_1','nativeEventId':'a'*64}),A));db.commit()
    native.text(route,'reply',str(uuid.uuid4()),quoted_message_id='SYNTHETIC_MESSAGE_1')
    assert call.call_args.kwargs['json']['quote_event']=='a'*64
    assert call.call_args.kwargs['json']['quote_id']=='SYNTHETIC_MESSAGE_1'
    call.reset_mock()
    for changed in ({**route,'conversation_id':'15550000003@s.whatsapp.net'},{**route,'tenant_id':B}):
        with pytest.raises(TenantPlatformError):native.text(changed,'reply',str(uuid.uuid4()),quoted_message_id='SYNTHETIC_MESSAGE_1')
        db.rollback()
    call.assert_not_called()


def test_old_quote_without_event_alias_uses_verified_current_source_pair(configured, monkeypatch):
    from test_whatsapp_native_projection import message
    from test_whatsapp_native_inbox import delivery, store
    import tenant_whatsapp_native_projection as projection
    import tenant_whatsapp_native_inbox as inbox
    db, route = configured
    call = accepted(monkeypatch)
    monkeypatch.setattr(projection, '_fernet', inbox._fernet)
    peer, lid = route['conversation_id'], '123456789012@lid'
    value = delivery()
    value['event']['payload'] = message(chat=lid, sender=lid, sender_alt='')
    store(value)
    db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND connect_channel_id=%s',
               (peer, A, CA))
    facts = {'externalMessageId': 'SYNTHETIC_MESSAGE_1', 'nativeEventId': 'a'*64,
             '_crm_provider_conversation_id': lid, 'conversationId': peer}
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s AND external_id='m1'",
               (json.dumps(facts), A))
    db.commit()
    with pytest.raises(transport.ConnectPreflightError):
        native.text(route, 'reply', str(uuid.uuid4()), quoted_message_id='SYNTHETIC_MESSAGE_1')
    call.assert_not_called()
    for identity in (lid, peer):
        db.execute('''INSERT INTO saas_connect_direct_identities
            (tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s,%s,%s,%s)''',
            (A, CA, identity, peer))
    db.commit()
    native.text(route, 'reply', str(uuid.uuid4()), quoted_message_id='SYNTHETIC_MESSAGE_1')
    call.assert_called_once()
    assert call.call_args.kwargs['json']['chat'] == peer
    assert call.call_args.kwargs['json']['quote_event'] == 'a'*64
    assert 'legacy_quote' not in call.call_args.kwargs['json']

    call.reset_mock()
    db.execute("UPDATE saas_crm_messages SET raw=raw||%s::jsonb WHERE tenant_id=%s AND external_id='m1'",
               (json.dumps({'conversationIdentityConflict': True}), A))
    db.commit()
    with pytest.raises(transport.ConnectPreflightError):
        native.text(route, 'reply', str(uuid.uuid4()), quoted_message_id='SYNTHETIC_MESSAGE_1')
    call.assert_not_called()


@pytest.mark.parametrize('http,extra,preflight', [
    (422, {}, True), (409, {}, False), (422, {'result': {'provider_id': 'old'}}, False),
])
def test_gateway_quote_refusal_is_known_only_with_exact_predispatch_signal(configured, monkeypatch, http, extra, preflight):
    _, route = configured
    call = accepted(monkeypatch)
    monkeypatch.setattr(native, 'resolve_quote', lambda *_: {'quote_id': 'ORIGINAL', 'quote_event': 'a'*64})
    response = call.return_value
    response.status_code = http
    response.iter_content.return_value = [json.dumps({'error': 'native_quote_not_sent', 'confirmed': False, **extra}).encode()]
    with pytest.raises(TenantPlatformError) as failure:
        native.text(route, 'reply', str(uuid.uuid4()), quoted_message_id='ORIGINAL')
    assert isinstance(failure.value, transport.ConnectPreflightError) is preflight
    call.assert_called_once()


def test_lookup_is_read_only_exact_actor_operation(configured,monkeypatch):
    db,route=configured;call=accepted(monkeypatch)
    route={**route,'author_id':11,'crm_conversation_id':41}
    request=str(uuid.uuid4())
    result=native.lookup(route,request)
    assert result['providerAccepted'] and not result['delivered']
    assert call.call_args.args[0].endswith('/messages/status')
    assert call.call_args.kwargs['json']['operation']=='employee:11:'+request
    assert 'text' not in call.call_args.kwargs['json']
    assert call.call_count==1


@pytest.mark.parametrize('original,canonical,from_me',[
    ('123456789012@lid','15550000002@s.whatsapp.net',False),
    ('15550000002@s.whatsapp.net','123456789012@lid',False),
    ('123456789012@lid','15550000002@s.whatsapp.net',True)])
def test_quote_alias_requires_same_provider_party(original,canonical,from_me):
    evidence={'chat':original,'sender':original if not from_me else '15550000001@s.whatsapp.net',
      'from_me':from_me,('recipient_alt' if from_me else 'sender_alt'):canonical}
    facts={'_crm_provider_conversation_id':original}
    assert native.quote_chat_matches(evidence,canonical,facts)
    assert not native.quote_chat_matches(evidence,'15550000003@s.whatsapp.net',facts)
    assert not native.quote_chat_matches(evidence,canonical,{'_crm_provider_conversation_id':'another'})


def test_legacy_quote_comes_from_scoped_history_not_caller(configured,monkeypatch):
    db,route=configured;call=accepted(monkeypatch)
    db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND connect_channel_id=%s',(route['conversation_id'],A,CA))
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb,body='historical original',message_type='text',direction='incoming' WHERE tenant_id=%s AND external_id='m1'",(json.dumps({'externalMessageId':'LEGACY_ORIGINAL_1'}),A));db.commit()
    native.text(route,'new reply',str(uuid.uuid4()),quoted_message_id='LEGACY_ORIGINAL_1')
    q=call.call_args.kwargs['json']['legacy_quote']
    assert q=={'chat':route['conversation_id'],'sender':route['conversation_id'],'provider_id':'LEGACY_ORIGINAL_1','text':'historical original'}
    call.reset_mock()
    with pytest.raises(TenantPlatformError):
        native.text({**route,'conversation_id':'15550000003@s.whatsapp.net'},'new reply',str(uuid.uuid4()),quoted_message_id='LEGACY_ORIGINAL_1')
    call.assert_not_called()


def test_native_group_and_consultation_dispatch_do_not_reach_connect(configured,monkeypatch):
    _,route=configured;call=accepted(monkeypatch)
    monkeypatch.setattr(transport,'_request',Mock(side_effect=AssertionError('legacy called')))
    group={**route,'conversation_id':'120363123456789@g.us'}
    result=transport.send_group_with_receipt(group,'synthetic',str(uuid.uuid4()))
    assert result['receipt']['_crm_native_send_receipt'] is True
    result=transport.send_consultation_with_receipt(route,'synthetic',str(uuid.uuid4()))
    assert result['state']=='sent' and result['external_message_id']=='PROVIDER_12345'
    assert call.call_count==2
