"""Isolated aiohttp WebSocket boundary; never registered on the real app.

Authenticating and constructing the real Copilot dependencies remain explicit
injected server responsibilities. This file contains no credential or provider.
"""
import base64
import json
import time
from aiohttp import WSMsgType, web


class SocketClient:
    def __init__(self, ws): self.ws=ws
    async def send(self, event):
        if event.get('type') == 'audio':
            event={'type':'audio','item_id':event['item_id'],
                   'pcm_base64':base64.b64encode(event['pcm']).decode('ascii')}
        if not self.ws.closed:
            await self.ws.send_json(event)
            if event.get('type')=='ended':
                await self.ws.close()


def make_handler(*, allowed_origins, authenticate, build_session):
    if (not allowed_origins or any(not isinstance(origin,str) or not origin.startswith('https://') for origin in allowed_origins)
            or not callable(authenticate) or not callable(build_session)):
        raise ValueError('trusted_gateway_configuration_required')
    allowed_origins=frozenset(allowed_origins)

    async def handler(request):
        # Origin is checked before both session creation and WebSocket upgrade.
        if request.headers.get('Origin') not in allowed_origins:
            raise web.HTTPForbidden()
        try:
            scope=await authenticate(request)
        except Exception:
            raise web.HTTPForbidden()
        if not isinstance(scope,tuple) or len(scope)!=4:
            raise web.HTTPForbidden()
        ws=web.WebSocketResponse(heartbeat=15, max_msg_size=9600, compress=False)
        ready=ws.can_prepare(request)
        if not ready.ok:
            raise web.HTTPBadRequest()
        await ws.prepare(request)
        session=None
        count=0
        window=time.monotonic()
        try:
            session=build_session(scope, SocketClient(ws))
            await session.start()
            async for message in ws:
                now=time.monotonic()
                if now-window>=1:
                    count=0;window=now
                count+=1
                if count>60:
                    break
                if message.type == WSMsgType.BINARY:
                    await session.receive_client({'type':'audio','pcm':bytes(message.data)})
                elif message.type == WSMsgType.TEXT:
                    if len(message.data)>512:
                        break
                    try: event=json.loads(message.data)
                    except (ValueError,TypeError): break
                    if not isinstance(event,dict) or event.get('type') not in ('end','mute','played','played_all'):
                        break
                    await session.receive_client(event)
                    if event.get('type')=='end': break
                else:
                    break
        except Exception:
            # Deliberately do not log request content, audio, or exception text.
            if not ws.closed:
                await ws.send_json({'type':'error','code':'voice_unavailable'})
        finally:
            try:
                if session is not None:
                    await session.end('disconnected')
            finally:
                await ws.close()
        return ws
    return handler
