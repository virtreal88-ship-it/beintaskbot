"""Server WebSocket gateway; production admission remains immutable-off.

Dependency callbacks are server code only, never parsed from the request.
"""
import asyncio
import base64
import json
import logging
import re
import time
from aiohttp import WSMsgType, web

_LOG=logging.getLogger(__name__)
_SAFE_EVENT_TYPE=re.compile(r'^(?:session|conversation|response|input_audio_buffer|output_audio_buffer|rate_limits|error)\.[a-z0-9_]+(?:\.[a-z0-9_]+){0,4}$')
_SAFE_FAILURE_CODES=frozenset({
    'provider_configuration_changed','provider_session_changed','provider_conversation_invalid',
    'provider_event_invalid','provider_event_forbidden','provider_item_forbidden',
    'provider_context_unconfirmed','unsafe_session_readback','session_not_ready',
    'wire_json_invalid','wire_event_invalid','unmatched_input_transcript_delta',
    'unmatched_input_transcript','unmatched_input_transcript_failure','invalid_committed_input',
    'unmatched_committed_input','unreserved_response','unreserved_or_duplicate_response',
    'invalid_audio_completion','invalid_provider_audio','input_response_pending',
    'websocket_closed','websocket_error','websocket_nontext_frame',
    'provider_message_too_large','provider_json_invalid','provider_event_invalid',
    'invalid_api_key','model_not_found','rate_limit_exceeded','server_error',
    'invalid_request_error','session_expired','input_audio_buffer_commit_empty',
    'input_audio_buffer_empty','audio_unintelligible','input_audio_transcription_failed',
})


def _provider_failure_fields(stage,event,exc):
    """Return only fixed failure codes and protocol metadata for safe logs."""
    raw_code=getattr(exc,'diagnostic_code',None)
    if raw_code is None:
        raw_code=exc.args[0] if len(exc.args)==1 and isinstance(exc.args[0],str) else None
    code=raw_code if isinstance(raw_code,str) and raw_code in _SAFE_FAILURE_CODES else 'provider_error'
    raw_type=event.get('type') if isinstance(event,dict) else None
    if getattr(exc,'diagnostic_event_type',None)=='error':raw_type='error'
    event_type=raw_type if raw_type=='error' or (isinstance(raw_type,str) and _SAFE_EVENT_TYPE.fullmatch(raw_type)) else 'unavailable'
    safe_stage=stage if stage in ('provider_receive','provider_event') else 'provider_error'
    return code,safe_stage,event_type


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
                stage='provider_receive'
                event=None
                try:
                    while not session.closed:
                        event=await session.next_provider_event()
                        stage='provider_event'
                        await session.receive_provider(event)
                        stage='provider_receive'
                        event=None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    code,safe_stage,event_type=_provider_failure_fields(stage,event,exc)
                    _LOG.warning('realtime_voice_provider_reader_failed code=%s stage=%s event_type=%s',
                                 code,safe_stage,event_type)
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
