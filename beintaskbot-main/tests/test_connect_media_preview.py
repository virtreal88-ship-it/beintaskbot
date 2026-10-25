"""Offline PostgreSQL authority + mocked bounded media transport. No customer traffic."""
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
import pytest
from cryptography.fernet import Fernet
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
import tenant_chat_attachment_store as store
import tenant_chat_attachment_api as api
import tenant_connect_media as media
import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError

MID = str(uuid.UUID(int=901))


@pytest.fixture(autouse=True)
def clear_attachment_api_memory():
    api.MEDIA_CACHE.clear()
    api.MEDIA_CACHE_BYTES = 0
    api.MEDIA_INFLIGHT.clear()
    api.REQUESTS.clear()

@pytest.fixture
def media_db(db, monkeypatch):
    monkeypatch.setattr(store, '_connect', lambda: db)
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr('tenant_connect_transport._fernet', lambda: cipher)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",
               (A, cipher.encrypt(b'{"api_key":"test-key"}')))
    db.execute("UPDATE saas_crm_messages SET external_id=%s,message_type='audio',raw=%s::jsonb WHERE tenant_id=%s::uuid",
               ('connect:'+MID, json.dumps({'conversationId':'client'}), A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s::uuid,33,'worker',%s::jsonb)", (A,json.dumps(["dialogs"])))
    db.commit()
    return db


def test_operator_connect_media_needs_no_kommo_crm_or_ai(media_db):
    person = {'tenant_id':A,'telegram_id':33}
    for _ in range(2):
        result=store.load(person,41,'connect:'+MID)
        assert result['route']['channel_id']==CA and result['provider']=='connect'
    assert media_db.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs')[0]['n']==0
    assert media_db.execute('SELECT count(*) AS n FROM saas_crm_deals')[0]['n']==0
    assert media_db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0


def test_case_media_request_is_bound_to_exact_channel_and_conversation(media_db):
    person={'tenant_id':A,'telegram_id':33}
    with pytest.raises(TenantLinearError) as wrong_channel:
        store.load(person,41,'connect:'+MID,channel_id=CB,conversation_id='client')
    assert wrong_channel.value.status==404
    with pytest.raises(TenantLinearError) as wrong_conversation:
        store.load(person,41,'connect:'+MID,channel_id=CA,conversation_id='other')
    assert wrong_conversation.value.status==404
    with pytest.raises(TenantLinearError) as incomplete_scope:
        store.load(person,41,'connect:'+MID,channel_id=CA)
    assert incomplete_scope.value.status==404


@pytest.mark.parametrize('tenant_status',['active','onboarding','ready_for_integration'])
@pytest.mark.parametrize('case',['tenant','channel','conversation','lead','message','permission','inactive','disconnected','disabled'])
def test_fresh_scope_and_rights(media_db,case,tenant_status):
    media_db.execute("UPDATE saas_tenants SET status=%s WHERE id=%s::uuid",(tenant_status,A))
    person={'tenant_id':A,'telegram_id':33};lead=41;message='connect:'+MID
    if case=='tenant':person={'tenant_id':B,'telegram_id':22}
    elif case=='channel':media_db.execute('UPDATE saas_crm_messages SET channel=%s WHERE tenant_id=%s::uuid',('connect:'+CB,A))
    elif case=='conversation':media_db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps({"conversationId":"other"}),A))
    elif case=='lead':lead=42
    elif case=='message':message='connect:'+str(uuid.uuid4())
    elif case=='permission':media_db.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33",(A,))
    elif case=='inactive':media_db.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=33",(A,))
    elif case=='disconnected':media_db.execute("UPDATE saas_tenant_integrations SET status='disconnected' WHERE tenant_id=%s::uuid",(A,))
    else:media_db.execute("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid",(A,))
    media_db.commit()
    with pytest.raises(TenantLinearError):store.load(person,lead,message)


def test_group_media_preserves_route_and_saved_transcript(media_db):
    media_db.execute("UPDATE saas_connect_conversations SET conversation_id='g@g.us' WHERE tenant_id=%s::uuid",(A,))
    media_db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps({'conversationId':'g@g.us'}),A));media_db.commit()
    assert store.load(PROFILE,41,'connect:'+MID)['route']['conversation_id']=='g@g.us'
    result=dialogs._message({'channel':'connect:'+CA,'direction':'incoming','message_type':'audio','body':'Salam! Привет!',
                             'media_url':'https://private-provider/secret','raw':{'participantJid':'person@s.whatsapp.net','durationSeconds':65,'voice_transcription':{'status':'completed'}}},group=True)
    assert result['author_type']=='participant' and result['has_transcription'] is True and result['body']=='Salam! Привет!'
    assert result['media_url']=='' and result['duration_seconds']==65 and 'raw' not in result
    assert 'has_transcription' not in dialogs._message({'channel':'connect:'+CA,'message_type':'audio','body':'','raw':{'voice_transcription':{'status':'failed'}}})


def test_voice_uses_the_same_protected_preview_and_transcript_contract(media_db):
    media_db.execute("UPDATE saas_crm_messages SET message_type='voice' WHERE tenant_id=%s::uuid",(A,));media_db.commit()
    result=store.load({'tenant_id':A,'telegram_id':33},41,'connect:'+MID,channel_id=CA,conversation_id='client')
    assert result['row']['message_type']=='voice' and result['route']['channel_id']==CA
    public=dialogs._message({'channel':'connect:'+CA,'direction':'incoming','message_type':'voice','body':'Hazır transkript',
        'raw':{'voice_transcription':{'status':'completed'},'durationSeconds':12}})
    assert public['has_transcription'] is True and public['duration_seconds']==12


def run(coroutine):return asyncio.run(coroutine)


def item():return {'provider':'connect','route':{'channel_id':CA,'conversation_id':'g@g.us','api_key':'test-key'},'message_id':MID,'row':{'message_type':'image'}}


def transport(monkeypatch,*,status=200,headers=None,chunks=None):
    response=MagicMock();response.status=status
    response.headers={'X-Connect-Channel-Id':CA,'X-Connect-Message-Id':MID,'Content-Type':'image/png',**(headers or {})}
    async def stream(_size):
        for chunk in chunks if chunks is not None else [b'picture']:yield chunk
    response.content.iter_chunked=stream
    request=MagicMock();request.__aenter__=AsyncMock(return_value=response);request.__aexit__=AsyncMock(return_value=False)
    session=MagicMock();session.get.return_value=request
    factory=MagicMock();factory.return_value.__aenter__=AsyncMock(return_value=session);factory.return_value.__aexit__=AsyncMock(return_value=False)
    monkeypatch.setattr(media.aiohttp,'ClientSession',factory)
    monkeypatch.setattr(media.aiohttp,'TCPConnector',Mock());monkeypatch.setattr(media,'PublicResolver',Mock())
    return factory,session


def test_scoped_url_timeout_and_no_redirects(monkeypatch):
    factory,session=transport(monkeypatch)
    assert run(media.download_media(item()))==(b'picture','image/png')
    url=session.get.call_args.args[0];options=session.get.call_args.kwargs
    assert '/media?conversationId=g%40g.us' in url and MID in url and CA in url
    assert options['allow_redirects'] is False and options['headers']['x-api-key']=='test-key'
    assert factory.call_args.kwargs['timeout'].total==30
    assert factory.call_args.kwargs['auto_decompress'] is False


def test_final_preview_limits_are_exact_and_keep_audio_unchanged():
    assert media.media_limit('image') == 20*1024*1024
    assert media.media_limit('audio') == media.media_limit('voice') == 10*1024*1024
    assert media.media_limit('video') == 100*1024*1024
    assert media.media_limit('document') == 5*1024*1024


@pytest.mark.parametrize('status',[404,410,413,415,429,500,302])
def test_safe_unavailable_and_provider_errors(monkeypatch,status):
    transport(monkeypatch,status=status)
    with pytest.raises(TenantLinearError) as error:run(media.download_media(item()))
    assert 'test-key' not in str(error.value)
    assert error.value.status==(404 if status in (404,410) else 502 if status in (500,302) else status)


@pytest.mark.parametrize('headers,chunks',[
    ({'Content-Type':'text/html'},None),({'Content-Type':'image/svg+xml'},None),
    ({'X-Connect-Channel-Id':CB},None),({'X-Connect-Message-Id':str(uuid.uuid4())},None),
    ({'Content-Encoding':'gzip'},None),({'Content-Length':str(20*1024*1024+1)},None),
    ({'Content-Length':'20'},[b'short']),({},[b'x'*(20*1024*1024+1)]),({},[])])
def test_mime_size_identity_and_truncation(monkeypatch,headers,chunks):
    transport(monkeypatch,headers=headers,chunks=chunks)
    with pytest.raises(TenantLinearError):run(media.download_media(item()))


@pytest.mark.parametrize('kind',['audio','voice'])
def test_audio_preview(monkeypatch,kind):
    transport(monkeypatch,headers={'Content-Type':'audio/ogg'},chunks=[b'OggSopus'])
    audio=item();audio['row']['message_type']=kind
    assert run(media.download_media(audio))==(b'OggSopus','audio/ogg')


@pytest.mark.parametrize('mime',['video/mp4','video/webm'])
def test_video_preview_accepts_100mib_and_rejects_one_byte_above(monkeypatch,mime):
    video=item();video['row']['message_type']='video'
    at_limit=b'\0'*(100*1024*1024)
    transport(monkeypatch,headers={'Content-Type':mime,'Content-Length':str(len(at_limit))},chunks=[at_limit])
    assert run(media.download_media(video))==(at_limit,mime)
    transport(monkeypatch,headers={'Content-Type':mime,'Content-Length':str(len(at_limit)+1)},chunks=[])
    with pytest.raises(TenantLinearError) as error:run(media.download_media(video))
    assert error.value.status==413


@pytest.mark.parametrize('mime',[
    'application/pdf', 'application/msword', 'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'text/plain', 'text/csv',
])
def test_attachment_preview_delivers_original_document_formats_without_expanding_ai_allowlist(monkeypatch,mime):
    _factory,session=transport(monkeypatch,headers={'Content-Type':mime},chunks=[b'original'])
    document=item();document['row']['message_type']='document'
    assert run(media.download_media(document,preview_documents=True))==(b'original',mime)
    assert mime in session.get.call_args.kwargs['headers']['Accept']


def test_ai_document_allowlist_does_not_expand_with_preview_formats(monkeypatch):
    transport(monkeypatch,headers={'Content-Type':'application/vnd.ms-excel'},chunks=[b'original'])
    document=item();document['row']['message_type']='document'
    with pytest.raises(TenantLinearError) as error:
        run(media.download_media(document))
    assert error.value.status==415


def test_attachment_api_serves_office_file_as_download_with_original_filename(monkeypatch):
    current=item();current['row']['message_type']='document'
    current['row']['raw']={'message':{'documentMessage':{'fileName':'maintenance.xlsx'}}}
    monkeypatch.setattr(api,'load',Mock(return_value=current))
    fetched=AsyncMock(return_value=(b'original-xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
    monkeypatch.setattr(media,'download_media',fetched)
    handle=api.handler(lambda _:PROFILE,AsyncMock(),Mock())
    request=SimpleNamespace(query={'lead_id':'41','message_id':'connect:'+MID,'expected_tenant_id':A,'expected_user_id':'11'},headers={})
    response=run(handle(request))
    assert response.status==200 and response.body==b'original-xlsx'
    assert response.content_type=='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    assert response.headers['Content-Disposition']=='attachment; filename="maintenance.xlsx"; filename*=UTF-8\'\'maintenance.xlsx'
    fetched.assert_awaited_once_with(current,preview_documents=True)


def test_preview_api_rechecks_authority_without_kommo_or_ai(monkeypatch):
    current=item();loader=Mock(return_value=current);monkeypatch.setattr(api,'load',loader)
    monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(b'picture','image/png')))
    provider=AsyncMock();handle=api.handler(lambda _:PROFILE,provider,Mock())
    request=SimpleNamespace(query={'lead_id':'41','message_id':'connect:'+MID,'expected_tenant_id':A,'expected_user_id':'11'})
    api.REQUESTS.clear()
    assert run(handle(request)).status==200
    provider.assert_not_called();assert loader.call_count==2
    loader.side_effect=[current,TenantLinearError('denied',403)]
    assert run(handle(request)).status==403
    api.REQUESTS.clear()


def test_per_actor_and_tenant_rate_limits():
    api.REQUESTS.clear()
    for _ in range(20):api.admit(PROFILE)
    with pytest.raises(TenantLinearError):api.admit(PROFILE)
    api.admit({'tenant_id':B,'telegram_id':11})
    for actor in (12,13):
        for _ in range(20):api.admit({'tenant_id':A,'telegram_id':actor})
    with pytest.raises(TenantLinearError):api.admit({'tenant_id':A,'telegram_id':14})
    api.REQUESTS.clear()


def test_local_rate_limit_retry_after_uses_actual_window(monkeypatch):
    import time
    api.REQUESTS.clear();api.REQUESTS[(A,'11')]=(20,time.monotonic()+31)
    with pytest.raises(TenantLinearError) as error:api.admit(PROFILE)
    assert error.value.status==429 and 1<=error.value.retry_after<=31
    api.REQUESTS.clear()


@pytest.mark.parametrize('url',['http://connect.test','https://127.0.0.1','https://[::1]','https://user:password@connect.test','https://connect.test/path','https://connect.test?secret=1'])
def test_unsafe_configured_origin_never_opens_network(monkeypatch,url):
    monkeypatch.setattr(media,'CONNECT_URL',url)
    factory,_=transport(monkeypatch)
    with pytest.raises(TenantLinearError):run(media.download_media(item()))
    factory.assert_not_called()


def test_dns_rebinding_private_answer_is_rejected():
    async def check():
        resolver=media.PublicResolver()
        try:
            with patch.object(media.aiohttp.resolver.DefaultResolver,'resolve',AsyncMock(return_value=[{'host':'127.0.0.1'}])):
                with pytest.raises(ValueError):await resolver.resolve('connect.test',443)
        finally:await resolver.close()
    run(check())


def test_transport_timeout_has_safe_error(monkeypatch):
    _,session=transport(monkeypatch)
    session.get.side_effect=TimeoutError('private-provider-key')
    with pytest.raises(TenantLinearError) as error:run(media.download_media(item()))
    assert error.value.status==502 and 'private-provider-key' not in str(error.value)


def test_upstream_429_retry_after_is_preserved(monkeypatch):
    transport(monkeypatch,status=429,headers={'Retry-After':'17'})
    with pytest.raises(TenantLinearError) as error:run(media.download_media(item()))
    assert error.value.status==429 and error.value.retry_after==17
    assert media.retry_after_seconds('99999')==3600
    assert media.retry_after_seconds('invalid')==60


def test_attachment_api_returns_remaining_retry_after(monkeypatch):
    current=item();monkeypatch.setattr(api,'load',Mock(return_value=current))
    limited=TenantLinearError('Try later',429);limited.retry_after=23
    monkeypatch.setattr(media,'download_media',AsyncMock(side_effect=limited))
    handle=api.handler(lambda _:PROFILE,AsyncMock(),Mock())
    request=SimpleNamespace(query={'lead_id':'41','message_id':'connect:'+MID,'expected_tenant_id':A,'expected_user_id':'11'})
    api.REQUESTS.clear();response=run(handle(request))
    assert response.status==429 and response.headers['Retry-After']=='23'
    api.REQUESTS.clear()


@pytest.mark.parametrize('status',['active','onboarding','ready_for_integration','suspended','archived'])
@pytest.mark.parametrize('kind',['image','audio','voice'])
def test_connect_preview_lifecycle_matches_read_only_dialogs(media_db,status,kind):
    media_db.execute("UPDATE saas_tenants SET status=%s WHERE id=%s::uuid",(status,A))
    media_db.execute("UPDATE saas_crm_messages SET message_type=%s WHERE tenant_id=%s::uuid",(kind,A));media_db.commit()
    person={'tenant_id':A,'telegram_id':33}
    if status in {'active','onboarding','ready_for_integration'}:
        assert store.load(person,41,'connect:'+MID)['route']['channel_id']==CA
    else:
        with pytest.raises(TenantLinearError) as error:store.load(person,41,'connect:'+MID)
        assert error.value.status==403
    assert media_db.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs')[0]['n']==0


def test_onboarding_does_not_expand_legacy_attachment_access(media_db):
    media_db.execute("UPDATE saas_tenants SET status='onboarding' WHERE id=%s::uuid",(A,));media_db.commit()
    with pytest.raises(TenantLinearError) as error:store.load(PROFILE,41,'kommo-file')
    assert error.value.status==403
