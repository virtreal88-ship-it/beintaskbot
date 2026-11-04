"""Server WebSocket gateway; production admission remains immutable-off.

Dependency callbacks are server code only, never parsed from the request.
"""
import asyncio
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
        if self.ws.closed:
            if event.get('type') in ('ended','end_unconfirmed'):
                return  # already disconnected; no client notification possible
            raise ConnectionError('voice_socket_closed')
        await self.ws.send_json(event)
        if event.get('type') in ('ended','end_unconfirmed'):
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
        reader=None
        provider_reader=None
        incoming=asyncio.Queue(maxsize=60)
        try:
            session=build_session(scope, SocketClient(ws))
            async def read_frames():
                count=0;window=time.monotonic()
                try:
                    async for message in ws:
                        now=time.monotonic()
                        if now-window>=1:count=0;window=now
                        count+=1
                        # A fully authorised provider is marked open just
                        # before ready is sent. A frame immediately following
                        # observable ready may arrive before that send returns;
                        # queue it rather than falsely treating it as pre-open.
                        if count>60 or session.closed or incoming.full():
                            break
                        incoming.put_nowait(message)
                finally:
                    await session.end('disconnected')
                    await ws.close()
                    while incoming.full(): incoming.get_nowait()
                    incoming.put_nowait(None)
            # A dedicated single reader detects disconnect even while open is
            # pending; client frames before ready cause a fail-closed stop.
            reader=asyncio.create_task(read_frames())
            await session.start()
            async def read_provider():
                try:
                    while not session.closed:
                        event=await session.next_provider_event()
                        await session.receive_provider(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await session.end('provider_disconnected')
                    await ws.close()
            if callable(getattr(session.provider,'receive',None)):
                provider_reader=asyncio.create_task(read_provider())
            while True:
                message=await incoming.get()
                if message is None:
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
                if reader is not None:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                if provider_reader is not None:
                    provider_reader.cancel()
                    # Never block the handler forever on a cancellation-
                    # suppressing provider; Session already retains the lease
                    # unless its receive lock and socket were drained.
                    done,pending=await asyncio.wait({provider_reader},timeout=.5)
                    for task in pending:task.add_done_callback(session._consume_task)
        return ws
    return handler
