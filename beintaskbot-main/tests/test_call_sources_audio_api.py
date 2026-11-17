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
