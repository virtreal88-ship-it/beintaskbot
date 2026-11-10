"""Server-owned, bounded ElevenLabs Text-to-Dialogue stream for one reply."""
import asyncio
import base64
import binascii
import logging
import os
import re
import time

from tenant_linear_policy import TenantLinearError

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
_LOG=logging.getLogger(__name__)


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
        self.close_unconfirmed=False;self.attempted_chars=0;self.pcm_chunks=0
        self.first_text_at=None;self.first_pcm_at=None
        self._text_submitted=asyncio.Event()

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
        return not self.cancelled and self.is_current(self.epoch)

    async def _send(self,event):
        # Every text/flush operation may incur cost. Recheck the same live
        # identity, scoped authority and exact fenced lease before each send.
        if not self._current():raise asyncio.CancelledError()
        await self.guard.assert_live(self.guard.scope,self.guard.session_id)
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
                if not isinstance(event,dict) or event.get('error') is not None:raise ValueError('tts_provider_error')
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
            if next_event is not None:
                next_event.cancel();await asyncio.gather(next_event,return_exceptions=True)

    async def _run(self):
        self._started=True
        reader=writer=None;result='error';failure=None
        try:
            if not self._current():raise asyncio.CancelledError()
            self.socket=await self.connector(self.guard,self.loader,self._current)
            if not self._current():raise asyncio.CancelledError()
            reader=asyncio.create_task(self._read());writer=asyncio.create_task(self._write())
            done,_=await asyncio.wait({reader,writer},return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
                if not writer.done():raise ValueError('tts_ended_early')
            await writer
            try:await asyncio.wait_for(asyncio.shield(reader),FINAL_DRAIN_TIMEOUT)
            except asyncio.TimeoutError:raise ValueError('tts_timeout') from None
            result='done'
        except asyncio.CancelledError:
            self.cancelled=True;result='cancelled'
        except Exception as error:
            failure=str(error) if str(error) in ('tts_close_unconfirmed','tts_timeout') else 'tts_provider_error'
            if failure=='tts_close_unconfirmed':self.close_unconfirmed=True
        finally:
            self._cleaning=True
            # Stop further sends before closing; a successful WS close is
            # required, but does not prove remote compute was cancelled.
            for task in (writer,reader):
                if task is not None and not task.done():task.cancel()
            if self.socket is not None:
                try:await self.socket.close()
                except Exception:failure='tts_close_unconfirmed';result='error';self.close_unconfirmed=True
            tasks=[task for task in (writer,reader) if task is not None]
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
            self.finished=True
            first_ms=(int(min(max(self.first_pcm_at-self.first_text_at,0),600)*1000)
                      if self.first_pcm_at is not None and self.first_text_at is not None else -1)
            _LOG.info('realtime_tts_activity provider=elevenlabs_ali attempted_chars=%d pcm_bytes=%d '
                      'pcm_chunks=%d first_text_to_pcm_ms=%d',min(self.attempted_chars,MAX_RESPONSE_CHARS),
                      min(self.audio_bytes,MAX_AUDIO_BYTES),min(self.pcm_chunks,10**9),first_ms)
            await self.emit((result,self.epoch,failure))
