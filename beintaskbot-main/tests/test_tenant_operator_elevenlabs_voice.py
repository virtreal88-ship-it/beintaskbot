"""Synthetic bridge and local loopback transport tests; no external calls or real speech."""
import asyncio
import base64
import copy
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch
from aiohttp import web

from tenant_operator_elevenlabs_voice import (ElevenTTDStream,VOICE_ID,_credential_valid,
                                              _TTDProviderError,_log_tts_failure)
from tenant_operator_realtime_wire import (SyntheticHybridRealtimeWire,AiohttpRealtimeSocket,
                                            _connect_websocket,_new_owned_session)
from tenant_linear_policy import TenantLinearError
from tenant_operator_voice_session import Session
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_voice_analytics import realtime_definitions


class Guard:
    scope=('tenant',7,'channel','dialog');session_id='synthetic-session'
    def __init__(self):self.checks=0;self.valid=True
    async def assert_live(self,*_):
        self.checks+=1
        if not self.valid:raise ValueError('revoked')


class TTDSocket:
    def __init__(self,*,close_fails=False,emit_audio=True):
        self.sent=[];self.incoming=asyncio.Queue();self.closed=False
        self.close_fails=close_fails;self.emit_audio=emit_audio
    async def send_json(self,event):
        self.sent.append(copy.deepcopy(event))
        if event.get('inputs') and self.emit_audio:
            await self.incoming.put({'audio':base64.b64encode(bytes(480)).decode()})
        if event.get('close_socket'):await self.incoming.put({'is_final':True})
    async def receive_json(self):return await self.incoming.get()
    async def close(self):
        self.closed=True
        if self.close_fails:raise ValueError('synthetic failure')


class FinalBeforeSendReturnsTTDSocket(TTDSocket):
    async def send_json(self,event):
        if event.get('close_socket'):
            self.sent.append(copy.deepcopy(event))
            await self.incoming.put({'is_final':True})
            # Simulate a fast provider response arriving before aiohttp's
            # send coroutine has returned to the writer task.
            await asyncio.sleep(.02)
            return
        await super().send_json(event)


class SlowCloseTTDSocket(TTDSocket):
    def __init__(self,*,close_fails=False):
        super().__init__(close_fails=close_fails)
        self.close_entered=asyncio.Event();self.close_release=asyncio.Event()
    async def close(self):
        self.close_entered.set();await self.close_release.wait()
        await super().close()


class OpenAISocket:
    synthetic_only=True
    def __init__(self):self.sent=[];self.incoming=asyncio.Queue();self.closed=False;self.first=True
    async def send_json(self,event):
        self.sent.append(copy.deepcopy(event))
        if event['type']=='session.update':
            await self.incoming.put({'type':'session.updated','session':{**copy.deepcopy(event['session']),'id':'realtime-session'}})
        elif event['type']=='conversation.item.create':
            await self.incoming.put({'type':'conversation.item.added','item':copy.deepcopy(event['item'])})
    async def receive_json(self):
        if self.first:self.first=False;return {'type':'session.created','session':{'id':'realtime-session'}}
        return await self.incoming.get()
    async def close(self):self.closed=True


class Authority:
    async def check(self,*_):pass
class Lease:
    async def claim(self,*_):return 1
    async def assert_current(self,*_):pass
    async def release(self,*_):return True


class TTDStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_final_frame_can_arrive_before_close_socket_send_returns(self):
        guard=Guard();socket=FinalBeforeSendReturnsTTDSocket();events=asyncio.Queue()
        async def connector(*_):return socket
        stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
        stream.feed('Hello, synthetic voice!')
        self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'audio')
        stream.finish_input()
        self.assertEqual(await asyncio.wait_for(events.get(),2),('done',1,None))
        await stream.wait_closed()
        self.assertTrue(socket.closed)
        self.assertTrue(stream.final_before_close_send_return)
        self.assertTrue(any(event.get('close_socket') for event in socket.sent))

    async def test_streams_russian_and_azerbaijani_and_logs_only_aggregates(self):
        for phrase in ('Привет, как дела?', 'Salam, məni eşidirsən?'):
            with self.subTest(language='ru' if phrase.startswith('Привет') else 'az'):
                guard=Guard();socket=TTDSocket();events=asyncio.Queue()
                async def connector(*_):return socket
                stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
                with self.assertLogs('tenant_operator_elevenlabs_voice',level='INFO') as logs:
                    stream.feed(phrase)
                    event=await asyncio.wait_for(events.get(),2)
                    self.assertEqual(event[:2],('audio',1))
                    self.assertEqual(base64.b64decode(event[2]),bytes(480))
                    stream.finish_input()
                    self.assertEqual((await asyncio.wait_for(events.get(),2))[:2],('done',1))
                    await stream.wait_closed()
                sent=[event for event in socket.sent if 'inputs' in event]
                self.assertEqual(''.join(event['inputs'][0]['text'] for event in sent),phrase)
                self.assertTrue(all(event['inputs'][0]['voice_id']==VOICE_ID for event in sent))
                self.assertEqual(guard.checks,len(sent)+1)
                self.assertTrue(socket.closed)
                self.assertNotIn(phrase,repr(logs.output));self.assertIn('provider=elevenlabs_ali',repr(logs.output))

    async def test_cancel_never_sends_flush_close_and_waits_for_socket_close(self):
        guard=Guard();socket=TTDSocket();events=asyncio.Queue()
        async def connector(*_):return socket
        stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
        stream.feed('Hello, synthetic voice!')
        self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'audio')
        stream.cancel_now();await stream.wait_closed()
        self.assertTrue(socket.closed)
        self.assertFalse(any(event.get('close_socket') for event in socket.sent))

    async def test_unconfirmed_close_and_connect_cleanup_propagate(self):
        guard=Guard();events=asyncio.Queue();socket=TTDSocket(close_fails=True)
        async def connector(*_):return socket
        stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
        stream.feed('Hello, synthetic voice!');await asyncio.wait_for(events.get(),2)
        stream.cancel_now()
        with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):await stream.wait_closed()
        async def uncertain(*_):raise ValueError('tts_close_unconfirmed')
        other=ElevenTTDStream(guard,object(),2,lambda _epoch:True,events.put,connector=uncertain).start()
        with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):await other.wait_closed()

    async def test_cancel_during_natural_or_failed_close_does_not_cancel_cleanup(self):
        for fail in (False,True):
            with self.subTest(close_fails=fail):
                guard=Guard();events=asyncio.Queue();socket=SlowCloseTTDSocket(close_fails=fail)
                async def connector(*_):return socket
                stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
                stream.feed('Hello, synthetic voice!');await asyncio.wait_for(events.get(),2)
                stream.finish_input();await asyncio.wait_for(socket.close_entered.wait(),2)
                stream.cancel_now();waiting=asyncio.create_task(stream.wait_closed())
                await asyncio.sleep(0);self.assertFalse(waiting.done())
                socket.close_release.set()
                if fail:
                    with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):await waiting
                else:await waiting
                self.assertTrue(socket.closed)

    async def test_no_audio_timeout_and_revocation_are_fail_closed(self):
        guard=Guard();socket=TTDSocket(emit_audio=False);events=asyncio.Queue()
        async def connector(*_):return socket
        with patch('tenant_operator_elevenlabs_voice.FIRST_PCM_TIMEOUT',.05):
            stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
            stream.feed('Hello, synthetic voice!')
            self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_timeout'))
            await stream.wait_closed();self.assertTrue(socket.closed)
        denied=Guard();denied.valid=False;socket=TTDSocket();events=asyncio.Queue()
        async def second_connector(*_):return socket
        stream=ElevenTTDStream(denied,object(),2,lambda _epoch:True,events.put,connector=second_connector).start()
        stream.feed('Hello, synthetic voice!')
        self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'error')
        await stream.wait_closed();self.assertFalse(any('inputs' in event for event in socket.sent))

    def test_key_validation_never_accepts_header_injection(self):
        self.assertTrue(_credential_valid('synthetic-token'))
        for key in ('','non ascii ё','a\nb','a\rb',None):self.assertFalse(_credential_valid(key))


class TTDLoopbackCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def make_stream(self,mode,*,provider_code='authentication_required'):
        received=asyncio.Event();server_closed=asyncio.Event();server_state={'code':None,'sent':[]}
        async def handler(request):
            ws=web.WebSocketResponse();await ws.prepare(request)
            server_state['peer']=ws
            try:
                async for message in ws:
                    import json
                    event=json.loads(message.data);server_state['sent'].append(event)
                    if 'inputs' in event:
                        received.set()
                        if mode=='abort':request.transport.abort();break
                        if mode=='provider_error':
                            await ws.send_json({'error':provider_code,'message':'private synthetic text',
                                                'param':'private synthetic parameter','code':1008})
                            await ws.close(code=1008);break
                        if mode=='normal':
                            await ws.send_json({'audio':base64.b64encode(bytes(480)).decode()})
                    if event.get('close_socket'):
                        await ws.send_json({'is_final':True});await ws.close();break
            finally:
                server_state['code']=ws.close_code;server_closed.set()
            return ws
        app=web.Application();app.router.add_get('/test',handler)
        runner=web.AppRunner(app);await runner.setup();self.addAsyncCleanup(runner.cleanup)
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        sockets=[];events=asyncio.Queue();guard=Guard()
        async def connector(*_):
            session=_new_owned_session()
            ws=await _connect_websocket(session,f'http://127.0.0.1:{port}/test',{})
            socket=AiohttpRealtimeSocket(ws,owned_session=session);sockets.append(socket);return socket
        stream=ElevenTTDStream(guard,object(),1,lambda _epoch:True,events.put,connector=connector).start()
        stream.feed('Synthetic local voice test.')
        await asyncio.wait_for(received.wait(),2)
        return stream,guard,sockets[0],events,server_state,server_closed

    async def wait_for_active_receive(self,socket):
        async def poll():
            while not socket.ws._waiting:await asyncio.sleep(.001)
        await asyncio.wait_for(poll(),2)

    async def test_natural_finish_confirms_peer_close(self):
        stream,_,socket,events,state,closed=await self.make_stream('normal')
        self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'audio')
        stream.finish_input();await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'done')
        await asyncio.wait_for(closed.wait(),2)
        self.assertEqual((socket.ws.close_code,state['code']),(1000,1000))

    async def test_cancel_with_receive_pending_confirms_close_without_flush(self):
        stream,_,socket,events,state,closed=await self.make_stream('idle')
        await self.wait_for_active_receive(socket);stream.cancel_now()
        await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'cancelled')
        await asyncio.wait_for(closed.wait(),2)
        self.assertEqual((socket.ws.close_code,state['code']),(1000,1000))
        self.assertFalse(any(event.get('close_socket') for event in state['sent']))

    async def test_writer_error_with_receive_pending_preserves_cause_and_confirms_close(self):
        stream,guard,socket,events,state,closed=await self.make_stream('idle')
        await self.wait_for_active_receive(socket)
        guard.assert_live=AsyncMock(side_effect=TenantLinearError('private synthetic denial',403))
        with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING') as logs:
            stream.finish_input();await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_provider_error'))
        await asyncio.wait_for(closed.wait(),2)
        self.assertEqual((socket.ws.close_code,state['code']),(1000,1000))
        self.assertIn('stage=send code=tts_admission_rejected',repr(logs.output))
        self.assertNotIn('private synthetic',repr(logs.output))

    async def test_audio_arriving_during_cleanup_is_never_emitted(self):
        stream,guard,socket,events,state,_=await self.make_stream('idle')
        await self.wait_for_active_receive(socket)
        close_entered=asyncio.Event();close_release=asyncio.Event();original_close=socket.close
        async def delayed_close():
            close_entered.set();await close_release.wait();await original_close()
        socket.close=delayed_close
        guard.assert_live=AsyncMock(side_effect=TenantLinearError('private synthetic denial',403))
        with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING'):
            stream.finish_input();await asyncio.wait_for(close_entered.wait(),2)
            try:
                await state['peer'].send_json({'audio':base64.b64encode(bytes(480)).decode()})
                async def received_pcm():
                    while not stream.audio_bytes:await asyncio.sleep(.001)
                await asyncio.wait_for(received_pcm(),2)
                await asyncio.sleep(.06)
                self.assertTrue(events.empty())
            finally:close_release.set()
            await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_provider_error'))
        self.assertEqual(socket.ws.close_code,1000)

    async def test_pcm_timeout_confirms_peer_close_without_cancelling_active_read(self):
        for mode,timeout_setting in (('idle','FIRST_PCM_TIMEOUT'),('normal','NEXT_PCM_TIMEOUT')):
            with self.subTest(mode=mode):
                with patch('tenant_operator_elevenlabs_voice.'+timeout_setting,.08):
                    with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING') as logs:
                        stream,_,socket,events,state,closed=await self.make_stream(mode)
                        if mode=='normal':
                            self.assertEqual((await asyncio.wait_for(events.get(),2))[0],'audio')
                        await asyncio.wait_for(stream.wait_closed(),3)
                self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_timeout'))
                await asyncio.wait_for(closed.wait(),2)
                self.assertEqual((socket.ws.close_code,state['code']),(1000,1000))
                self.assertIn('stage=receive code=tts_timeout',repr(logs.output))

    async def test_provider_error_uses_allowlisted_code_without_payload(self):
        with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING') as logs:
            stream,_,socket,events,state,closed=await self.make_stream('provider_error')
            await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_provider_error'))
        await asyncio.wait_for(closed.wait(),2)
        # Each side records the peer's status: the provider sent 1008 and
        # aiohttp replied with a normal 1000 acknowledgment.
        self.assertEqual((socket.ws.close_code,state['code']),(1008,1000))
        self.assertIn('stage=receive code=authentication_required',repr(logs.output))
        self.assertIn('provider_error_event=True declared_close_code=1008',repr(logs.output))
        self.assertNotIn('private synthetic',repr(logs.output))

    async def test_unknown_provider_error_is_redacted(self):
        with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING') as logs:
            stream,_,_,events,_,_=await self.make_stream('provider_error',provider_code='private synthetic identifier')
            await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_provider_error'))
        self.assertIn('stage=receive code=tts_provider_error',repr(logs.output))
        self.assertNotIn('private synthetic',repr(logs.output))

    def test_documented_safe_provider_codes_only(self):
        codes=('invalid_api_key','voice_not_found','quota_exceeded','max_character_limit_exceeded')
        with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING') as logs:
            for code in codes:
                _log_tts_failure('receive',_TTDProviderError({'error':code,'message':'private synthetic',
                                                               'param':'private synthetic','code':1008}))
            _log_tts_failure('receive',_TTDProviderError({'error':['private synthetic'],
                                                           'message':'private synthetic','code':True}))
        for code in codes:self.assertIn('code='+code,repr(logs.output))
        self.assertIn('code=tts_provider_error',repr(logs.output))
        self.assertNotIn('private synthetic',repr(logs.output))

    async def test_abrupt_tcp_loss_never_confirms_close(self):
        with self.assertLogs('tenant_operator_elevenlabs_voice',level='WARNING') as logs:
            stream,_,socket,events,_,_=await self.make_stream('abort')
            with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):
                await asyncio.wait_for(stream.wait_closed(),3)
        self.assertEqual(await asyncio.wait_for(events.get(),2),('error',1,'tts_close_unconfirmed'))
        self.assertEqual(socket.ws.close_code,1006)
        self.assertIn('stage=receive',repr(logs.output))


class HybridOpenAILoopbackCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def make_wire(self,*,abort=False):
        stop=asyncio.Event();server_closed=asyncio.Event();server_state={}
        async def handler(request):
            ws=web.WebSocketResponse();await ws.prepare(request)
            try:
                if abort:
                    await stop.wait();request.transport.abort()
                else:
                    async for _ in ws:pass
            finally:
                server_state['code']=ws.close_code;server_closed.set()
            return ws
        app=web.Application();app.router.add_get('/test',handler)
        runner=web.AppRunner(app);await runner.setup();self.addAsyncCleanup(runner.cleanup)
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        session=_new_owned_session()
        ws=await _connect_websocket(session,f'http://127.0.0.1:{port}/test',{})
        socket=AiohttpRealtimeSocket(ws,owned_session=session);socket.synthetic_only=True
        policy=PilotAdmission(Authority(),Lease(),('tenant',7,'channel','dialog'),str(uuid.uuid4()))
        wire=SyntheticHybridRealtimeWire(socket,policy,object(),lambda *_:None);wire.ready=True
        reading=asyncio.create_task(wire.receive())
        async def wait_reader():
            while not ws._waiting:await asyncio.sleep(.001)
        await asyncio.wait_for(wait_reader(),2)
        return wire,ws,reading,stop,server_state,server_closed

    async def test_close_confirms_peer_before_cancelling_openai_receive(self):
        wire,ws,reading,_,state,closed=await self.make_wire()
        try:
            await asyncio.wait_for(wire.close(),3)
            await asyncio.wait_for(closed.wait(),2)
            self.assertEqual((ws.close_code,state['code']),(1000,1000))
        finally:
            if not reading.done():reading.cancel()
            await asyncio.gather(reading,return_exceptions=True)

    async def test_openai_tcp_abort_remains_unconfirmed(self):
        wire,ws,reading,stop,_,closed=await self.make_wire(abort=True)
        class TTDProbe:
            cancelled=False;closed=False
            def cancel_now(self):self.cancelled=True
            async def wait_closed(self):self.closed=True
        ttd=TTDProbe();wire._hybrid_streams.append(ttd)
        try:
            stop.set();await asyncio.wait_for(closed.wait(),2)
            await asyncio.gather(reading,return_exceptions=True)
            with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):
                await asyncio.wait_for(wire.close(),3)
            self.assertEqual(ws.close_code,1006)
            self.assertTrue(ttd.cancelled and ttd.closed)
        finally:
            if not reading.done():reading.cancel()
            await asyncio.gather(reading,return_exceptions=True)

    async def test_unconfirmed_ttd_still_closes_openai_peer(self):
        wire,ws,reading,_,state,closed=await self.make_wire()
        class BrokenTTD:
            def cancel_now(self):pass
            async def wait_closed(self):raise ValueError('tts_close_unconfirmed')
        wire._hybrid_streams.append(BrokenTTD())
        try:
            with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):
                await asyncio.wait_for(wire.close(),3)
            await asyncio.wait_for(closed.wait(),2)
            self.assertEqual((ws.close_code,state['code']),(1000,1000))
        finally:
            if not reading.done():reading.cancel()
            await asyncio.gather(reading,return_exceptions=True)


class HybridBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope=('tenant',7,'channel','dialog');self.session_id=str(uuid.uuid4())
        self.policy=PilotAdmission(Authority(),Lease(),self.scope,self.session_id);self.policy._fence=1
        self.openai=OpenAISocket();self.ttd_sockets=[]
        async def connector(*_):
            socket=TTDSocket();self.ttd_sockets.append(socket);return socket
        self.connector=connector
        def stream_factory(*args):return ElevenTTDStream(*args,connector=self.connector)
        self.wire=SyntheticHybridRealtimeWire(self.openai,self.policy,object(),stream_factory)
        await self.wire.open({'existing_copilot_instructions':'Never send messages','lines':[],'extra_context':{}},
                             tools=[],create_response=False,interrupt_response=True,transcription=None,reconnect=False,duplex=True)

    async def asyncTearDown(self):await self.wire.close()

    async def test_read_only_function_call_bypasses_tts_until_spoken_followup(self):
        await self.wire.close()
        self.openai=OpenAISocket()
        def stream_factory(*args):return ElevenTTDStream(*args,connector=self.connector)
        self.wire=SyntheticHybridRealtimeWire(self.openai,self.policy,object(),stream_factory)
        await self.wire.open({'existing_copilot_instructions':'Never send messages','lines':[],'extra_context':{}},
            tools=realtime_definitions(),create_response=False,interrupt_response=True,transcription=None,
            reconnect=False,duplex=True)
        await self.arm()
        await self.openai.incoming.put({'type':'response.created','response':{'id':'response1'}})
        self.assertEqual((await self.wire.receive())['type'],'response.created')
        call={'id':'function1','type':'function_call','call_id':'call1','name':'search_tasks','arguments':''}
        await self.openai.incoming.put({'type':'response.output_item.added','response_id':'response1',
                                        'output_index':0,'item':call})
        self.assertEqual((await self.wire.receive())['type'],'response.output_item.added')
        await self.openai.incoming.put({'type':'response.function_call_arguments.delta','response_id':'response1',
            'item_id':'function1','call_id':'call1','delta':'{"query":"invoice"}'})
        self.assertEqual((await self.wire.receive())['type'],'response.function_call_arguments.delta')
        arguments='{"query":"invoice"}'
        await self.openai.incoming.put({'type':'response.function_call_arguments.done','response_id':'response1',
            'item_id':'function1','call_id':'call1','arguments':arguments})
        self.assertEqual((await self.wire.receive())['type'],'response.function_call_arguments.done')
        call={**call,'arguments':arguments}
        await self.openai.incoming.put({'type':'response.output_item.done','response_id':'response1',
                                        'output_index':0,'item':call})
        self.assertEqual((await self.wire.receive())['type'],'response.output_item.done')
        await self.openai.incoming.put({'type':'response.done','response':{'id':'response1','status':'completed',
                                                                            'output':[call]}})
        event=await asyncio.wait_for(self.wire.receive(),2)
        self.assertEqual(event['type'],'response.done')
        self.assertIsNone(self.wire._hybrid_active)
        self.assertEqual(self.ttd_sockets,[])
        await self.wire.send_function_call_output('call1','{"read_only":true}')

    async def arm(self):
        request_id=str(uuid.uuid4());scope={'request_id':request_id,'tenant_id':'tenant','actor_id':7,
            'channel_id':'channel','conversation_id':'dialog','session_id':self.session_id,'response_number':1,'executor':'test'}
        receipt=await self.policy.issue(self.scope,request_id);await self.wire.bind_pilot_claim(scope,receipt)
        refs=[{'type':'item_reference','id':self.wire.context_id},{'type':'item_reference','id':'turn1'}]
        token=await self.wire.prepare_guarded_response({'type':'response.create','response':{'input':refs}},lambda:True)
        self.assertTrue(await self.wire.send_prepared_response(token,lambda:True))

    async def start_response(self):
        await self.arm()
        await self.openai.incoming.put({'type':'response.created','response':{'id':'response1'}})
        self.assertEqual((await self.wire.receive())['type'],'response.created')
        await self.openai.incoming.put({'type':'response.output_item.added','response_id':'response1','output_index':0,
                                        'item':{'type':'message','role':'assistant','id':'item1'}})
        self.assertEqual((await self.wire.receive())['type'],'response.output_item.added')

    def text_event(self,kind,**extra):
        return {'type':kind,'response_id':'response1','item_id':'item1','output_index':0,'content_index':0,**extra}

    async def assert_phrase(self,phrase):
        await self.start_response()
        await self.openai.incoming.put(self.text_event('response.output_text.delta',delta=phrase))
        audio=await asyncio.wait_for(self.wire.receive(),2)
        self.assertEqual(audio['type'],'response.output_audio.delta')
        self.assertEqual(base64.b64decode(audio['delta']),bytes(480))
        await self.openai.incoming.put(self.text_event('response.output_text.done',text=phrase))
        await self.openai.incoming.put({'type':'response.output_item.done','response_id':'response1','output_index':0,
            'item':{'type':'message','role':'assistant','id':'item1','status':'completed',
                    'content':[{'type':'output_text','text':phrase}]}})
        await self.openai.incoming.put({'type':'response.done','response':{'id':'response1','status':'completed',
            'output':[{'type':'message','role':'assistant','id':'item1'}]}})
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.output_audio.done')
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.done')
        self.assertEqual(list(self.policy._permits.values()),[None])

    async def test_russian_text_streams_pcm_before_done(self):
        await self.assert_phrase('Привет, как дела?')

    async def test_azerbaijani_text_streams_pcm_before_done(self):
        await self.assert_phrase('Salam, məni eşidirsən?')

    async def test_unsolicited_response_never_opens_ttd(self):
        await self.openai.incoming.put({'type':'response.created','response':{'id':'unapproved'}})
        with self.assertRaisesRegex(ValueError,'hybrid_unsolicited_response'):await self.wire.receive()
        self.assertEqual(self.ttd_sockets,[])

    async def test_final_text_mismatch_is_rejected(self):
        await self.start_response()
        await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='Hello, synthetic voice!'))
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.output_audio.delta')
        await self.openai.incoming.put(self.text_event('response.output_text.done',text='A different response'))
        with self.assertRaisesRegex(ValueError,'hybrid_text_mismatch'):
            await asyncio.wait_for(self.wire.receive(),2)

    async def test_completed_item_text_mismatch_is_rejected(self):
        await self.start_response()
        phrase='Hello, synthetic voice!'
        await self.openai.incoming.put(self.text_event('response.output_text.delta',delta=phrase))
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.output_audio.delta')
        await self.openai.incoming.put(self.text_event('response.output_text.done',text=phrase))
        await self.openai.incoming.put({'type':'response.output_item.done','response_id':'response1','output_index':0,
            'item':{'type':'message','role':'assistant','id':'item1','status':'completed',
                    'content':[{'type':'output_text','text':'A different response'}]}})
        with self.assertRaisesRegex(ValueError,'hybrid_text_mismatch'):
            await asyncio.wait_for(self.wire.receive(),2)

    async def test_unexpected_openai_audio_is_rejected(self):
        await self.start_response()
        await self.openai.incoming.put({'type':'response.output_audio.delta','response_id':'response1',
                                       'item_id':'item1','content_index':0,'delta':'AAA='})
        with self.assertRaisesRegex(ValueError,'hybrid_event_forbidden'):
            await asyncio.wait_for(self.wire.receive(),2)
        self.assertEqual(self.ttd_sockets,[])

    async def test_vad_before_response_created_suppresses_late_completed_reply(self):
        await self.arm()
        await self.openai.incoming.put({'type':'input_audio_buffer.speech_started'})
        self.assertEqual((await self.wire.receive())['type'],'input_audio_buffer.speech_started')
        await self.openai.incoming.put({'type':'response.created','response':{'id':'response1'}})
        self.assertEqual((await self.wire.receive())['type'],'response.created')
        await self.openai.incoming.put({'type':'response.output_item.added','response_id':'response1','output_index':0,
                                        'item':{'type':'message','role':'assistant','id':'item1'}})
        self.assertEqual((await self.wire.receive())['type'],'response.output_item.added')
        await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='Late synthetic text'))
        await self.openai.incoming.put(self.text_event('response.output_text.done',text='Late synthetic text'))
        await self.openai.incoming.put({'type':'response.done','response':{'id':'response1','status':'completed',
            'output':[{'type':'message','role':'assistant','id':'item1'}]}})
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.done')
        self.assertEqual(self.ttd_sockets,[])

    async def test_interruption_discards_late_pcm_and_delete_ack_maps_to_truncation(self):
        await self.start_response();await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='Hello, synthetic voice!'))
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.output_audio.delta')
        await self.openai.incoming.put({'type':'input_audio_buffer.speech_started'})
        self.assertEqual((await self.wire.receive())['type'],'input_audio_buffer.speech_started')
        await self.wire.send({'type':'response.cancel','response_id':'response1'})
        await self.wire.send({'type':'conversation.item.truncate','item_id':'item1','content_index':0,'audio_end_ms':0})
        self.assertEqual(self.openai.sent[-1],{'type':'conversation.item.delete','item_id':'item1'})
        await self.openai.incoming.put({'type':'conversation.item.deleted','item_id':'item1'})
        self.assertEqual(await self.wire.receive(),{'type':'conversation.item.truncated','item_id':'item1','content_index':0,'audio_end_ms':0})
        await self.wire._hybrid_events.put(('audio',1,base64.b64encode(bytes(2)).decode()))
        await self.openai.incoming.put({'type':'response.done','response':{'id':'response1','status':'cancelled','output':[]}})
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'response.done')
        self.assertFalse(any(event.get('close_socket') for event in self.ttd_sockets[0].sent))

    async def test_speech_started_preempts_ready_audio_with_pending_reader(self):
        await self.start_response()
        epoch=self.wire._hybrid_active['epoch'];encoded=base64.b64encode(bytes(480)).decode()
        first=asyncio.create_task(self.wire.receive())
        await self.wire._hybrid_events.put(('audio',epoch,encoded))
        self.assertEqual((await asyncio.wait_for(first,2))['type'],'response.output_audio.delta')
        self.assertIsNotNone(self.wire._hybrid_openai_task)
        self.assertFalse(self.wire._hybrid_openai_task.done())
        stale={'type':'response.output_audio.delta','response_id':'response1','item_id':'item1',
               'content_index':0,'delta':encoded}
        terminal={'type':'response.done','response':{'id':'response1','status':'cancelled','output':[]}}
        self.wire._hybrid_ready.extend([stale.copy(),stale.copy(),terminal])
        await self.openai.incoming.put({'type':'input_audio_buffer.speech_started'})
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'input_audio_buffer.speech_started')
        self.assertEqual(list(self.wire._hybrid_ready),[terminal])

    async def test_speech_started_preempts_ready_audio_before_reader_exists(self):
        await self.start_response()
        self.assertIsNone(self.wire._hybrid_openai_task)
        self.wire._hybrid_ready.append({'type':'response.output_audio.delta','response_id':'response1',
                                        'item_id':'item1','content_index':0,'delta':'AAA='})
        await self.openai.incoming.put({'type':'input_audio_buffer.speech_started'})
        self.assertEqual((await asyncio.wait_for(self.wire.receive(),2))['type'],'input_audio_buffer.speech_started')
        self.assertFalse(self.wire._hybrid_ready)

    async def test_ready_audio_is_not_held_by_idle_openai_reader(self):
        await self.start_response()
        audio={'type':'response.output_audio.delta','response_id':'response1',
               'item_id':'item1','content_index':0,'delta':'AAA='}
        self.wire._hybrid_ready.append(audio)
        self.assertEqual(await asyncio.wait_for(self.wire.receive(),.25),audio)

    async def test_ready_audio_is_not_starved_by_queued_openai_text(self):
        await self.start_response()
        audio={'type':'response.output_audio.delta','response_id':'response1',
               'item_id':'item1','content_index':0,'delta':'AAA='}
        self.wire._hybrid_ready.append(audio)
        for _ in range(50):
            await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='a'))
        self.assertEqual(await asyncio.wait_for(self.wire.receive(),.25),audio)
        self.assertFalse(self.wire._hybrid_active['text_done'])
        self.assertGreater(self.openai.incoming.qsize(),0)

    async def test_unconfirmed_ttd_close_still_closes_openai_and_fails(self):
        async def connector(*_):
            socket=TTDSocket(close_fails=True);self.ttd_sockets.append(socket);return socket
        self.connector=connector
        await self.start_response();await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='Hello, synthetic voice!'))
        await asyncio.wait_for(self.wire.receive(),2)
        with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):await self.wire.close()
        self.assertTrue(self.openai.closed)
        # close is idempotently failed; avoid a second teardown exception.
        self.wire.close=lambda:asyncio.sleep(0)

    async def test_failed_reader_does_not_override_confirmed_transport_close(self):
        await self.start_response()
        await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='Hello, synthetic voice!'))
        await asyncio.wait_for(self.wire.receive(),2)
        await self.openai.incoming.put(None)
        with self.assertRaisesRegex(ValueError,'provider_event_invalid'):
            await asyncio.wait_for(self.wire.receive(),2)
        await asyncio.wait_for(self.wire.close(),2)
        self.assertTrue(self.openai.closed)
        self.assertTrue(self.ttd_sockets[0].closed)

    async def test_cancelled_ttd_wait_is_unconfirmed_even_when_openai_closes(self):
        class CancelledWaitStream:
            def cancel_now(self):pass
            async def wait_closed(self):raise asyncio.CancelledError()
        self.wire._hybrid_streams.append(CancelledWaitStream())
        with self.assertRaisesRegex(ValueError,'tts_close_unconfirmed'):
            await asyncio.wait_for(self.wire.close(),2)
        self.assertTrue(self.openai.closed)
        self.wire.close=lambda:asyncio.sleep(0)

    async def test_session_never_releases_lease_when_cancel_races_unconfirmed_ttd_close(self):
        async def connector(*_):
            socket=SlowCloseTTDSocket(close_fails=True);self.ttd_sockets.append(socket);return socket
        self.connector=connector
        await self.start_response();await self.openai.incoming.put(self.text_event('response.output_text.delta',delta='Hello, synthetic voice!'))
        await asyncio.wait_for(self.wire.receive(),2)
        stream=self.wire._hybrid_active['stream'];stream.finish_input();socket=self.ttd_sockets[0]
        await asyncio.wait_for(socket.close_entered.wait(),2);stream.cancel_now()
        session=object.__new__(Session)
        session.current_reservation=None;session.pilot_policy=None;session.history=None;session.transcript=None
        session.provider=self.wire;session.lease=type('Lease',(),{'release':AsyncMock(return_value=True)})()
        session.scope=self.scope;session.fence=1;session.shutdown_timeout=1
        session.client=type('Client',(),{'send':AsyncMock()})()
        for name in ('io_lock','receive_lock','provider_event_lock','client_event_lock','client_lock'):
            setattr(session,name,asyncio.Lock())
        ending=asyncio.create_task(session._finish_end('user_end'));await asyncio.sleep(.01)
        socket.close_release.set();self.assertFalse(await ending)
        session.lease.release.assert_not_awaited();self.assertTrue(self.openai.closed)
        self.wire.close=lambda:asyncio.sleep(0)


if __name__=='__main__':unittest.main()
