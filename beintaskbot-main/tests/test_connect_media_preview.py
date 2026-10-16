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


@pytest.mark.parametrize('case',['tenant','channel','conversation','lead','message','permission','inactive','disconnected','disabled'])
def test_fresh_scope_and_rights(media_db,case):
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
                            'media_url':'https://private-provider/secret','raw':{'participantJid':'person@s.whatsapp.net','voice_transcription':{'status':'completed'}}},group=True)
    assert result['author_type']=='participant' and result['has_transcription'] is True and result['body']=='Salam! Привет!'
    assert result['media_url']=='' and 'raw' not in result
    assert 'has_transcription' not in dialogs._message({'channel':'connect:'+CA,'message_type':'audio','body':'','raw':{'voice_transcription':{'status':'failed'}}})


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


@pytest.mark.parametrize('status',[404,410,413,415,429,500,302])
def test_safe_unavailable_and_provider_errors(monkeypatch,status):
    transport(monkeypatch,status=status)
    with pytest.raises(TenantLinearError) as error:run(media.download_media(item()))
    assert 'test-key' not in str(error.value)
    assert error.value.status==(404 if status in (404,410) else 502 if status in (500,302) else status)


@pytest.mark.parametrize('headers,chunks',[
    ({'Content-Type':'text/html'},None),({'Content-Type':'image/svg+xml'},None),
    ({'X-Connect-Channel-Id':CB},None),({'X-Connect-Message-Id':str(uuid.uuid4())},None),
    ({'Content-Encoding':'gzip'},None),({'Content-Length':str(8*1024*1024+1)},None),
    ({'Content-Length':'20'},[b'short']),({},[b'x'*(8*1024*1024+1)]),({},[])])
def test_mime_size_identity_and_truncation(monkeypatch,headers,chunks):
    transport(monkeypatch,headers=headers,chunks=chunks)
    with pytest.raises(TenantLinearError):run(media.download_media(item()))


def test_audio_preview(monkeypatch):
    transport(monkeypatch,headers={'Content-Type':'audio/ogg'},chunks=[b'OggSopus'])
    audio=item();audio['row']['message_type']='audio'
    assert run(media.download_media(audio))==(b'OggSopus','audio/ogg')


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
