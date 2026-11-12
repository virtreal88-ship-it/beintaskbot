"""Server-owned, bounded ElevenLabs Text-to-Dialogue stream for one reply."""
import asyncio
import base64
import binascii
import logging
import os
import re
import time

from tenant_linear_policy import TenantLinearError
from tenant_operator_voice_pilot import PilotAdmissionError

VOICE_ID='UM9h0wiwZVQwUByy82ad'
TTD_URL=('wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input'
         '?model_id=eleven_v4_turbo&output_format=pcm_24000')
MAX_DELTA_CHARS=1600
MAX_RESPONSE_CHARS=24000
MAX_AUDIO_BYTES=4_800_000
TEXT_QUEUE_LIMIT=128
TEXT_FLUSH_SECONDS=.18
MAX_NO_SPACE_CHARS=120
MAX_CLIENT_AUDIO_BYTES=9600
AUDIO_COALESCE_SECONDS=.03
FIRST_PCM_TIMEOUT=10
NEXT_PCM_TIMEOUT=5
FINAL_DRAIN_TIMEOUT=120
FINAL_SEND_DRAIN_TIMEOUT=3
_LOG=logging.getLogger(__name__)
_SAFE_TTD_ERROR_CODES=frozenset({'authentication_required','invalid_api_key','voice_not_found',
                                  'quota_exceeded','max_character_limit_exceeded'})
_SAFE_TRANSPORT_CODES=frozenset({'websocket_closed','websocket_error','websocket_nontext_frame',
                                 'provider_message_too_large','provider_json_invalid','provider_event_invalid'})
_SAFE_TRANSPORT_KINDS=frozenset({'none','timeout','aiohttp_client_error','socket_error','unknown'})


def _safe_close_code(value):
    if type(value) is int and (1000<=value<=1015 or 3000<=value<=4999):return value
    return 'none'


class _TTDProviderError(ValueError):
    """Only safe fields from a terminal ElevenLabs error event."""
    def __init__(self,event):
        code=event.get('error')
        self.safe_code=code if isinstance(code,str) and code in _SAFE_TTD_ERROR_CODES else 'tts_provider_error'
        self.declared_close_code=_safe_close_code(event.get('code'))
        super().__init__('tts_provider_error')


def _log_tts_failure(stage,error):
    from tenant_operator_realtime_wire import SafeRealtimeFailure
    code='tts_provider_error';close_code='none';declared_close_code='none';transport='none'
    provider_error_event=isinstance(error,_TTDProviderError)
    if provider_error_event:
        code=error.safe_code;declared_close_code=error.declared_close_code
    elif isinstance(error,SafeRealtimeFailure):
        if isinstance(error.diagnostic_code,str) and error.diagnostic_code in _SAFE_TRANSPORT_CODES:code=error.diagnostic_code
        close_code=_safe_close_code(error.diagnostic_close_code)
        if isinstance(error.diagnostic_transport_kind,str) and error.diagnostic_transport_kind in _SAFE_TRANSPORT_KINDS:
            transport=error.diagnostic_transport_kind
    elif error.args and error.args[0] in ('tts_timeout','tts_close_unconfirmed','tts_admission_rejected'):
        code=error.args[0]
    if stage not in ('connect','receive','send'):stage='unknown'
    _LOG.warning('realtime_tts_failure provider=elevenlabs_ali stage=%s code=%s close_code=%s '
                 'provider_error_event=%s declared_close_code=%s transport_kind=%s',
                 stage,code,close_code,provider_error_event,declared_close_code,transport)


def _credential_valid(value):
    return isinstance(value,str) and 1<=len(value)<=4096 and all(33<=ord(ch)<=126 for ch in value)


class ElevenServerCredentialLoader:
    """Load only the server-managed credential; never expose it to a client."""
    def __init__(self,authority):
        from tenant_operator_voice_orchestration import ScopedAuthority
        if type(authority) is not ScopedAuthority:raise ValueError('eleven_authority_required')
        self.authority=authority

    async def load(self):
        value=os.environ.get('ELEVENLABS_API_KEY','')
        if not _credential_valid(value):raise TenantLinearError('Голосовой AI недоступен.',503)
        return value


async def _open_eleven_ttd_socket(guard,loader,is_current):
    # Import lazily to keep the transport dependency cycle explicit. The
    # existing connector disables environment proxies and refuses redirects.
    from tenant_operator_realtime_wire import (_new_owned_session,_connect_websocket,
                                                 _require_live_admission,AiohttpRealtimeSocket,CONNECT_DEADLINE)
    await _require_live_admission(guard)
    if not is_current():raise ValueError('tts_cancelled')
    key=await asyncio.wait_for(loader.load(),CONNECT_DEADLINE)
    session=_new_owned_session();socket=None
    try:
        if not _credential_valid(key) or not is_current():raise ValueError('tts_unavailable')
        try:
            ws=await _connect_websocket(session,TTD_URL,{'xi-api-key':key})
        except Exception as error:
            status=getattr(error,'status',None)
            if type(status) is not int or not 100<=status<=599:status='none'
            kind=type(error).__name__
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}',kind):kind='UnknownError'
            _LOG.warning('realtime_tts_connect_failed stage=websocket_upgrade provider=elevenlabs_ali error=%s status=%s',kind,status)
            raise ValueError('tts_provider_error') from None
        socket=AiohttpRealtimeSocket(ws,owned_session=session)
        await _require_live_admission(guard)
        if not is_current():raise ValueError('tts_cancelled')
        await socket.send_json({'voices':[VOICE_ID]})
        return socket
    except BaseException:
        try:
            if socket is not None:
                await socket.close()
            else:
                await session.close()
                if not session.closed:raise ValueError('tts_close_unconfirmed')
        except BaseException:
            raise ValueError('tts_close_unconfirmed') from None
        raise
    finally:
        key=None


class ElevenTTDStream:
    """Single response stream; cancellation never sends a flush command."""
    def __init__(self,guard,loader,epoch,is_current,emit,*,connector=None):
        self.guard=guard;self.loader=loader;self.epoch=epoch;self.is_current=is_current;self.emit=emit
        self.connector=connector or _open_eleven_ttd_socket
        self.queue=asyncio.Queue(maxsize=TEXT_QUEUE_LIMIT)
        self.task=None;self.socket=None;self.cancelled=False;self.finished=False
        self.text_chars=0;self.audio_bytes=0;self._input_finished=False;self._started=False;self._cleaning=False
        self._close_socket_started=False
        self.final_before_close_send_return=False
        self.close_unconfirmed=False;self.attempted_chars=0;self.pcm_chunks=0
        self.first_text_at=None;self.first_pcm_at=None
        self._text_submitted=asyncio.Event();self._pending_receive=None

    def start(self):
        if self.task is not None:raise ValueError('tts_started')
        self.task=asyncio.create_task(self._run())
        return self

    def feed(self,text):
        if (self.cancelled or self._input_finished or not isinstance(text,str) or not text or
            len(text)>MAX_DELTA_CHARS or self.text_chars+len(text)>MAX_RESPONSE_CHARS):
            raise ValueError('tts_text_invalid')
        self.text_chars+=len(text)
        if self.first_text_at is None:self.first_text_at=time.monotonic()
        try:self.queue.put_nowait(('text',text))
        except asyncio.QueueFull:raise ValueError('tts_input_backlog') from None

    def finish_input(self):
        if self.cancelled or self._input_finished:raise ValueError('tts_input_finished')
        self._input_finished=True
        try:self.queue.put_nowait(('finish',None))
        except asyncio.QueueFull:raise ValueError('tts_input_backlog') from None

    def cancel_now(self):
        if self.cancelled:return
        self.cancelled=True
        if self.task is not None and not self.task.done() and self._started and not self._cleaning:self.task.cancel()

    async def wait_closed(self):
        if self.task is not None:await asyncio.shield(self.task)
        if self.close_unconfirmed:raise ValueError('tts_close_unconfirmed')

    def _current(self):
        return not self.cancelled and not self._cleaning and self.is_current(self.epoch)

    async def _send(self,event):
        # Every text/flush operation may incur cost. Recheck the same live
        # identity, scoped authority and exact fenced lease before each send.
        if not self._current():raise asyncio.CancelledError()
        try:await self.guard.assert_live(self.guard.scope,self.guard.session_id)
        except (TenantLinearError,PilotAdmissionError):raise ValueError('tts_admission_rejected') from None
        if not self._current():raise asyncio.CancelledError()
        inputs=event.get('inputs')
        if isinstance(inputs,list):self.attempted_chars+=sum(len(item['text']) for item in inputs)
        await self.socket.send_json(event)
        if isinstance(inputs,list):self._text_submitted.set()

    async def _write(self):
        pending=''
        async def flush(force=False):
            nonlocal pending
            if not pending:return
            if force or len(pending)>=MAX_NO_SPACE_CHARS or pending[-1].isspace() or pending[-1] in '.!?;:,':
                text,pending=pending,''
            else:
                boundary=max((i+1 for i,ch in enumerate(pending) if ch.isspace() or ch in '.!?;:,'),default=0)
                if not boundary:return
                text,pending=pending[:boundary],pending[boundary:]
            if text.strip():
                await self._send({'inputs':[{'text':text,'voice_id':VOICE_ID}], 'flush':True})
        deadline=None
        while True:
            timeout=max(0,deadline-asyncio.get_running_loop().time()) if deadline is not None else None
            try:kind,text=await asyncio.wait_for(self.queue.get(),timeout)
            except asyncio.TimeoutError:
                await flush()
                deadline=asyncio.get_running_loop().time()+TEXT_FLUSH_SECONDS if pending else None
                continue
            if kind=='finish':
                await flush(force=True)
                # This command flushes buffered content, so it is used only
                # for a natural finish, never for interruption.
                # The provider can answer with is_final before this send task
                # gets its next event-loop turn. Mark the protocol boundary
                # before awaiting the write so _run does not mistake that
                # valid ordering for an early provider termination.
                self._close_socket_started=True
                await self._send({'close_socket':True})
                return
            pending+=text
            if deadline is None:deadline=asyncio.get_running_loop().time()+TEXT_FLUSH_SECONDS
            if ((len(pending)>=16 and pending[-1] in '.!?;:') or
                (len(pending)>=80 and any(ch.isspace() for ch in pending))):
                await flush()
                deadline=asyncio.get_running_loop().time()+TEXT_FLUSH_SECONDS if pending else None

    async def _read(self):
        pending=bytearray();next_event=None
        async def send_audio(size):
            pcm=bytes(pending[:size]);del pending[:size]
            if pcm and self._current():await self.emit(('audio',self.epoch,base64.b64encode(pcm).decode('ascii')))
        try:
            await self._text_submitted.wait()
            audio_deadline=asyncio.get_running_loop().time()+FIRST_PCM_TIMEOUT
            while True:
                if next_event is None:next_event=asyncio.create_task(self.socket.receive_json())
                remaining=audio_deadline-asyncio.get_running_loop().time()
                if remaining<=0:raise ValueError('tts_timeout')
                done,_=await asyncio.wait({next_event},timeout=min(remaining,AUDIO_COALESCE_SECONDS) if pending else remaining)
                if not done:
                    if pending:await send_audio(len(pending));continue
                    raise ValueError('tts_timeout')
                event=await next_event;next_event=None
                if not isinstance(event,dict):raise ValueError('tts_provider_error')
                if event.get('error') is not None:raise _TTDProviderError(event)
                encoded=event.get('audio')
                if encoded is not None:
                    if not isinstance(encoded,str) or not encoded or len(encoded)>4*((MAX_AUDIO_BYTES+2)//3):
                        raise ValueError('tts_audio_invalid')
                    try:pcm=base64.b64decode(encoded,validate=True)
                    except (ValueError,TypeError,binascii.Error):raise ValueError('tts_audio_invalid') from None
                    if not pcm or len(pcm)%2 or self.audio_bytes+len(pcm)>MAX_AUDIO_BYTES:raise ValueError('tts_audio_invalid')
                    if self.first_pcm_at is None:self.first_pcm_at=time.monotonic()
                    self.audio_bytes+=len(pcm);self.pcm_chunks+=1;pending.extend(pcm)
                    audio_deadline=asyncio.get_running_loop().time()+NEXT_PCM_TIMEOUT
                    while len(pending)>=MAX_CLIENT_AUDIO_BYTES:await send_audio(MAX_CLIENT_AUDIO_BYTES)
                if event.get('is_final') is True:
                    if pending:await send_audio(len(pending))
                    return
        finally:
            # In particular on a PCM timeout, leave the pending aiohttp read
            # alive until _run closes the socket and can confirm its handshake.
            self._pending_receive=next_event

    async def _run(self):
        self._started=True
        reader=writer=None;result='error';failure=None;stage='connect'
        try:
            if not self._current():raise asyncio.CancelledError()
            self.socket=await self.connector(self.guard,self.loader,self._current)
            if not self._current():raise asyncio.CancelledError()
            reader=asyncio.create_task(self._read());writer=asyncio.create_task(self._write())
            done,_=await asyncio.wait({reader,writer},return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                stage='receive'
                await reader
                if not writer.done():
                    if not self._close_socket_started:raise ValueError('tts_ended_early')
                    self.final_before_close_send_return=True
                    # A final frame may beat completion of send_json even
                    # though the provider has already received close_socket.
                    # Let that in-flight write settle briefly, then fail
                    # closed if the transport itself remains stuck.
                    try:await asyncio.wait_for(asyncio.shield(writer),FINAL_SEND_DRAIN_TIMEOUT)
                    except asyncio.TimeoutError:raise ValueError('tts_timeout') from None
            stage='send'
            await writer
            stage='receive'
            try:await asyncio.wait_for(asyncio.shield(reader),FINAL_DRAIN_TIMEOUT)
            except asyncio.TimeoutError:raise ValueError('tts_timeout') from None
            result='done'
        except asyncio.CancelledError:
            self.cancelled=True;result='cancelled'
        except Exception as error:
            _log_tts_failure(stage,error)
            failure=str(error) if str(error) in ('tts_close_unconfirmed','tts_timeout') else 'tts_provider_error'
            if failure=='tts_close_unconfirmed':self.close_unconfirmed=True
        finally:
            self._cleaning=True
            # Drain the writer first to stop further paid sends. Keep the
            # reader alive for the WS close handshake: cancelling aiohttp's
            # pending receive prematurely records close code 1006 even when
            # the peer closes normally, making confirmation impossible.
            if writer is not None:
                if not writer.done():writer.cancel()
                await asyncio.gather(writer,return_exceptions=True)
            if self.socket is not None:
                try:await self.socket.close()
                except Exception:failure='tts_close_unconfirmed';result='error';self.close_unconfirmed=True
            if reader is not None:
                if not reader.done():reader.cancel()
                await asyncio.gather(reader,return_exceptions=True)
            if self._pending_receive is not None:
                if not self._pending_receive.done():self._pending_receive.cancel()
                await asyncio.gather(self._pending_receive,return_exceptions=True)
            self.finished=True
            first_ms=(int(min(max(self.first_pcm_at-self.first_text_at,0),600)*1000)
                      if self.first_pcm_at is not None and self.first_text_at is not None else -1)
            _LOG.info('realtime_tts_activity provider=elevenlabs_ali attempted_chars=%d pcm_bytes=%d '
                      'pcm_chunks=%d first_text_to_pcm_ms=%d final_before_close_send_return=%s',
                      min(self.attempted_chars,MAX_RESPONSE_CHARS),min(self.audio_bytes,MAX_AUDIO_BYTES),
                      min(self.pcm_chunks,10**9),first_ms,self.final_before_close_send_return)
            await self.emit((result,self.epoch,failure))
