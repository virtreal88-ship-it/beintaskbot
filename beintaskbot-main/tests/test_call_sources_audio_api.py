"""Real local WebSockets and synthetic PCM; no microphones/provider calls."""
import asyncio
from unittest.mock import Mock
import pytest
from aiohttp import WSMsgType,web
from aiohttp.test_utils import TestClient,TestServer
from tenant_linear_policy import TenantLinearError
import tenant_call_sources as sources
from tenant_call_sources_api import handler


def test_relay_identity_origin_pcm_and_live_permission_revocation(monkeypatch):
    async def run():
        tenant='00000000-0000-0000-0000-000000000001';call='00000000-0000-0000-0000-000000000002'
        gateway='00000000-0000-0000-0000-000000000003';nonce='00000000-0000-0000-0000-000000000004'
        profile={'tenant_id':tenant,'telegram_id':11};revoked=False;connections=[]
        async def engine(request):
            assert request.headers.get('Authorization')=='Bearer '+('x'*48)
            assert not request.headers.get('Cookie') and not request.headers.get('Origin')
            connections.append(request.path)
            socket=web.WebSocketResponse(max_msg_size=6400);await socket.prepare(request)
            async for message in socket:
                if message.type!=WSMsgType.BINARY:break
                await socket.send_bytes(message.data)
            return socket
        private=web.Application();private.router.add_get('/v1/sources/{source}/audio/{provider}/{call}',engine)
        async with TestServer(private) as upstream:
            monkeypatch.setattr(sources,'service',lambda:(str(upstream.make_url('')).rstrip('/'),'x'*48))
            def authority(p,c,n):
                if revoked or p!=profile or c!=call or n!=nonce:raise TenantLinearError('call_audio_denied',403)
                return gateway,'provider-synthetic',call
            monkeypatch.setattr(sources,'audio_authority',authority)
            app=web.Application();app.router.add_get('/calls/{call_id}/audio',handler(lambda _:profile,'https://app.invalid',Mock(),'audio'))
            async with TestClient(TestServer(app)) as client:
                url=f'/calls/{call}/audio?expected_tenant_id={tenant}&expected_user_id=11&request_id={nonce}'
                response=await client.get(url,headers={'Origin':'https://other.invalid'});assert response.status==403 and not connections
                response=await client.get(url.replace(nonce,'invalid'),headers={'Origin':'https://app.invalid'});assert response.status==403 and not connections
                async with client.ws_connect(url,headers={'Origin':'https://app.invalid'}) as socket:
                    raw=bytes([0,64,0,192])*100
                    await socket.send_bytes(raw)
                    message=await asyncio.wait_for(socket.receive(),2)
                    assert message.type==WSMsgType.BINARY and message.data==raw
                    revoked=True
                    ended=await asyncio.wait_for(socket.receive(),5)
                    assert ended.type in {WSMsgType.CLOSE,WSMsgType.CLOSED}
                assert connections==[f'/v1/sources/{gateway}/audio/provider-synthetic/{call}']
    asyncio.run(run())


def test_prebrowser_provider_backlog_hits_existing_rate_guard_safely(monkeypatch):
    """Reproduce the old ordering: 3 seconds PCM queued before browser readiness.

    Keep this guard. Engine now starts the provider only after browser PCM, so
    this initial burst cannot accumulate during command/poll/auth round trips.
    """
    async def run():
        tenant='00000000-0000-0000-0000-000000000001';call='00000000-0000-0000-0000-000000000002'
        profile={'tenant_id':tenant,'telegram_id':11};logger=Mock()
        async def engine(request):
            socket=web.WebSocketResponse();await socket.prepare(request)
            for _ in range(50):await socket.send_bytes(bytes(1920))
            async for _ in socket:pass
            return socket
        private=web.Application();private.router.add_get('/v1/sources/g/audio/p/{call}',engine)
        async with TestServer(private) as upstream:
            monkeypatch.setattr(sources,'service',lambda:(str(upstream.make_url('')).rstrip('/'),'x'*48))
            monkeypatch.setattr(sources,'audio_authority',lambda *a:('g','p',call))
            app=web.Application();app.router.add_get('/calls/{call_id}/audio',handler(lambda _:profile,'https://app.invalid',logger,'audio'))
            async with TestClient(TestServer(app)) as client:
                url=f'/calls/{call}/audio?expected_tenant_id={tenant}&expected_user_id=11&request_id=synthetic'
                async with client.ws_connect(url,headers={'Origin':'https://app.invalid'}) as socket:
                    while True:
                        message=await asyncio.wait_for(socket.receive(),3)
                        if message.type!=WSMsgType.BINARY:break
                logger.info.assert_any_call(
                    'call_audio_relay_end stage=stream code=%s input_received=%s output_received=%s',
                    'provider_rate_limit',False,True)
    asyncio.run(run())


def test_close_evidence_never_exposes_exception_or_unknown_peer_code():
    from tenant_call_sources_api import close_evidence
    for code,error,expected in [
        (1006,asyncio.TimeoutError('secret upstream detail'),(1006,'timeout')),
        (1006,ConnectionResetError('secret'),(1006,'connection_lost')),
        (1000,None,(1000,'none')),
        (4001,None,(4001,'none')),  # bounded client transport buffer overflow
        (4123,RuntimeError('secret'),(0,'transport_error')),
        (None,None,(0,'none')),
    ]:
        socket=Mock(close_code=code);socket.exception.return_value=error
        assert close_evidence(socket)==expected
