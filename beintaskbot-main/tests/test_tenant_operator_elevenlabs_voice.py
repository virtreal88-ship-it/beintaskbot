"""Synthetic OpenAI/ElevenLabs bridge tests; no network or real speech."""
import asyncio
import base64
import copy
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from tenant_operator_elevenlabs_voice import ElevenTTDStream, VOICE_ID, _credential_valid
from tenant_operator_realtime_wire import SyntheticHybridRealtimeWire
from tenant_operator_voice_session import Session
from tenant_operator_voice_pilot import PilotAdmission


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
