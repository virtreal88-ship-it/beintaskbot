"""Deterministic local regression tests; no provider network, audio, or secrets."""
import asyncio
import base64
import json
import struct
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp import WSMessage, WSMsgType

from tenant_operator_voice_session import Session, Refused
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_realtime_wire import SyntheticRealtimeWire
from tenant_operator_voice_gateway import make_handler
from tests.test_tenant_operator_realtime_wire import Socket
from tests.test_tenant_operator_voice_duplex import Authority, Lease, Ledger, Client, Context, Provider


class BlockingContext(Context):
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def revalidate(self, _):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
        return True


class ProviderDefaultConversation(Provider):
    """Model documented provider auto-commit and response.input cutoff."""
    def __init__(self):
        super().__init__()
        self.conversation = ['turn1']
        self.session = None
        self.create_snapshots = []

    async def send(self, event):
        await super().send(event)
        if event['type'] == 'input_audio_buffer.append':
            self.conversation.append('turn2')
        if event['type'] == 'response.create':
            ids = [r['id'] for r in event.get('response', {}).get('input', [])]
            self.create_snapshots.append((self.session.current_input_item_id, list(self.conversation), ids))


def make_session(provider, context):
    s = Session(('tenant', 7, 'channel', 'dialog'), authority=Authority(), ledger=Ledger(),
                lease=Lease(), context=context, provider=provider, client=Client(), duplex=True)
    s.closed = False
    s.started = time.monotonic()
    s.fence = 1
    s._check_receipt = lambda *_: None
    return s


async def confirmed_user(session, item_id):
    await session.receive_provider({'type': 'conversation.item.added',
                                    'item': {'id': item_id, 'type': 'message', 'role': 'user'}})


async def turn(session, item_id):
    await session.receive_provider({'type': 'input_audio_buffer.speech_started'})
    await session.receive_provider({'type': 'input_audio_buffer.speech_stopped'})
    await confirmed_user(session, item_id)
    await session.receive_provider({'type': 'input_audio_buffer.committed', 'item_id': item_id})


class BufferedProvider(Provider):
    def __init__(self):
        super().__init__();self.incoming=asyncio.Queue();self.created=asyncio.Event()
    async def receive(self):
        event=await self.incoming.get()
        if event is None:raise EOFError()
        if isinstance(event,Exception):raise event
        return event
    async def send(self,event):
        await super().send(event)
        if event['type']=='response.create':self.created.set()
    async def close(self):self.incoming.put_nowait(None)


class ObservedSession(Session):
    async def start(self):self.closed=False;self.started=time.monotonic();self.fence=1
    def observe_provider_event(self,event):
        super().observe_provider_event(event)
        if self._observed_speech_epoch==2:self.second_observed.set()


class GatewaySocket:
    last=None
    def __init__(self,**_):
        self.closed=False;self.close_code=None;self.messages=[];self.incoming=asyncio.Queue()
        GatewaySocket.last=self
    def can_prepare(self,_):return SimpleNamespace(ok=True)
    async def prepare(self,_):pass
    async def send_json(self,message):self.messages.append(message)
    async def close(self):self.closed=True;self.incoming.put_nowait(None)
    def exception(self):return None
    def __aiter__(self):return self
    async def __anext__(self):
        event=await self.incoming.get()
        if event is None:raise StopAsyncIteration
        return event


class TrackedLease(Lease):
    def __init__(self):self.released=0
    async def release(self,*_):self.released+=1;return True


class PausedDispatchAuthority(Authority):
    def __init__(self):
        self.entered=asyncio.Event();self.release=asyncio.Event();self.blocked=False
        self.valid=True
    async def check(self,scope,stage):
        if not self.valid:raise Refused('permission_revoked')
        if stage=='pilot_dispatch' and not self.blocked:
            self.blocked=True;self.entered.set();await self.release.wait()


async def make_pilot(authority=None,lease=None,socket=None):
    scope=('tenant',7,'channel','dialog');authority=authority or Authority();lease=lease or Lease()
    socket=socket or Socket();session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
    provider=SyntheticRealtimeWire(socket,policy)
    session=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=provider,
                    client=Client(),pilot_policy=policy,session_id=session_id,max_seconds=600,duplex=True)
    await session.start()
    return session,provider,policy,socket


class CausalityRaceTest(unittest.IsolatedAsyncioTestCase):
    @unittest.expectedFailure
    async def test_quiet_pcm_can_precede_delayed_vad_without_stale_response(self):
        """Known open race: the provider gives no acknowledgement per PCM append."""
        s,provider,policy,socket=await make_pilot();s.VAD_SETTLE_SECONDS=.15
        original=s._wait_audio_boundary;boundary_done=asyncio.Event()
        async def notify(boundary,guard):
            result=await original(boundary,guard);boundary_done.set();return result
        s._wait_audio_boundary=notify
        dispatch=asyncio.create_task(turn(s,'turn1'))
        await asyncio.wait_for(boundary_done.wait(),1)
        # A quiet but nonzero one-frame utterance is forwarded unchanged. The
        # local 1% RMS heuristic cannot identify this as speech.
        quiet=struct.pack('<h',200)*512
        await s.receive_client({'type':'audio','pcm':quiet})
        async def delayed_provider_vad():
            await asyncio.sleep(.3)
            sent_before_vad=any(e['type']=='response.create' for e in socket.sent)
            s.observe_provider_event({'type':'input_audio_buffer.speech_started'})
            return sent_before_vad
        vad=asyncio.create_task(delayed_provider_vad())
        try:
            await asyncio.wait_for(dispatch,1)
            sent_before_vad=await asyncio.wait_for(vad,1)
            self.assertFalse(sent_before_vad,'quiet speech can still race provider VAD')
        finally:
            await s.end('test')

    async def test_gateway_pcm_backlog_and_control_barrier_preserve_fifo_without_deadlock(self):
        class InputPausedAuthority(PausedDispatchAuthority):
            def __init__(self):
                super().__init__();self.input_entered=asyncio.Event();self.input_release=asyncio.Event()
            async def check(self,scope,stage):
                await super().check(scope,stage)
                if stage=='input' and not self.input_release.is_set():
                    self.input_entered.set();await self.input_release.wait()
        class RuntimeSocket(Socket):
            def __init__(self):
                super().__init__();self.runtime=asyncio.Queue();self.live=False;self.created=asyncio.Event()
            async def receive_json(self):
                if self.live:
                    event=await self.runtime.get()
                    if event is None:raise EOFError()
                    return event
                event=await super().receive_json()
                if self.sent and self.sent[-1]['type']=='conversation.item.create':self.live=True
                return event
            async def send_json(self,event):
                await super().send_json(event)
                if event.get('type')=='response.create':self.created.set()
            async def close(self):
                await super().close();self.runtime.put_nowait(None)
        class RecordingSession(Session):
            async def _receive_client(self,event):
                if event.get('type')=='audio':self.recorded.append(('audio',event['pcm']))
                if event.get('type')=='mute':self.recorded.append(('mute',event['muted']))
                return await super()._receive_client(event)
        scope=('tenant',7,'channel','dialog');authority=InputPausedAuthority();lease=Lease();socket=RuntimeSocket()
        session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
        provider=SyntheticRealtimeWire(socket,policy)
        s=RecordingSession(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=provider,
                           client=Client(),pilot_policy=policy,session_id=session_id,max_seconds=600,duplex=True)
        s.VAD_SETTLE_SECONDS=.02;s.recorded=[]
        async def authenticate(_):return scope
        handler=make_handler(allowed_origins=['https://crm.pro.az'],authenticate=authenticate,
                             build_session=lambda _,client:s)
        with patch('tenant_operator_voice_gateway.web.WebSocketResponse',GatewaySocket):
            task=asyncio.create_task(handler(SimpleNamespace(headers={'Origin':'https://crm.pro.az'})))
            while not socket.live:await asyncio.sleep(0)
            for event in [
                {'type':'input_audio_buffer.speech_started'},
                {'type':'input_audio_buffer.speech_stopped'},
                {'type':'conversation.item.added','item':{'id':'turn1','type':'message','role':'user'}},
                {'type':'input_audio_buffer.committed','item_id':'turn1'},
            ]:socket.runtime.put_nowait(event)
            await asyncio.wait_for(authority.entered.wait(),1)
            pcm1=bytes(1024);pcm2=bytes(1024);pcm3=bytes(1024)
            for message in [WSMessage(WSMsgType.BINARY,pcm1,''),WSMessage(WSMsgType.BINARY,pcm2,''),
                            WSMessage(WSMsgType.TEXT,json.dumps({'type':'mute','muted':False}),''),
                            WSMessage(WSMsgType.BINARY,pcm3,'')]:
                GatewaySocket.last.incoming.put_nowait(message)
            await asyncio.wait_for(authority.input_entered.wait(),1)
            async def all_seen():
                while s._client_audio_observed_bytes!=3072 or s._pending_client_controls!=1:
                    await asyncio.sleep(0)
            await asyncio.wait_for(all_seen(),1)
            authority.input_release.set()
            async def first_two_forwarded():
                while s._client_audio_forwarded_bytes<2048:await asyncio.sleep(0)
            await asyncio.wait_for(first_two_forwarded(),1)
            authority.release.set()
            await asyncio.wait_for(socket.created.wait(),2)
            before=[];after=[];seen_mute=False
            for kind,value in s.recorded:
                if kind=='mute':seen_mute=True
                elif not seen_mute:before.append(value)
                else:after.append(value)
            self.assertTrue(seen_mute)
            self.assertEqual(b''.join(before),pcm1+pcm2)
            self.assertEqual(b''.join(after),pcm3)
            self.assertEqual(s._client_audio_forwarded_bytes,3072)
            self.assertEqual(sum(e['type']=='response.create' for e in socket.sent),1)
            GatewaySocket.last.incoming.put_nowait(WSMessage(WSMsgType.CLOSE,None,''))
            await asyncio.wait_for(task,2)

    async def test_new_voiced_pcm_after_snapshot_blocks_stale_send_but_silence_does_not(self):
        for voiced in (False,True):
            with self.subTest(voiced=voiced):
                s,provider,policy,socket=await make_pilot();s.VAD_SETTLE_SECONDS=.15
                original=s._wait_audio_boundary;boundary_done=asyncio.Event()
                async def notify(boundary,guard):
                    result=await original(boundary,guard);boundary_done.set();return result
                s._wait_audio_boundary=notify
                dispatch=asyncio.create_task(turn(s,'turn1'))
                await asyncio.wait_for(boundary_done.wait(),1)
                pcm=struct.pack('<h',s.LOCAL_SPEECH_RMS)*512 if voiced else bytes(1024)
                await s.receive_client({'type':'audio','pcm':pcm})
                await asyncio.wait_for(dispatch,2)
                self.assertEqual(sum(e['type']=='response.create' for e in socket.sent),0 if voiced else 1)
                appends=[e for e in socket.sent if e['type']=='input_audio_buffer.append']
                self.assertEqual(base64.b64decode(appends[-1]['audio']),pcm)
                await s.end('test')

    async def test_false_noise_gate_expires_during_continuous_noise_and_rearms_after_quiet(self):
        s,provider,policy,socket=await make_pilot();s.VAD_SETTLE_SECONDS=.02;s.LOCAL_SPEECH_GATE_TIMEOUT=.05
        loud=struct.pack('<h',s.LOCAL_SPEECH_RMS)*512
        quiet=struct.pack('<h',s.LOCAL_SPEECH_RMS-1)*512
        # Below-threshold audio is still sent verbatim and does not gate.
        await s.receive_client({'type':'audio','pcm':quiet})
        self.assertFalse(s._local_speech_active)
        self.assertEqual(base64.b64decode(socket.sent[-1]['audio']),quiet)
        await s.receive_client({'type':'audio','pcm':loud})
        self.assertTrue(s._local_speech_active)
        await turn(s,'turn1')
        self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
        async def noise():
            while True:
                await s.receive_client({'type':'audio','pcm':loud})
                await asyncio.sleep(.01)
        background=asyncio.create_task(noise())
        async def wait_for_response():
            while not any(e['type']=='response.create' for e in socket.sent):await asyncio.sleep(.01)
        try:
            await asyncio.wait_for(wait_for_response(),1)
        finally:
            background.cancel();await asyncio.gather(background,return_exceptions=True)
        self.assertTrue(s._local_speech_suppressed)
        for _ in range(3):await s.receive_client({'type':'audio','pcm':bytes(9600)})
        self.assertFalse(s._local_speech_suppressed)
        old_epoch=s._local_speech_epoch
        await s.receive_client({'type':'audio','pcm':loud})
        self.assertTrue(s._local_speech_active)
        self.assertEqual(s._local_speech_epoch,old_epoch+1)
        await s.end('test')

    async def test_noise_timeout_during_slow_consume_does_not_lose_retry(self):
        authority=PausedDispatchAuthority();s,provider,policy,socket=await make_pilot(authority=authority)
        s.LOCAL_SPEECH_GATE_TIMEOUT=.05;s.VAD_SETTLE_SECONDS=.01
        await s.receive_provider({'type':'input_audio_buffer.speech_started'})
        await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await confirmed_user(s,'turn1')
        dispatch=asyncio.create_task(s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn1'}))
        await asyncio.wait_for(authority.entered.wait(),1)
        await s.receive_client({'type':'audio','pcm':struct.pack('<h',s.LOCAL_SPEECH_RMS)*512})
        await asyncio.sleep(.08)
        self.assertTrue(s._local_speech_suppressed)
        authority.release.set()
        await asyncio.wait_for(dispatch,1)
        async def response_sent():
            while not any(e['type']=='response.create' for e in socket.sent):await asyncio.sleep(.01)
        try:
            await asyncio.wait_for(response_sent(),1)
            self.assertEqual(list(policy._permits.values()),[None,None])
        finally:await s.end('test')
        self.assertEqual(sum(e['type']=='response.create' for e in socket.sent),1)
        self.assertEqual(list(policy._permits.values()),[])  # Session cleanup clears all permits.

    async def test_pcm_arrives_during_consume_speech_blocks_dispatch_and_silence_does_not_starve(self):
        class PCMSocket(Socket):
            def __init__(self):super().__init__();self.session=None;self.audio_sent=asyncio.Event();self.voiced=asyncio.Event()
            async def send_json(self,event):
                await super().send_json(event)
                if event.get('type')=='input_audio_buffer.append':
                    self.audio_sent.set()
                    if any(base64.b64decode(event['audio'])) and not self.voiced.is_set():
                        self.voiced.set()
                        # Model a provider VAD event produced by the PCM that
                        # actually reached its socket, not by a test-side event.
                        self.session.observe_provider_event({'type':'input_audio_buffer.speech_started'})
        for voiced in (False,True):
            with self.subTest(voiced=voiced):
                authority=PausedDispatchAuthority();socket=PCMSocket()
                s,provider,policy,socket=await make_pilot(authority=authority,socket=socket)
                socket.session=s;s.VAD_SETTLE_SECONDS=.05
                await s.receive_provider({'type':'input_audio_buffer.speech_started'})
                await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
                await confirmed_user(s,'turn1')
                dispatch=asyncio.create_task(s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn1'}))
                await asyncio.wait_for(authority.entered.wait(),1)
                audio=bytes([1,0])*512 if voiced else bytes(1024)
                await asyncio.wait_for(s.receive_client({'type':'audio','pcm':audio}),1)
                await asyncio.wait_for(socket.audio_sent.wait(),1)
                self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
                async def stream_silence():
                    while True:
                        await s.receive_client({'type':'audio','pcm':bytes(1024)})
                        await asyncio.sleep(.01)
                background=None if voiced else asyncio.create_task(stream_silence())
                try:
                    authority.release.set()
                    await asyncio.wait_for(dispatch,2)
                    creates=[e for e in socket.sent if e['type']=='response.create']
                    self.assertEqual(len(creates),0 if voiced else 1)
                    self.assertTrue(any(e['type']=='input_audio_buffer.append' for e in socket.sent))
                finally:
                    if background:
                        background.cancel()
                        await asyncio.gather(background,return_exceptions=True)
                    await s.end('test')

    async def test_revocation_or_lost_fence_during_settle_blocks_response(self):
        for changed in ('authority','lease'):
            with self.subTest(changed=changed):
                authority=PausedDispatchAuthority();authority.blocked=True
                lease=Lease();s,provider,policy,socket=await make_pilot(authority=authority,lease=lease)
                s.VAD_SETTLE_SECONDS=.15
                original=provider.prepare_guarded_response;prepared=asyncio.Event()
                async def notify(event,guard):
                    ticket=await original(event,guard);prepared.set();return ticket
                provider.prepare_guarded_response=notify
                dispatch=asyncio.create_task(turn(s,'turn1'))
                await asyncio.wait_for(prepared.wait(),1)
                if changed=='authority':authority.valid=False
                else:lease.valid=False
                with self.assertRaises(Refused):await asyncio.wait_for(dispatch,2)
                self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
                self.assertTrue(s.closed)

    async def test_revocation_or_lost_fence_while_waiting_for_io_lock_blocks_response(self):
        for changed in ('authority','lease'):
            with self.subTest(changed=changed):
                authority=PausedDispatchAuthority();authority.blocked=True
                lease=Lease();s,provider,policy,socket=await make_pilot(authority=authority,lease=lease)
                s.VAD_SETTLE_SECONDS=.01
                original=provider.prepare_guarded_response;prepared=asyncio.Event()
                async def notify(event,guard):
                    ticket=await original(event,guard);prepared.set();return ticket
                provider.prepare_guarded_response=notify
                await s.io_lock.acquire()
                dispatch=asyncio.create_task(turn(s,'turn1'))
                try:
                    await asyncio.wait_for(prepared.wait(),1)
                    await asyncio.sleep(.05)
                    self.assertFalse(dispatch.done())
                    if changed=='authority':authority.valid=False
                    else:lease.valid=False
                finally:s.io_lock.release()
                with self.assertRaises(Refused):await asyncio.wait_for(dispatch,2)
                self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
                self.assertTrue(s.closed)

    async def test_pcm_observed_during_final_locked_check_prevents_voiced_stale_send(self):
        class FinalCheckAuthority(Authority):
            def __init__(self):self.count=0;self.entered=asyncio.Event();self.release=asyncio.Event()
            async def check(self,scope,stage):
                if stage=='response_dispatch':
                    self.count+=1
                    if self.count==2:self.entered.set();await self.release.wait()
        authority=FinalCheckAuthority();s,provider,policy,socket=await make_pilot(authority=authority)
        s.VAD_SETTLE_SECONDS=.01
        dispatch=asyncio.create_task(turn(s,'turn1'))
        await asyncio.wait_for(authority.entered.wait(),1)
        pcm=struct.pack('<h',s.LOCAL_SPEECH_RMS)*512
        audio=asyncio.create_task(s.receive_client({'type':'audio','pcm':pcm}))
        async def audio_observed():
            while s._client_audio_observed_bytes<len(pcm):await asyncio.sleep(0)
        await asyncio.wait_for(audio_observed(),1)
        authority.release.set()
        await asyncio.wait_for(dispatch,1)
        await asyncio.wait_for(audio,1)
        self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
        self.assertEqual(base64.b64decode(socket.sent[-1]['audio']),pcm)
        await s.end('test')

    async def test_unforwarded_audio_boundary_timeout_fails_closed(self):
        s,provider,policy,socket=await make_pilot();s.AUDIO_BOUNDARY_TIMEOUT=.05
        s.observe_client_audio(bytes(1024))
        with self.assertRaisesRegex(Refused,'client_audio_boundary_timeout'):
            await asyncio.wait_for(turn(s,'turn1'),1)
        self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
        self.assertTrue(s.closed)

    async def test_existing_audio_snapshot_can_drain_after_five_seconds(self):
        s,provider,policy,socket=await make_pilot();s.VAD_SETTLE_SECONDS=.01
        s.observe_client_audio(bytes(1024))
        dispatch=asyncio.create_task(turn(s,'turn1'))
        try:
            await asyncio.sleep(5.0)
            self.assertFalse(s.closed)
            await s.receive_client({'type':'audio','pcm':bytes(1024)},audio_observed=True)
            await asyncio.wait_for(dispatch,2)
            self.assertTrue(any(e['type']=='response.create' for e in socket.sent))
        finally:await s.end('test')

    async def test_all_documented_user_item_ack_orders_are_supported(self):
        for kind in ('conversation.item.added','conversation.item.created','conversation.item.done'):
            for ack_first in (False,True):
                with self.subTest(kind=kind,ack_first=ack_first):
                    provider=Provider();s=make_session(provider,Context())
                    await s.receive_provider({'type':'input_audio_buffer.speech_started'})
                    await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
                    ack={'type':kind,'item':{'id':'turn1','type':'message','role':'user'}}
                    commit={'type':'input_audio_buffer.committed','item_id':'turn1'}
                    await s.receive_provider(ack if ack_first else commit)
                    self.assertFalse(any(e['type']=='response.create' for e in provider.events))
                    await s.receive_provider(commit if ack_first else ack)
                    self.assertEqual(sum(e['type']=='response.create' for e in provider.events),1)

    async def test_unfinished_user_item_does_not_enter_response_context(self):
        provider=Provider();s=make_session(provider,Context())
        await s.receive_provider({'type':'input_audio_buffer.speech_started'})
        await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await s.receive_provider({'type':'conversation.item.added',
                                  'item':{'id':'turn1','type':'message','role':'user','status':'in_progress'}})
        await s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn1'})
        self.assertFalse(any(e['type']=='response.create' for e in provider.events))
        await s.receive_provider({'type':'conversation.item.done',
                                  'item':{'id':'turn1','type':'message','role':'user','status':'completed'}})
        self.assertEqual(sum(e['type']=='response.create' for e in provider.events),1)

    async def test_gateway_observes_speech_while_older_event_waits_for_checks(self):
        provider=BufferedProvider();context=BlockingContext();scope=('tenant',7,'channel','dialog')
        s=ObservedSession(scope,authority=Authority(),ledger=Ledger(),lease=Lease(),context=context,
                          provider=provider,client=Client(),duplex=True)
        s._check_receipt=lambda *_:None;s.second_observed=asyncio.Event()
        async def authenticate(_):return scope
        handler=make_handler(allowed_origins=['https://crm.pro.az'],authenticate=authenticate,
                             build_session=lambda _,client:s)
        with patch('tenant_operator_voice_gateway.web.WebSocketResponse',GatewaySocket):
            task=asyncio.create_task(handler(SimpleNamespace(headers={'Origin':'https://crm.pro.az'})))
            for event in [
                {'type':'input_audio_buffer.speech_started'},
                {'type':'input_audio_buffer.speech_stopped'},
                {'type':'conversation.item.added','item':{'id':'turn1','type':'message','role':'user'}},
                {'type':'input_audio_buffer.committed','item_id':'turn1'},
            ]:provider.incoming.put_nowait(event)
            await asyncio.wait_for(context.entered.wait(),1)
            # Fill the bounded provider-event queue past its capacity before
            # the next VAD event. A stale response must not jump the backlog.
            for _ in range(140):provider.incoming.put_nowait({'type':'rate_limits.updated'})
            provider.incoming.put_nowait({'type':'input_audio_buffer.speech_started'})
            async def wait_for_full_queue():
                while s._pending_provider_events<129:await asyncio.sleep(0)
            await asyncio.wait_for(wait_for_full_queue(),1)
            context.release.set()
            await asyncio.wait_for(s.second_observed.wait(),2)
            for event in [
                {'type':'input_audio_buffer.speech_stopped'},
                {'type':'conversation.item.added','item':{'id':'turn2','type':'message','role':'user'}},
                {'type':'input_audio_buffer.committed','item_id':'turn2'},
            ]:provider.incoming.put_nowait(event)
            await asyncio.wait_for(provider.created.wait(),1)
            creates=[e for e in provider.events if e['type']=='response.create']
            self.assertEqual(len(creates),1)
            self.assertEqual([e['id'] for e in creates[0]['response']['input']],['turn1','turn2'])
            GatewaySocket.last.incoming.put_nowait(WSMessage(WSMsgType.CLOSE,None,''))
            await asyncio.wait_for(task,2)

    async def test_gateway_close_while_provider_queue_is_full_drains_before_releasing_lease(self):
        provider=BufferedProvider();context=BlockingContext();scope=('tenant',7,'channel','dialog')
        lease=TrackedLease();client=Client()
        s=ObservedSession(scope,authority=Authority(),ledger=Ledger(),lease=lease,context=context,
                          provider=provider,client=client,duplex=True)
        s._check_receipt=lambda *_:None;s.second_observed=asyncio.Event()
        async def authenticate(_):return scope
        handler=make_handler(allowed_origins=['https://crm.pro.az'],authenticate=authenticate,
                             build_session=lambda _,client:s)
        with patch('tenant_operator_voice_gateway.web.WebSocketResponse',GatewaySocket):
            task=asyncio.create_task(handler(SimpleNamespace(headers={'Origin':'https://crm.pro.az'})))
            for event in [
                {'type':'input_audio_buffer.speech_started'},
                {'type':'input_audio_buffer.speech_stopped'},
                {'type':'conversation.item.added','item':{'id':'turn1','type':'message','role':'user'}},
                {'type':'input_audio_buffer.committed','item_id':'turn1'},
            ]:provider.incoming.put_nowait(event)
            await asyncio.wait_for(context.entered.wait(),1)
            for _ in range(140):provider.incoming.put_nowait({'type':'rate_limits.updated'})
            async def wait_for_full_queue():
                while s._pending_provider_events<129:await asyncio.sleep(0)
            await asyncio.wait_for(wait_for_full_queue(),1)
            GatewaySocket.last.incoming.put_nowait(WSMessage(WSMsgType.CLOSE,None,''))
            while not s.closed:await asyncio.sleep(0)
            self.assertEqual(lease.released,0)
            context.release.set()
            await asyncio.wait_for(task,2)
            self.assertFalse(any(e['type']=='response.create' for e in provider.events))
            self.assertEqual(lease.released,1)
            self.assertTrue(any(e['type']=='ended' for e in client.events))

    async def test_stalled_provider_read_keeps_lease_when_shutdown_cannot_drain(self):
        class StalledProvider(Provider):
            def __init__(self):
                super().__init__();self.entered=asyncio.Event();self.release_read=asyncio.Event()
            async def receive(self):
                self.entered.set()
                await self.release_read.wait()
                return {'type':'rate_limits.updated'}
            async def close(self):pass
        provider=StalledProvider();s=make_session(provider,Context());lease=TrackedLease();s.lease=lease
        s.shutdown_timeout=.05
        reader=asyncio.create_task(s.next_provider_event())
        await asyncio.wait_for(provider.entered.wait(),1)
        try:
            self.assertFalse(await asyncio.wait_for(s.end('test'),1))
            self.assertEqual(lease.released,0)
        finally:
            provider.release_read.set()
            await asyncio.wait_for(reader,1)

    async def test_provider_receive_failure_keeps_stage_and_redacts_details(self):
        provider=BufferedProvider();scope=('tenant',7,'channel','dialog')
        s=ObservedSession(scope,authority=Authority(),ledger=Ledger(),lease=Lease(),context=Context(),
                          provider=provider,client=Client(),duplex=True)
        s.second_observed=asyncio.Event()
        async def authenticate(_):return scope
        handler=make_handler(allowed_origins=['https://crm.pro.az'],authenticate=authenticate,
                             build_session=lambda _,client:s)
        with patch('tenant_operator_voice_gateway.web.WebSocketResponse',GatewaySocket):
            with self.assertLogs('tenant_operator_voice_gateway',level='WARNING') as logs:
                task=asyncio.create_task(handler(SimpleNamespace(headers={'Origin':'https://crm.pro.az'})))
                provider.incoming.put_nowait({'type':'rate_limits.updated'})
                provider.incoming.put_nowait(ConnectionError('sensitive failure content'))
                await asyncio.wait_for(task,2)
            self.assertTrue(any('stage=provider_receive' in line for line in logs.output))
            self.assertNotIn('sensitive',str(logs.output))

    async def test_context_snapshot_excludes_future_remote_commit_during_await(self):
        provider = ProviderDefaultConversation()
        context = BlockingContext()
        s = make_session(provider, context)
        provider.session = s
        await s.receive_provider({'type': 'input_audio_buffer.speech_started'})
        await s.receive_provider({'type': 'input_audio_buffer.speech_stopped'})
        await confirmed_user(s, 'turn1')
        pending = asyncio.create_task(s.receive_provider({'type': 'input_audio_buffer.committed', 'item_id': 'turn1'}))
        await asyncio.wait_for(context.entered.wait(), 1)
        await s.receive_client({'type': 'audio', 'pcm': bytes(480)})
        context.release.set()
        await asyncio.wait_for(pending, 1)
        self.assertEqual(provider.create_snapshots, [('turn1', ['turn1', 'turn2'], ['turn1'])])

    async def test_observed_new_speech_during_authority_wait_defers_response_until_new_commit(self):
        provider = ProviderDefaultConversation()
        context = BlockingContext()
        s = make_session(provider, context)
        provider.session = s
        await s.receive_provider({'type': 'input_audio_buffer.speech_started'})
        await s.receive_provider({'type': 'input_audio_buffer.speech_stopped'})
        await confirmed_user(s, 'turn1')
        pending = asyncio.create_task(s.receive_provider({'type': 'input_audio_buffer.committed', 'item_id': 'turn1'}))
        await asyncio.wait_for(context.entered.wait(), 1)
        new_start = {'type': 'input_audio_buffer.speech_started'}
        s.observe_provider_event(new_start)
        await s.receive_client({'type': 'audio', 'pcm': bytes(480)})
        context.release.set()
        await asyncio.wait_for(pending, 1)
        self.assertEqual(provider.create_snapshots, [])
        await s.receive_provider(new_start, already_observed=True)
        await s.receive_provider({'type': 'input_audio_buffer.speech_stopped'})
        await confirmed_user(s, 'turn2')
        await s.receive_provider({'type': 'input_audio_buffer.committed', 'item_id': 'turn2'})
        self.assertEqual(provider.create_snapshots, [('turn2', ['turn1', 'turn2'], ['turn1', 'turn2'])])

    async def test_multiple_pending_turns_wait_for_active_speech_and_coalesce(self):
        provider = Provider()
        s = make_session(provider, Context())
        await turn(s, 'turn1')
        await s.receive_provider({'type': 'response.created', 'response': {'id': 'response1'}})
        await s.receive_provider({'type': 'response.output_audio.delta', 'response_id': 'response1',
                                  'item_id': 'output1', 'content_index': 0,
                                  'delta': base64.b64encode(bytes(480)).decode()})
        await turn(s, 'turn2')
        await s.receive_provider({'type': 'input_audio_buffer.speech_started'})
        self.assertNotIn('output1',[e['id'] for e in s._context_refs()])
        await s.receive_provider({'type': 'response.done', 'response': {'id': 'response1', 'output': []}})
        await s.receive_provider({'type': 'conversation.item.truncated', 'item_id': 'output1',
                                  'content_index': 0, 'audio_end_ms': 0})
        self.assertNotIn('output1',[e['id'] for e in s._context_refs()])
        self.assertFalse(s.input_turn['stopped'])
        self.assertEqual(sum(e['type'] == 'response.create' for e in provider.events), 1)
        await s.receive_provider({'type': 'input_audio_buffer.speech_stopped'})
        await confirmed_user(s, 'turn3')
        await s.receive_provider({'type': 'input_audio_buffer.committed', 'item_id': 'turn3'})
        creates = [e for e in provider.events if e['type'] == 'response.create']
        self.assertEqual(len(creates), 2)
        self.assertEqual(s.current_input_item_id, 'turn3')
        self.assertEqual([item['id'] for item in creates[-1]['response']['input']], ['turn1','turn2','turn3'])
        self.assertNotIn('output1', [item['id'] for item in creates[-1]['response']['input']])

    async def test_only_confirmed_user_items_and_completed_playback_enter_context(self):
        provider = Provider()
        s = make_session(provider, Context())
        await s.receive_provider({'type': 'input_audio_buffer.speech_started'})
        await s.receive_provider({'type': 'input_audio_buffer.speech_stopped'})
        await s.receive_provider({'type': 'input_audio_buffer.committed', 'item_id': 'turn1'})
        self.assertFalse(any(e['type'] == 'response.create' for e in provider.events))
        await confirmed_user(s, 'turn1')
        await s.receive_provider({'type': 'response.created', 'response': {'id': 'response1'}})
        await s.receive_provider({'type': 'response.output_audio.delta', 'response_id': 'response1',
                                  'item_id': 'output1', 'content_index': 0,
                                  'delta': base64.b64encode(bytes(480)).decode()})
        self.assertEqual([x['id'] for x in s._context_refs()], ['turn1'])
        await s.receive_client({'type': 'playback_scheduled', 'item_id': 'output1', 'context_state': 'running'})
        self.assertEqual([x['id'] for x in s._context_refs()], ['turn1'])
        await s.receive_provider({'type':'response.output_audio.done','response_id':'response1',
                                  'item_id':'output1','content_index':0})
        await s.receive_provider({'type':'response.done','response':{'id':'response1','output':[]}})
        await s.receive_client({'type':'played_all','item_id':'output1'})
        await turn(s,'turn2')
        self.assertEqual([x['id'] for x in provider.events[-1]['response']['input']],
                         ['turn1','output1','turn2'])

    async def test_confirmed_partial_truncation_retains_only_played_prefix_in_context(self):
        provider=Provider();s=make_session(provider,Context())
        await turn(s,'turn1')
        await s.receive_provider({'type':'response.created','response':{'id':'response1'}})
        await s.receive_provider({'type':'response.output_audio.delta','response_id':'response1',
                                  'item_id':'output1','content_index':0,
                                  'delta':base64.b64encode(bytes(480)).decode()})
        await s.receive_client({'type':'stop_response','item_id':'output1','audio_end_ms':5})
        await turn(s,'turn2')
        await s.receive_provider({'type':'response.done','response':{'id':'response1','output':[]}})
        self.assertEqual([e['id'] for e in s._context_refs()],['turn1','turn2'])
        await s.receive_provider({'type':'conversation.item.truncated','item_id':'output1',
                                  'content_index':0,'audio_end_ms':5})
        self.assertEqual([e['id'] for e in provider.events[-1]['response']['input']],
                         ['turn1','output1','turn2'])

    async def test_guard_after_one_use_permit_consume_cancels_stale_dispatch(self):
        class BarrierAuthority(Authority):
            def __init__(self):
                self.entered=asyncio.Event();self.release=asyncio.Event();self.blocked=False
            async def check(self, scope, stage):
                if stage=='pilot_dispatch' and not self.blocked:
                    self.blocked=True;self.entered.set();await self.release.wait()
        scope=('tenant',7,'channel','dialog');authority=BarrierAuthority();lease=Lease();client=Client()
        session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
        socket=Socket();provider=SyntheticRealtimeWire(socket,policy)
        s=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=provider,
                  client=client,pilot_policy=policy,session_id=session_id,max_seconds=600,duplex=True)
        await s.start()
        try:
            initial=next(e for e in socket.sent if e['type']=='conversation.item.create')
            self.assertEqual(initial['item']['content'][0]['text'],
                             'Untrusted dialog context: {"lines":[],"extra_context":{}}')
            await s.receive_provider({'type':'input_audio_buffer.speech_started'})
            await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await confirmed_user(s,'turn1')
            pending=asyncio.create_task(s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn1'}))
            await asyncio.wait_for(authority.entered.wait(),1)
            new_start={'type':'input_audio_buffer.speech_started'}
            s.observe_provider_event(new_start)
            authority.release.set()
            await asyncio.wait_for(pending,1)
            self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
            self.assertEqual(list(policy._permits.values()),[None])
            await s.receive_provider(new_start,already_observed=True)
            await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await confirmed_user(s,'turn2')
            await s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn2'})
            creates=[e for e in socket.sent if e['type']=='response.create']
            self.assertEqual(len(creates),1)
            self.assertEqual([e['id'] for e in creates[0]['response']['input']],
                             [initial['item']['id'],'turn1','turn2'])
            self.assertEqual(list(policy._permits.values()),[None,None])
        finally:
            await s.end('test')

    async def test_shutdown_during_permit_check_sends_no_response_and_drains_before_release(self):
        class BarrierAuthority(Authority):
            def __init__(self):self.entered=asyncio.Event();self.release=asyncio.Event()
            async def check(self,scope,stage):
                if stage=='pilot_dispatch':self.entered.set();await self.release.wait()
        scope=('tenant',7,'channel','dialog');authority=BarrierAuthority();lease=TrackedLease();client=Client()
        session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
        socket=Socket();provider=SyntheticRealtimeWire(socket,policy)
        s=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=provider,
                  client=client,pilot_policy=policy,session_id=session_id,max_seconds=600,duplex=True)
        await s.start()
        await s.receive_provider({'type':'input_audio_buffer.speech_started'})
        await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await confirmed_user(s,'turn1')
        pending=asyncio.create_task(s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn1'}))
        await asyncio.wait_for(authority.entered.wait(),1)
        end_task=asyncio.create_task(s.end('test'))
        while not s.closed:await asyncio.sleep(0)
        self.assertEqual(lease.released,0)
        authority.release.set()
        try:await asyncio.wait_for(pending,1)
        except (Refused,ValueError):pass
        result=await asyncio.wait_for(end_task,1)
        self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
        self.assertEqual(lease.released,1 if result else 0)


if __name__ == '__main__':
    unittest.main()

