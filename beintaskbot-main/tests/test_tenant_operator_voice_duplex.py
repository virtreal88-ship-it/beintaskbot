"""Local event-order tests; these do not claim actual speech understanding."""
import base64
import json
import time
import unittest
import uuid
from unittest.mock import AsyncMock
import aiohttp

from tenant_operator_voice_session import Session, Refused
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_realtime_wire import SyntheticRealtimeWire, AiohttpRealtimeSocket, SafeRealtimeFailure
from tests.test_tenant_operator_realtime_wire import Socket


class Authority:
    async def check(self,*_):pass


class Lease:
    valid=True
    async def claim(self,_):return 1
    async def confirm_provider_open(self,*_):return True
    async def release(self,*_):return True
    async def assert_current(self,*_):
        if not self.valid:raise Refused('lease_expired')


class Provider:
    mock_only=True
    def __init__(self):self.events=[]
    async def bind_claim(self,*_):pass
    async def send(self,event):self.events.append(event)


class Ledger:
    async def reserve(self,_):return {}
    async def claim(self,_):return {}
    async def mark_unknown(self,_):return {'state':'unknown'}


class Context:
    async def revalidate(self,_):return True
    async def read_authorized(self,_):
        return {'existing_copilot_instructions':'Never send messages','lines':[],'extra_context':{}}


class Client:
    def __init__(self):self.events=[]
    async def send(self,event):self.events.append(event)


class DuplexIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider=Provider();self.lease=Lease();self.client=Client()
        self.s=Session(('tenant',7,'channel','dialog'),authority=Authority(),ledger=Ledger(),
                       lease=self.lease,context=Context(),provider=self.provider,client=self.client,duplex=True)
        self.s.closed=False;self.s.started=time.monotonic();self.s.fence=1
        self.s._check_receipt=lambda *_:None

    async def commit(self,session,item_id):
        await session.receive_provider({'type':'conversation.item.added',
                                        'item':{'id':item_id,'type':'message','role':'user'}})
        await session.receive_provider({'type':'input_audio_buffer.committed','item_id':item_id})

    async def test_external_tts_interruption_excludes_entire_unaligned_text_item(self):
        s=self.s
        self.provider.text_output=True
        s.item='spoken_item';s.produced=48000
        s.response_inflight=True;s.provider_response_id='reply'
        s.assistant_response_ids['spoken_item']='reply'
        s._remember_context_item('spoken_item',usable=False,role='assistant')
        await s.receive_client({'type':'stop_response','item_id':'spoken_item','audio_end_ms':500})
        self.assertEqual(s.truncate_pending_audio_end_ms,0)
        self.assertEqual(self.provider.events[-1],{'type':'conversation.item.truncate',
                         'item_id':'spoken_item','content_index':0,'audio_end_ms':0})
        await s.receive_provider({'type':'conversation.item.truncated','item_id':'spoken_item',
                                   'content_index':0,'audio_end_ms':0})
        self.assertNotIn('spoken_item',s._context_usable)

    async def test_pcm_before_vad_then_interrupt_cancel_truncate_commit_and_dispatch(self):
        s=self.s
        await s.receive_client({'type':'audio','pcm':bytes(480)})
        self.assertEqual(self.provider.events[-1]['type'],'input_audio_buffer.append')
        await s.receive_provider({'type':'input_audio_buffer.speech_started'})
        await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.commit(s,'input-1')
        self.assertEqual(sum(e['type']=='response.create' for e in self.provider.events),1)
        await s.receive_provider({'type':'response.created','response':{'id':'response-1'}})
        await s.receive_provider({'type':'response.output_item.added','response_id':'response-1',
                                  'item':{'id':'output-1','type':'message','role':'assistant'}})
        await s.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
                                  'item_id':'output-1','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        await s.receive_client({'type':'audio','pcm':bytes(480)})
        await s.receive_provider({'type':'input_audio_buffer.speech_started'})
        self.assertEqual(sum(e['type']=='response.cancel' for e in self.provider.events),1)
        self.assertEqual(self.client.events[-1],{'type':'interrupt','item_id':'output-1'})
        self.assertEqual(self.provider.events[-1]['type'],'conversation.item.truncate')
        await s.receive_client({'type':'stop_response','item_id':'output-1','audio_end_ms':0})
        self.assertEqual(sum(e['type']=='response.cancel' for e in self.provider.events),1,
                         'an overlapping explicit stop cannot send a second cancellation')
        count=len(self.client.events)
        await s.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
                                  'item_id':'output-1','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        self.assertEqual(len(self.client.events),count,'late audio is discarded while truncation is pending')
        await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await self.commit(s,'input-2')
        await s.receive_provider({'type':'response.done','response':{'id':'response-1','output':[]}})
        self.assertEqual(sum(e['type']=='response.create' for e in self.provider.events),1)
        await s.receive_provider({'type':'conversation.item.truncated','item_id':'output-1','content_index':0,'audio_end_ms':0})
        self.assertEqual(sum(e['type']=='response.create' for e in self.provider.events),2)

    async def test_expired_lease_blocks_continuous_pcm_before_forwarding(self):
        self.lease.valid=False
        with self.assertRaises(Refused):await self.s._receive_client({'type':'audio','pcm':bytes(480)})
        self.assertEqual(self.provider.events,[])

    async def test_combined_pcm_still_requires_current_authority_and_lease(self):
        self.lease.valid=False
        with self.assertRaisesRegex(Refused,'lease_expired'):
            await self.s._receive_client({'type':'audio','pcm':bytes(8192)})
        self.assertEqual(self.provider.events,[])
        self.lease.valid=True
        self.s.authority.check=AsyncMock(side_effect=Refused('permission_revoked'))
        with self.assertRaisesRegex(Refused,'permission_revoked'):
            await self.s._receive_client({'type':'audio','pcm':bytes(8192)})
        self.assertEqual(self.provider.events,[])

    async def test_interrupt_completion_order_discards_old_audio_and_late_ack(self):
        for done_first in (False,True):
            with self.subTest(done_first=done_first):
                self.setUp();s=self.s
                await s.receive_client({'type':'audio','pcm':bytes(480)})
                await s.receive_provider({'type':'input_audio_buffer.speech_started'})
                await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
                await self.commit(s,'input-1')
                await s.receive_provider({'type':'response.created','response':{'id':'response-1'}})
                audio={'type':'response.output_audio.delta','response_id':'response-1','item_id':'output-1',
                       'content_index':0,'delta':base64.b64encode(bytes(480)).decode()}
                await s.receive_provider(audio)
                await s.receive_provider({'type':'input_audio_buffer.speech_started'})
                await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
                await self.commit(s,'input-2')
                done={'type':'response.done','response':{'id':'response-1','output':[]}}
                truncated={'type':'conversation.item.truncated','item_id':'output-1','content_index':0,'audio_end_ms':0}
                for event in ([done,truncated] if done_first else [truncated,done]):
                    await s.receive_provider(event)
                    count=len(self.client.events)
                    await s.receive_provider(audio)
                    self.assertEqual(len(self.client.events),count)
                await s.receive_client({'type':'playback_scheduled','item_id':'output-1','context_state':'running'})
                self.assertFalse(s.closed)
                self.assertEqual(sum(e['type']=='response.create' for e in self.provider.events),2)
                await s.receive_provider({'type':'response.created','response':{'id':'response-2'}})
                await s.receive_provider({**audio,'response_id':'response-2','item_id':'output-2'})
                await s.receive_client({'type':'playback_scheduled','item_id':'output-1','context_state':'running'})
                self.assertEqual(s.item,'output-2')
                self.assertFalse(s.closed)

    async def test_continuous_pcm_still_obeys_the_audio_limit(self):
        self.s.max_input_bytes=4
        await self.s._receive_client({'type':'audio','pcm':bytes(4)})
        with self.assertRaisesRegex(Refused,'session_input_limit'):
            await self.s._receive_client({'type':'audio','pcm':bytes(2)})
        self.assertEqual(len(self.provider.events),1)

    async def test_cancel_waits_for_exact_response_id_when_speech_starts_during_thinking(self):
        s=self.s;s.response_inflight=True
        await s.receive_client({'type':'audio','pcm':bytes(480)})
        await s.receive_provider({'type':'input_audio_buffer.speech_started'})
        self.assertFalse(any(e['type']=='response.cancel' for e in self.provider.events))
        await s.receive_provider({'type':'response.created','response':{'id':'response-1'}})
        self.assertEqual([e for e in self.provider.events if e['type']=='response.cancel'],
                         [{'type':'response.cancel','response_id':'response-1'}])
        count=len(self.client.events)
        await s.receive_provider({'type':'response.output_audio.delta','response_id':'response-1',
                                  'item_id':'output-1','content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
        self.assertEqual(len(self.client.events),count)

    async def test_error_parser_keeps_only_cancel_correlation_and_redacts_details(self):
        class WS:
            def __init__(self,event):self.event=event
            async def receive(self):return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT,json.dumps(self.event),'')
        adapter=AiohttpRealtimeSocket(WS({'type':'error','error':{'code':'response_cancel_not_active',
                                         'event_id':'cancel-1','message':'secret error body'}}))
        self.assertEqual(await adapter.receive_json(),{'type':'_cancel_not_active','event_id':'cancel-1'})
        adapter=AiohttpRealtimeSocket(WS({'type':'error','error':{'code':'response_cancel_not_active',
                                         'message':'secret error body'}}))
        with self.assertRaises(SafeRealtimeFailure) as caught:await adapter.receive_json()
        self.assertNotIn('secret',str(caught.exception))

    async def test_pilot_response_count_and_total_audio_are_bounded(self):
        s=self.s;s.pilot_policy=object();s.pending_input_item_id='input';s.response_pending=True
        s.response_number=s.MAX_PILOT_RESPONSES
        with self.assertRaisesRegex(Refused,'session_response_limit'):await s._dispatch_pending()
        self.assertFalse(any(e['type']=='response.create' for e in self.provider.events))
        s.response_inflight=True;s.provider_response_id='response';s.MAX_PILOT_OUTPUT_BYTES=2;s.pilot_output_bytes=2
        with self.assertRaisesRegex(Refused,'session_output_limit'):
            await s._receive_provider({'type':'response.output_audio.delta','response_id':'response',
                  'item_id':'item','content_index':0,'delta':base64.b64encode(bytes(2)).decode()})
        self.assertEqual(self.client.events,[])

    async def test_latency_records_both_provider_event_orders_without_ids(self):
        for commit_first in (False,True):
            self.setUp();s=self.s;s._dispatch_pending=AsyncMock()
            await s.receive_client({'type':'audio','pcm':bytes(480)})
            await s.receive_provider({'type':'input_audio_buffer.speech_started'})
            stop={'type':'input_audio_buffer.speech_stopped'}
            commit={'type':'input_audio_buffer.committed','item_id':'private-input'}
            await s.receive_provider({'type':'conversation.item.added',
                                      'item':{'id':'private-input','type':'message','role':'user'}})
            with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
                for event in ([commit,stop] if commit_first else [stop,commit]):
                    await s.receive_provider(event)
            stage='commit_ack_to_vad_stop' if commit_first else 'vad_stop_to_commit_ack'
            self.assertTrue(any('stage='+stage in line for line in logs.output))
            self.assertNotIn('private-input',str(logs.output))

    async def test_pilot_admission_and_wire_gate_a_realistic_vad_turn(self):
        scope=('tenant',7,'channel','dialog');authority=Authority();lease=Lease();client=Client()
        session_id=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,session_id)
        socket=Socket();provider=SyntheticRealtimeWire(socket,policy)
        session=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),
                        provider=provider,client=client,pilot_policy=policy,session_id=session_id,
                        max_seconds=600,duplex=True)
        try:
            await session.start()
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            self.assertEqual(socket.sent[-1]['type'],'input_audio_buffer.append')
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            self.assertFalse(any(e['type']=='response.create' for e in socket.sent))
            await self.commit(session,'input-1')
            self.assertEqual(sum(e['type']=='response.create' for e in socket.sent),1)
            with self.assertRaisesRegex(ValueError,'claim_missing'):
                await provider.send({'type':'response.create'})
        finally:
            await session.end('test')


if __name__=='__main__':unittest.main()
