"""Server WebSocket gateway; production admission remains immutable-off.

Dependency callbacks are server code only, never parsed from the request.
"""
import asyncio
import base64
import json
import logging
import time
from aiohttp import WSMsgType, web

_LOG=logging.getLogger(__name__)
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
    'session_closed','output_authorization_rejected','voice_lease_rejected','response_not_inflight',
    'response_id_mismatch','provider_authority_rejected','provider_lease_rejected',
    'interrupted_before_audio','input_turn_commit_pending',
    'provider_audio_delta_invalid','provider_audio_delta_too_large',
    'provider_audio_base64_invalid','provider_audio_chunk_invalid','provider_audio_output_limit',
    'input_turn_queue_full','multiple_commits_for_input_turn','assistant_item_response_conflict',
    'audio_after_completion','overlapping_output_items','session_output_limit',
})
_SAFE_FAILURE_STAGES=frozenset({'provider_receive','provider_event'})
_SAFE_PROVIDER_EVENT_TYPES=frozenset({
    'error','session.created','session.updated','conversation.created',
    'conversation.item.created','conversation.item.input_audio_transcription.delta',
    'conversation.item.input_audio_transcription.completed',
    'conversation.item.input_audio_transcription.failed','input_audio_buffer.speech_started',
    'input_audio_buffer.speech_stopped','input_audio_buffer.committed',
    'response.created','response.done','response.output_item.added','response.output_item.done',
    'response.content_part.added','response.content_part.done','response.output_audio.delta',
    'response.output_audio.done','response.output_audio_transcript.delta',
    'response.output_audio_transcript.done','rate_limits.updated',
})
_SAFE_CLOSE_ERROR_KINDS=frozenset({'none','timeout','aiohttp_client_error','socket_error','unknown'})
_SAFE_CLIENT_READER_EXITS=frozenset({'unavailable','peer_close','peer_error','frame_rate_limit','queue_full','session_closed','stream_ended'})
_SAFE_CLIENT_TRANSPORT_KINDS=frozenset({'none','timeout','socket_error','transport_error'})
_SAFE_CLIENT_FAILURE_CODES=frozenset({
    'client_authority_rejected','client_lease_rejected','client_event_not_allowed',
    'client_authority_or_lease_rejected','client_scope_binding_rejected',
    'session_closed','invalid_pcm_frame','session_input_limit','input_turn_commit_pending',
    'input_turn_queue_full','invalid_playback_ack','invalid_playback_completion',
    'invalid_playback_start_ack','invalid_playback_failure_ack',
})
_SAFE_CLIENT_EVENT_TYPES=frozenset({'audio','played','played_all','playback_scheduled','playback_failed','mute','end','unavailable'})
_VOICE_ACTIVITY_COUNTERS=(
    'input_audio_frames','input_audio_bytes','vad_speech_started','vad_speech_stopped',
    'input_transcriptions_completed','responses_created','output_audio_chunks',
    'responses_completed','browser_playback_acks','vad_interrupt_candidates',
    'interrupt_notices_sent','vad_cancelled_responses','playback_interrupt_acks',
    'playback_completion_acks','audio_frames_ws_sent','browser_playback_scheduled',
    'browser_playback_failed','playback_context_running','playback_context_suspended',
    'input_audio_batches','input_audio_process_ms',
)


def _provider_failure_fields(stage,event,exc):
    """Return allowlisted protocol diagnostics, never provider/user payloads."""
    raw_code=getattr(exc,'diagnostic_code',None)
    if raw_code is None:
        raw_code=exc.args[0] if len(exc.args)==1 and isinstance(exc.args[0],str) else None
    code=raw_code if isinstance(raw_code,str) and raw_code in _SAFE_FAILURE_CODES else 'provider_error'
    safe_stage=stage if stage in _SAFE_FAILURE_STAGES else 'provider_error'
    raw_type=event.get('type') if isinstance(event,dict) else None
    if getattr(exc,'diagnostic_event_type',None)=='error':raw_type='error'
    event_type=raw_type if isinstance(raw_type,str) and raw_type in _SAFE_PROVIDER_EVENT_TYPES else 'unavailable'
    raw_close_code=getattr(exc,'diagnostic_close_code',None)
    close_code=(raw_close_code if type(raw_close_code) is int and
                ((1000<=raw_close_code<=1014 and raw_close_code not in (1004,1005,1006)) or
                 3000<=raw_close_code<=4999) else 'none')
    raw_transport_kind=getattr(exc,'diagnostic_transport_kind','none')
    transport_kind=(raw_transport_kind if isinstance(raw_transport_kind,str) and
                    raw_transport_kind in _SAFE_CLOSE_ERROR_KINDS else 'unknown')
    response_inflight=getattr(exc,'diagnostic_response_inflight',False) is True
    response_id_matches=getattr(exc,'diagnostic_response_id_matches',False) is True
    return code,safe_stage,event_type,close_code,transport_kind,response_inflight,response_id_matches


def _log_provider_failure(stage,event,exc):
    (code,safe_stage,event_type,close_code,transport_kind,response_inflight,response_id_matches)=\
        _provider_failure_fields(stage,event,exc)
    _LOG.warning('realtime_voice_provider_reader_failed code=%s stage=%s event_type=%s close_code=%s '
                 'transport_kind=%s response_inflight=%s response_id_matches=%s',
                 code,safe_stage,event_type,close_code,transport_kind,response_inflight,response_id_matches)


def _safe_client_failure_fields(event,exc):
    """Expose only bounded client protocol diagnostics, never payloads."""
    raw_kind=event.get('type') if isinstance(event,dict) else None
    event_type=raw_kind if isinstance(raw_kind,str) and raw_kind in _SAFE_CLIENT_EVENT_TYPES else 'unavailable'
    raw_code=getattr(exc,'diagnostic_code',None)
    if raw_code is None and len(getattr(exc,'args',()))==1 and isinstance(exc.args[0],str):
        raw_code=exc.args[0]
    code=raw_code if isinstance(raw_code,str) and raw_code in _SAFE_CLIENT_FAILURE_CODES else 'client_event_rejected'
    stage=('audio_input' if event_type=='audio' else
           'playback_ack' if event_type in ('played','played_all') else
           'mute' if event_type=='mute' else 'client_event')
    return code,stage,event_type


def _log_client_failure(event,exc):
    code,stage,event_type=_safe_client_failure_fields(event,exc)
    _LOG.warning('realtime_voice_client_event_failed code=%s stage=%s event_type=%s',code,stage,event_type)


def _safe_voice_activity_fields(ready,activity):
    """Return bounded numeric activity only; never include audio or transcript data."""
    raw_exit=activity.get('client_reader_exit') if isinstance(activity,dict) else None
    reader_exit=raw_exit if isinstance(raw_exit,str) and raw_exit in _SAFE_CLIENT_READER_EXITS else 'unavailable'
    raw_close=activity.get('client_close_code') if isinstance(activity,dict) else None
    close_code=(raw_close if type(raw_close) is int and
                ((1000<=raw_close<=1014 and raw_close not in (1004,1005,1006)) or 3000<=raw_close<=4999)
                else 'none')
    raw_transport=activity.get('client_transport_kind','none') if isinstance(activity,dict) else 'none'
    transport=(raw_transport if isinstance(raw_transport,str) and raw_transport in _SAFE_CLIENT_TRANSPORT_KINDS
               else 'transport_error')
    end_received=(activity.get('client_end_received') is True) if isinstance(activity,dict) else False
    values=[]
    for key in _VOICE_ACTIVITY_COUNTERS:
        value=activity.get(key,0) if isinstance(activity,dict) else 0
        values.append(min(value,10**9) if type(value) is int and value>=0 else 0)
    return (ready is True,reader_exit,close_code,transport,end_received,*values)


def _log_voice_activity(ready,activity):
    fields=_safe_voice_activity_fields(ready,activity)
    _LOG.info('realtime_voice_session_activity ready=%s client_reader_exit=%s client_close_code=%s '
              'client_transport_kind=%s client_end_received=%s input_audio_frames=%d input_audio_bytes=%d '
              'vad_speech_started=%d vad_speech_stopped=%d input_transcriptions_completed=%d '
              'responses_created=%d output_audio_chunks=%d responses_completed=%d browser_playback_acks=%d '
              'vad_interrupt_candidates=%d interrupt_notices_sent=%d vad_cancelled_responses=%d '
              'playback_interrupt_acks=%d playback_completion_acks=%d audio_frames_ws_sent=%d '
              'browser_playback_scheduled=%d browser_playback_failed=%d playback_context_running=%d '
              'playback_context_suspended=%d input_audio_batches=%d input_audio_process_ms=%d',*fields)


def _safe_client_transport_kind(error):
    if error is None:return 'none'
    if isinstance(error,asyncio.TimeoutError):return 'timeout'
    if isinstance(error,OSError):return 'socket_error'
    return 'transport_error'


async def _enqueue_client_message(incoming, message):
    """Apply bounded backpressure instead of dropping a live audio stream."""
    await incoming.put(message)


def _log_playback_queue_latency(event, received_at):
    """Measure only server-side time after a frame was read from the socket."""
    if isinstance(event,dict) and event.get('type') in ('played','played_all','playback_scheduled','playback_failed'):
        from tenant_operator_voice_session import _log_voice_latency
        _log_voice_latency('playback_ack_server_queue',received_at,time.monotonic())


def _expected_close_after_session_end(session,exc):
    return (session is not None and session.closed is True and
            getattr(exc,'diagnostic_code',None)=='websocket_closed')


def _startup_failure_code(exc, session, session_ready):
    """Expose only a precise pre-open, actor-scoped lease refusal."""
    from tenant_operator_voice_session import LeaseAlreadyActive
    if (not session_ready and type(exc) is LeaseAlreadyActive and
        exc.args == ('session_already_active',) and
        session is not None and getattr(session,'fence',None) is None):
        return 'voice_session_busy'
    return 'voice_unavailable'


class SocketClient:
    def __init__(self, ws, activity=None):
        self.ws=ws;self.activity=activity;self.close_task=None;self.terminal_sent=False
        self.send_lock=asyncio.Lock()

    @staticmethod
    def _consume_close(task):
        try:task.exception()
        except BaseException:pass

    def start_close(self):
        if self.close_task is None:
            self.close_task=asyncio.create_task(self.ws.close())
            self.close_task.add_done_callback(self._consume_close)
        return self.close_task

    async def close(self):
        await asyncio.shield(self.start_close())

    async def send(self, event):
        async with self.send_lock:
            await self._send_locked(event)

    async def _send_locked(self, event):
        terminal=event.get('type') in ('ended','end_unconfirmed')
        if self.terminal_sent:
            if terminal:return
            raise ConnectionError('voice_socket_closed')
        if event.get('type') == 'audio':
            event={'type':'audio','item_id':event['item_id'],
                   'pcm_base64':base64.b64encode(event['pcm']).decode('ascii')}
        if self.ws.closed:
            raise ConnectionError('voice_socket_closed')
        try:await self.ws.send_json(event)
        except Exception:
            if terminal:self.start_close()
            raise
        if isinstance(self.activity,dict):
            if event.get('type')=='audio':self.activity['audio_frames_ws_sent']+=1
            elif event.get('type')=='interrupt':self.activity['interrupt_notices_sent']+=1
        if terminal:
            self.terminal_sent=True
            self.start_close()


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
        activity={key:0 for key in _VOICE_ACTIVITY_COUNTERS}
        activity.update(client_reader_exit='unavailable',client_close_code=None,
                        client_transport_kind='none',client_end_received=False)
        session_ready=False
        socket_client=SocketClient(ws,activity)
        try:
            session=build_session(scope, socket_client)
            async def read_frames():
                count=0;window=time.monotonic()
                try:
                    async for message in ws:
                        now=time.monotonic()
                        if now-window>=1:count=0;window=now
                        count+=1
                        if message.type in (WSMsgType.CLOSE,WSMsgType.CLOSED,WSMsgType.CLOSING):
                            activity['client_reader_exit']='peer_close'
                            break
                        if message.type==WSMsgType.ERROR:
                            activity['client_reader_exit']='peer_error'
                            break
                        # A fully authorised provider is marked open just
                        # before ready is sent. A frame immediately following
                        # observable ready may arrive before that send returns;
                        # queue it rather than falsely treating it as pre-open.
                        if count>60:
                            activity['client_reader_exit']='frame_rate_limit'
                            break
                        if session.closed:
                            activity['client_reader_exit']='session_closed'
                            break
                        if message.type==WSMsgType.BINARY:
                            observer=getattr(session,'observe_client_audio',None)
                            if callable(observer):observer(bytes(message.data))
                        elif message.type==WSMsgType.TEXT:
                            observer=getattr(session,'observe_client_control',None)
                            if callable(observer):observer()
                            # A user-requested end is terminal: do not leave it
                            # behind buffered PCM that will no longer be used.
                            # Session.end still drains in-flight provider I/O
                            # and releases only the exact fenced lease.
                            try:control=json.loads(message.data) if len(message.data)<=512 else None
                            except (ValueError,TypeError):control=None
                            if isinstance(control,dict) and control=={'type':'end'}:
                                activity['client_end_received']=True
                                try:
                                    if callable(observer) and getattr(session,'duplex',False):
                                        await session.receive_client(control,control_observed=True)
                                    else:await session.receive_client(control)
                                except Exception as exc:_log_client_failure(control,exc)
                                break
                        # The audio consumer performs live authorization and
                        # lease checks before forwarding each frame. If it is
                        # briefly slower than the browser, pause this reader
                        # and let TCP flow control catch up instead of closing
                        # a healthy session and discarding queued speech.
                        await _enqueue_client_message(incoming,(message,time.monotonic()))
                finally:
                    close_code=ws.close_code
                    activity['client_close_code']=(close_code if type(close_code) is int else None)
                    activity['client_transport_kind']=_safe_client_transport_kind(ws.exception())
                    if activity['client_reader_exit']=='unavailable':activity['client_reader_exit']='stream_ended'
                    await session.end('disconnected')
                    socket_client.start_close()
                    while incoming.full(): incoming.get_nowait()
                    incoming.put_nowait(None)
            # A dedicated single reader detects disconnect even while open is
            # pending; client frames before ready cause a fail-closed stop.
            reader=asyncio.create_task(read_frames())
            await session.start()
            async def read_provider():
                stage='provider_receive'
                event=None
                duplex=getattr(session,'duplex',False)
                incoming_provider=asyncio.Queue(maxsize=128) if duplex else None
                observer=getattr(session,'observe_provider_event',None) if duplex else None
                async def receive_ahead():
                    try:
                        while not session.closed:
                            incoming=await session.next_provider_event()
                            # Observe VAD promptly even when handling an older
                            # event is waiting for authorization or lease IO.
                            if callable(observer):observer(incoming)
                            await incoming_provider.put((True,incoming))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        await incoming_provider.put((False,exc))
                provider_receiver=asyncio.create_task(receive_ahead()) if duplex else None
                try:
                    while not session.closed:
                        if duplex:
                            ok,event=await incoming_provider.get()
                            if not ok:raise event
                        else:
                            event=await session.next_provider_event()
                        stage='provider_event'
                        response_inflight_before=session.response_inflight
                        output_playing_before=bool(session.item)
                        if callable(observer):await session.receive_provider(event,already_observed=True)
                        else:await session.receive_provider(event)
                        if isinstance(event,dict):
                            kind=event.get('type')
                            if kind=='input_audio_buffer.speech_started':
                                activity['vad_speech_started']+=1
                                if response_inflight_before or output_playing_before:
                                    activity['vad_interrupt_candidates']+=1
                            elif kind=='input_audio_buffer.speech_stopped':activity['vad_speech_stopped']+=1
                            elif kind=='conversation.item.input_audio_transcription.completed':activity['input_transcriptions_completed']+=1
                            elif kind=='response.created':activity['responses_created']+=1
                            elif kind=='response.output_audio.delta':activity['output_audio_chunks']+=1
                            elif kind=='response.done':
                                activity['responses_completed']+=1
                                response=event.get('response')
                                details=response.get('status_details') if isinstance(response,dict) else None
                                if (isinstance(response,dict) and response.get('status')=='cancelled' and
                                    isinstance(details,dict) and details.get('reason')=='turn_detected'):
                                    activity['vad_cancelled_responses']+=1
                        stage='provider_receive'
                        event=None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not _expected_close_after_session_end(session,exc):
                        _log_provider_failure(stage,event,exc)
                    # Even when another task has already marked the session
                    # closed, wait for its shared end task to finish before
                    # closing the browser socket and racing the terminal frame.
                    await session.end('provider_disconnected')
                    socket_client.start_close()
                finally:
                    if provider_receiver is not None:
                        provider_receiver.cancel()
                        # Cancellation does not prove that a socket read ended.
                        # Session shutdown still drains receive_lock before lease
                        # release and retains its lease on an uncertain drain.
                        provider_receiver.add_done_callback(session._consume_task)
            if callable(getattr(session.provider,'receive',None)):
                provider_reader=asyncio.create_task(read_provider())
            session_ready=True
            deferred=None
            has_deferred=False
            while True:
                if has_deferred:
                    queued=deferred;deferred=None;has_deferred=False
                else:
                    queued=await incoming.get()
                if queued is None:
                    break
                message,received_at=queued
                if session.closed:
                    activity['client_reader_exit']='session_closed'
                    break
                if message.type == WSMsgType.BINARY:
                    pcm=bytes(message.data)
                    frame_count=1
                    # Coalesce only adjacent, already queued valid PCM frames.
                    # Never wait for more audio or move it across a control
                    # frame; one full authority/lease check protects the
                    # resulting single provider operation.
                    if (getattr(session,'duplex',False) and pcm and len(pcm)<=session.MAX_FRAME and len(pcm)%2==0):
                        while len(pcm)<session.MAX_FRAME:
                            try:next_queued=incoming.get_nowait()
                            except asyncio.QueueEmpty:break
                            if next_queued is None:
                                deferred=next_queued;has_deferred=True;break
                            next_message,_=next_queued
                            if next_message.type!=WSMsgType.BINARY:
                                deferred=next_queued;has_deferred=True;break
                            part=bytes(next_message.data)
                            if not part or len(part)%2 or len(pcm)+len(part)>session.MAX_FRAME:
                                deferred=next_queued;has_deferred=True;break
                            pcm+=part;frame_count+=1
                    event={'type':'audio','pcm':pcm}
                    audio_started=time.monotonic()
                    try:
                        if callable(getattr(session,'observe_client_audio',None)) and getattr(session,'duplex',False):
                            await session.receive_client(event,audio_observed=True)
                        else:await session.receive_client(event)
                    except Exception as exc:
                        _log_client_failure(event,exc)
                        raise
                    activity['input_audio_frames']+=frame_count
                    activity['input_audio_bytes']+=len(pcm)
                    activity['input_audio_batches']+=1
                    activity['input_audio_process_ms']+=int(min(time.monotonic()-audio_started,600)*1000)
                elif message.type == WSMsgType.TEXT:
                    if len(message.data)>512:
                        break
                    try: event=json.loads(message.data)
                    except (ValueError,TypeError): break
                    if not isinstance(event,dict) or event.get('type') not in ('end','mute','talk_start','stop_response','talk_end','played','played_all','playback_scheduled','playback_failed'):
                        break
                    _log_playback_queue_latency(event,received_at)
                    try:
                        if callable(getattr(session,'observe_client_control',None)) and getattr(session,'duplex',False):
                            await session.receive_client(event,control_observed=True)
                        else:await session.receive_client(event)
                    except Exception as exc:
                        _log_client_failure(event,exc)
                        raise
                    if event.get('type') in ('played','played_all'):
                        activity['browser_playback_acks']+=1
                        if event['type']=='played':activity['playback_interrupt_acks']+=1
                        else:activity['playback_completion_acks']+=1
                    elif event.get('type')=='playback_scheduled':
                        activity['browser_playback_scheduled']+=1
                        if event.get('context_state')=='running':activity['playback_context_running']+=1
                        elif event.get('context_state')=='suspended':activity['playback_context_suspended']+=1
                    elif event.get('type')=='playback_failed':activity['browser_playback_failed']+=1
                    if event.get('type')=='end':
                        activity['client_end_received']=True
                        break
                else:
                    break
        except Exception as exc:
            # Deliberately do not log request content, audio, or exception text.
            if not ws.closed and not (session_ready and session is not None and session.closed):
                # Report only the server's precise, pre-open lease refusal.  A
                # generic provider error must never be mistaken for proof that
                # no other voice session exists or that one has ended.
                await socket_client.send({'type':'error','code':_startup_failure_code(exc,session,session_ready)})
        finally:
            try:
                if session is not None:
                    await session.end('disconnected')
            finally:
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
                try:
                    await socket_client.close()
                finally:
                    _log_voice_activity(session_ready,activity)
        return ws
    return handler
