import asyncio
import copy
import unittest
import uuid
from unittest.mock import patch
from tenant_linear_policy import TenantLinearError
from tenant_operator_realtime_wire import SyntheticRealtimeWire,create_production_provider,MODEL,QUOTE,MINIMUM_HOLD
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_voice_session import Session


class Socket:
    synthetic_only=True
    def __init__(self,mutate=None):self.sent=[];self.mutate=mutate;self.closed=False
    async def send_json(self,event):self.sent.append(copy.deepcopy(event))
    async def receive_json(self):
        if not self.sent:return {'type':'session.created','session':{'id':'synthetic-session'}}
        if self.sent[-1]['type']=='conversation.item.create':
            return {'type':'conversation.item.added','item':copy.deepcopy(self.sent[-1]['item'])}
        config=copy.deepcopy(self.sent[-1]['session'])
        if self.mutate:self.mutate(config)
        return {'type':'session.updated','session':{**config,'id':'synthetic-session'}}
    async def close(self):self.closed=True


class Guard:
    def __init__(self):self.claims=set();self.deny=False
    async def consume(self,scope,receipt):
        if self.deny or scope['request_id'] in self.claims:return False
        self.claims.add(scope['request_id']);return True


class WireTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket=Socket();self.guard=Guard();self.wire=SyntheticRealtimeWire(self.socket,self.guard)
        self.snapshot={'existing_copilot_instructions':'Never send messages, call tools or perform external actions.',
                       'lines':[],'extra_context':{}}
        self.options={'tools':[],'create_response':False,'interrupt_response':False,'transcription':None,'reconnect':False}

    async def open(self):await self.wire.open(self.snapshot,**self.options)

    def scope(self):
        return {'request_id':str(uuid.uuid4()),'tenant_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4()),
                'channel_id':str(uuid.uuid4()),'conversation_id':'synthetic','actor_id':7,'response_number':1,'executor':'test'}

    def receipt(self,scope):return {**scope,'state':'claimed','model':MODEL,'quote_version':QUOTE,'reserved_microusd':MINIMUM_HOLD,
                                    'tenant_reservation_id':str(uuid.uuid4())}

    async def test_effective_policy_must_be_read_back_before_audio(self):
        with self.assertRaises(ValueError):await self.wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
        await self.open();self.assertEqual(self.socket.sent[0]['type'],'session.update')
        input_audio=self.socket.sent[0]['session']['audio']['input']
        self.assertEqual(input_audio['noise_reduction'],{'type':'near_field','strength':1})
        self.assertIsNone(input_audio['turn_detection'])
        await self.wire.send({'type':'input_audio_buffer.append','audio':'AAA='});self.assertEqual(self.socket.sent[-1]['type'],'input_audio_buffer.append')

    async def test_duplex_vad_cannot_create_or_cancel_responses_without_server(self):
        await self.wire.open(self.snapshot,**self.options,duplex=True)
        vad=self.socket.sent[0]['session']['audio']['input']['turn_detection']
        self.assertEqual(vad,{'type':'server_vad','threshold':0.5,'prefix_padding_ms':300,
                              'silence_duration_ms':500,'create_response':False,'interrupt_response':False})
        await self.wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
        with self.assertRaisesRegex(ValueError,'claim_missing'):await self.wire.send({'type':'response.create'})
        for field in ('create_response','interrupt_response'):
            wire=SyntheticRealtimeWire(Socket(lambda s:s['audio']['input']['turn_detection'].__setitem__(field,True)),Guard())
            with self.assertRaisesRegex(ValueError,'unsafe_session_readback'):
                await wire.open(self.snapshot,**self.options,duplex=True)

    async def test_cancel_race_is_ignored_only_after_exact_completed_response(self):
        class QueueSocket(Socket):
            def __init__(self):super().__init__();self.events=[]
            async def receive_json(self):
                if not self.events:await asyncio.Event().wait()
                return self.events.pop(0)
        socket=QueueSocket();wire=SyntheticRealtimeWire(socket,Guard());wire.ready=True;wire.duplex=True
        await wire.send({'type':'response.cancel','response_id':'response-a'})
        event_id=socket.sent[-1]['event_id']
        socket.events=[{'type':'response.done','response':{'id':'response-a'}},
                       {'type':'_cancel_not_active','event_id':event_id}]
        self.assertEqual((await wire.receive())['type'],'response.done')
        self.assertEqual(await wire.receive(),{'type':'response_cancel_noop'})
        socket.events=[{'type':'_cancel_not_active','event_id':event_id}]
        with self.assertRaisesRegex(ValueError,'provider_error'):await wire.receive()
        await wire.send({'type':'response.cancel','response_id':'response-b'})
        event_id=socket.sent[-1]['event_id']
        socket.events=[{'type':'_cancel_not_active','event_id':event_id},
                       {'type':'rate_limits.updated'}, {'type':'response.done','response':{'id':'response-b'}}]
        self.assertEqual((await wire.receive())['type'],'rate_limits.updated')
        self.assertEqual((await wire.receive())['type'],'response.done')
        self.assertFalse(wire._pending_cancel_races)
        await wire.send({'type':'response.cancel','response_id':'response-c'})
        event_id=socket.sent[-1]['event_id']
        socket.events=[{'type':'_cancel_not_active','event_id':event_id}]
        with patch('tenant_operator_realtime_wire.CANCEL_RACE_DEADLINE',0.01):
            with self.assertRaisesRegex(ValueError,'provider_error'):await wire.receive()
        socket=QueueSocket();wire=SyntheticRealtimeWire(socket,Guard());wire.ready=True;wire.duplex=True
        socket.events=[{'type':'_cancel_not_active','event_id':'unrelated'}]
        with self.assertRaisesRegex(ValueError,'provider_error'):await wire.receive()

    async def test_cancel_error_requires_done_for_the_matching_response(self):
        class QueueSocket(Socket):
            def __init__(self):super().__init__();self.events=[]
            async def receive_json(self):
                if not self.events:await asyncio.Event().wait()
                return self.events.pop(0)
        socket=QueueSocket();wire=SyntheticRealtimeWire(socket,Guard());wire.ready=True;wire.duplex=True
        await wire.send({'type':'response.cancel','response_id':'expected'})
        event_id=socket.sent[-1]['event_id']
        socket.events=[{'type':'_cancel_not_active','event_id':event_id},
                       {'type':'response.done','response':{'id':'different'}}]
        with patch('tenant_operator_realtime_wire.CANCEL_RACE_DEADLINE',0.01):
            self.assertEqual((await wire.receive())['response']['id'],'different')
            with self.assertRaisesRegex(ValueError,'provider_error'):await wire.receive()
        wire=SyntheticRealtimeWire(Socket(lambda s:s['audio']['input']['turn_detection'].__setitem__('idle_timeout_ms',None)),Guard())
        await wire.open(self.snapshot,**self.options,duplex=True)
        for key,value in [('idle_timeout_ms',1000),('unknown_option',True),('prefix_padding_ms',True)]:
            wire=SyntheticRealtimeWire(Socket(lambda s:s['audio']['input']['turn_detection'].__setitem__(key,value)),Guard())
            with self.assertRaisesRegex(ValueError,'unsafe_session_readback'):
                await wire.open(self.snapshot,**self.options,duplex=True)

    async def test_truncate_accepts_bounded_playback_offset_for_click_interruption(self):
        await self.open()
        await self.wire.send({'type':'conversation.item.truncate','item_id':'assistant-item',
                              'content_index':0,'audio_end_ms':125})
        self.assertEqual(self.socket.sent[-1]['audio_end_ms'],125)
        for offset in [-1,100_001,True,1.5]:
            with self.assertRaisesRegex(ValueError,'truncate_invalid'):
                await self.wire.send({'type':'conversation.item.truncate','item_id':'assistant-item',
                                      'content_index':0,'audio_end_ms':offset})

    async def test_mismatched_policy_closes_admission(self):
        changes=[lambda s:s['audio']['input'].__setitem__('turn_detection',{'type':'server_vad','create_response':True}),
                 lambda s:s['audio']['input'].__setitem__('turn_detection',{'type':'server_vad','interrupt_response':False}),
                 lambda s:s['audio']['input'].__setitem__('turn_detection',{}),
                 lambda s:s['audio']['input'].__setitem__('turn_detection','server_vad'),
                 lambda s:s['audio']['input']['noise_reduction'].__setitem__('type','far_field'),
                 lambda s:s['audio']['input']['noise_reduction'].__setitem__('strength',0.5),
                 lambda s:s['audio']['input']['noise_reduction'].__setitem__('future_option',True),
                 lambda s:s['audio']['input'].__setitem__('transcription',{'model':'unapproved'}),
                 lambda s:s.__setitem__('tools',[{'name':'send'}]),lambda s:s.__setitem__('max_output_tokens',999999),
                 lambda s:s.__setitem__('max_output_tokens',32000),
                 lambda s:s.__setitem__('model','other')]
        for change in changes:
            wire=SyntheticRealtimeWire(Socket(change),Guard())
            with self.assertRaisesRegex(ValueError,'unsafe_session_readback'):await wire.open(self.snapshot,**self.options)
            with self.assertRaises(ValueError):await wire.send({'type':'response.create'})

    async def test_provider_null_transcription_readback_is_disabled(self):
        wire=SyntheticRealtimeWire(Socket(lambda s:s['audio']['input'].__setitem__('transcription',None)),Guard())
        await wire.open(self.snapshot,**self.options)
        self.assertTrue(wire.ready)

    async def test_one_committed_scoped_claim_per_response(self):
        await self.open()
        with self.assertRaisesRegex(ValueError,'claim_missing'):await self.wire.send({'type':'response.create'})
        scope=self.scope();receipt=self.receipt(scope);await self.wire.bind_claim(scope,receipt)
        await self.wire.send({'type':'response.create'})
        with self.assertRaisesRegex(ValueError,'claim_reused'):await self.wire.bind_claim(scope,receipt)
        with self.assertRaisesRegex(ValueError,'claim_missing'):await self.wire.send({'type':'response.create'})

    async def test_pilot_permit_is_consumed_once_without_financial_receipt(self):
        class Authority:
            async def check(self,scope,stage):return True
        class Lease:
            async def claim(self,scope):return 1
            async def assert_current(self,scope,fence):return True
            async def confirm_provider_open(self,scope,fence):return True
            async def release(self,scope,fence):return True
        scope=self.scope()
        session_scope=(scope['tenant_id'],scope['actor_id'],scope['channel_id'],scope['conversation_id'])
        policy=PilotAdmission(Authority(),Lease(),session_scope,scope['session_id'])
        await policy.admit(session_scope,scope['session_id'],1)
        wire=SyntheticRealtimeWire(Socket(),policy);await wire.open(self.snapshot,**self.options)
        receipt=await policy.issue(session_scope,scope['request_id'])
        await wire.bind_pilot_claim(scope,receipt);await wire.send({'type':'response.create'})
        with self.assertRaises(ValueError):await wire.bind_pilot_claim(scope,receipt)
        self.assertEqual(sum(event['type']=='response.create' for event in wire.socket.sent),1)

    async def test_pilot_session_and_real_wire_complete_three_synthetic_turns(self):
        class Authority:
            async def check(self,scope,stage):return True
        class Lease:
            def __init__(self):self.held=False
            async def claim(self,scope):self.held=True;return 1
            async def assert_current(self,scope,fence):
                if not self.held or fence!=1:raise ValueError('lease_lost')
            async def confirm_provider_open(self,scope,fence):
                await self.assert_current(scope,fence);return True
            async def release(self,scope,fence):self.held=False;return True
        class Context:
            async def read_authorized(self,scope):
                return {'existing_copilot_instructions':'Never send messages or call tools.', 'lines':[], 'extra_context':{}}
            async def revalidate(self,scope):return True
        class Client:
            async def send(self,event):return None
        scope=(str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic');sid=str(uuid.uuid4())
        authority=Authority();lease=Lease();policy=PilotAdmission(authority,lease,scope,sid)
        wire=SyntheticRealtimeWire(Socket(),policy)
        session=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=wire,
                        client=Client(),session_id=sid,pilot_policy=policy,max_seconds=5,idle_seconds=4)
        await session.start()
        self.assertFalse(session.greeting_pending)
        self.assertEqual(sum(event['type']=='response.create' for event in wire.socket.sent),0)
        for n in range(3):
            await session.receive_client({'type':'talk_start'})
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            await session.receive_client({'type':'talk_end'})
            await session.receive_provider({'type':'input_audio_buffer.committed','item_id':f'input-{n}'})
            await session.receive_provider({'type':'response.created','response':{'id':f'response-{n}'}})
            await session.receive_provider({'type':'response.output_item.added','response_id':f'response-{n}',
                                            'item':{'id':f'assistant-{n}','type':'message','role':'assistant'}})
            await session.receive_provider({'type':'response.output_audio.delta','response_id':f'response-{n}',
                                            'item_id':f'assistant-{n}','content_index':0,'delta':'AQA='})
            await session.receive_provider({'type':'response.output_audio.done','response_id':f'response-{n}',
                                            'item_id':f'assistant-{n}','content_index':0})
            await session.receive_provider({'type':'response.done','response':{'id':f'response-{n}','output':[]}})
            before=copy.deepcopy(wire.socket.sent)
            await session.receive_client({'type':'talk_start'})
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            self.assertEqual(wire.socket.sent,before,'generated audio still playing cannot be interrupted by speech')
            self.assertIsNone(session.input_turn)
            await session.receive_client({'type':'played_all','item_id':f'assistant-{n}'})
            self.assertIsNone(session.item)
        self.assertEqual(sum(event['type']=='response.create' for event in wire.socket.sent),3)
        self.assertTrue(await session.end('test'))
        self.assertFalse(lease.held);self.assertTrue(wire.socket.closed)

    async def test_explicit_stop_interrupts_exact_item_and_dispatches_followup_after_truncate(self):
        class Authority:
            async def check(self,scope,stage):return True
        class Lease:
            def __init__(self):self.held=False
            async def claim(self,scope):self.held=True;return 1
            async def assert_current(self,scope,fence):
                if not self.held or fence!=1:raise ValueError('lease_lost')
            async def confirm_provider_open(self,scope,fence):return True
            async def release(self,scope,fence):self.held=False;return True
        class Context:
            async def read_authorized(self,scope):
                return {'existing_copilot_instructions':'Never send messages or call tools.', 'lines':[], 'extra_context':{}}
            async def revalidate(self,scope):return True
        class Client:
            def __init__(self):self.events=[]
            async def send(self,event):self.events.append(copy.deepcopy(event))
        scope=(str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic');sid=str(uuid.uuid4())
        authority=Authority();lease=Lease();policy=PilotAdmission(authority,lease,scope,sid)
        wire=SyntheticRealtimeWire(Socket(),policy);client=Client()
        session=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=wire,
                        client=client,session_id=sid,pilot_policy=policy,max_seconds=5,idle_seconds=4)
        await session.start()
        try:
            await session.receive_client({'type':'talk_start'})
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            await session.receive_client({'type':'talk_end'})
            await session.receive_provider({'type':'input_audio_buffer.committed','item_id':'input-0'})
            # Model thinking is also uninterruptible from ambient speech.
            before=copy.deepcopy(wire.socket.sent)
            await session.receive_client({'type':'talk_start'})
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            self.assertEqual(wire.socket.sent,before)
            self.assertFalse(session.cancel_requested)
            await session.receive_provider({'type':'response.created','response':{'id':'response-0'}})
            await session.receive_provider({'type':'response.output_item.added','response_id':'response-0',
                                            'item':{'id':'assistant-0','type':'message','role':'assistant'}})
            await session.receive_provider({'type':'response.output_audio.delta','response_id':'response-0',
                                            'item_id':'assistant-0','content_index':0,'delta':'AQA='})
            self.assertEqual(client.events[-1]['type'],'audio')

            # Automatic turn requests and residual mic audio cannot interrupt.
            before=copy.deepcopy(wire.socket.sent)
            await session.receive_client({'type':'talk_start'})
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            self.assertEqual(wire.socket.sent,before)
            self.assertIsNone(session.input_turn)
            self.assertFalse(session.cancel_requested)
            # Only the explicit Stop button cancels and truncates the answer.
            await session.receive_client({'type':'stop_response','item_id':'assistant-0','audio_end_ms':0})
            self.assertEqual(client.events[-1],{'type':'talk_ready'})
            self.assertEqual(wire.socket.sent[-2:],[
                {'type':'response.cancel'},
                {'type':'conversation.item.truncate','item_id':'assistant-0','content_index':0,'audio_end_ms':0},
            ])
            before=copy.deepcopy(wire.socket.sent)
            await session.receive_client({'type':'stop_response','item_id':'assistant-0','audio_end_ms':0})
            self.assertEqual(wire.socket.sent,before,'duplicate Stop must not cancel twice')
            await session.receive_client({'type':'audio','pcm':bytes(480)})
            await session.receive_client({'type':'talk_end'})
            await session.receive_provider({'type':'input_audio_buffer.committed','item_id':'input-1'})
            response_creates=lambda:sum(event['type']=='response.create' for event in wire.socket.sent)
            self.assertEqual(response_creates(),1,'follow-up waits while the interrupted item is not yet truncated')

            await session.receive_provider({'type':'response.done','response':{
                'id':'response-0','status':'cancelled','output':[{'type':'message','role':'assistant'}],'usage':{}}})
            self.assertEqual(response_creates(),1,'response.done alone cannot dispatch over unconfirmed truncation')
            await session.receive_provider({'type':'conversation.item.truncated','item_id':'assistant-0',
                                            'content_index':0,'audio_end_ms':0})
            self.assertEqual(response_creates(),2,'the new user turn is dispatched after exact truncate confirmation')
            self.assertTrue(any(event=={'type':'thinking'} for event in client.events))
        finally:
            await session.end('test')
        self.assertFalse(lease.held)

    async def test_wrong_scope_quote_or_missing_tenant_hold_cannot_bind(self):
        await self.open();scope=self.scope();good=self.receipt(scope)
        for changes in [{'channel_id':str(uuid.uuid4())},{'state':'reserved'},{'reserved_microusd':1},
                        {'model':'different'},{'quote_version':'old'},{'tenant_reservation_id':None}]:
            with self.assertRaises(ValueError):await self.wire.bind_claim(scope,{**good,**changes})
        self.assertEqual([e['type'] for e in self.socket.sent],['session.update','conversation.item.create'])

    async def test_denied_claim_and_unsafe_commands_never_send(self):
        await self.open();scope=self.scope();self.guard.deny=True
        await self.wire.bind_claim(scope,self.receipt(scope))
        with self.assertRaisesRegex(ValueError,'claim_not_consumed'):await self.wire.send({'type':'response.create'})
        for event in [{'type':'session.update'},{'type':'conversation.item.create'},{'type':'function_call_output'}]:
            with self.assertRaises(ValueError):await self.wire.send(event)
        self.assertEqual([e['type'] for e in self.socket.sent],['session.update','conversation.item.create'])

    async def test_dialog_content_never_enters_system_instructions(self):
        self.snapshot['lines']=[{'text':'SYNTHETIC UNTRUSTED INJECTION'}]
        await self.open()
        self.assertNotIn('SYNTHETIC UNTRUSTED INJECTION',self.socket.sent[0]['session']['instructions'])
        self.assertIn('SYNTHETIC UNTRUSTED INJECTION',self.socket.sent[1]['item']['content'][0]['text'])
        self.assertEqual(self.socket.sent[1]['item']['role'],'user')

    async def test_context_ack_must_match_and_readiness_waits(self):
        def wrong_id(item):item['id']='wrong-context'
        def wrong_text(item):item['content'][0]['text']='different context'
        def wrong_role(item):item['role']='assistant'
        for change in (wrong_id,wrong_text,wrong_role):
            class BadAck(Socket):
                async def receive_json(self):
                    event=await super().receive_json()
                    if event['type']=='conversation.item.added':change(event['item'])
                    return event
            wire=SyntheticRealtimeWire(BadAck(),Guard())
            with self.assertRaisesRegex(ValueError,'provider_context_unconfirmed'):
                await wire.open(self.snapshot,**self.options)
            with self.assertRaisesRegex(ValueError,'session_not_ready'):
                await wire.send({'type':'input_audio_buffer.clear'})

    async def test_no_context_ack_never_becomes_ready(self):
        class DelayedAck(Socket):
            def __init__(self):super().__init__();self.wait=asyncio.Event()
            async def receive_json(self):
                if self.sent and self.sent[-1]['type']=='conversation.item.create':
                    await self.wait.wait()
                return await super().receive_json()
        socket=DelayedAck();wire=SyntheticRealtimeWire(socket,Guard())
        task=asyncio.create_task(wire.open(self.snapshot,**self.options))
        for _ in range(20):
            if len(socket.sent)==2:break
            await asyncio.sleep(0)
        self.assertEqual(len(socket.sent),2);self.assertFalse(wire.ready)
        with self.assertRaisesRegex(ValueError,'session_not_ready'):
            await wire.send({'type':'input_audio_buffer.clear'})
        socket.wait.set();await task;self.assertTrue(wire.ready)

    async def test_context_ack_rejects_foreign_session_incomplete_and_nonroot(self):
        def wrong_session(event):event['session_id']='wrong-session'
        def incomplete(event):event['item']['status']='incomplete'
        def nonroot(event):event['previous_item_id']='foreign-item'
        for change in (wrong_session,incomplete,nonroot):
            class BadAck(Socket):
                async def receive_json(self):
                    event=await super().receive_json()
                    if event['type']=='conversation.item.added':change(event)
                    return event
            wire=SyntheticRealtimeWire(BadAck(),Guard())
            with self.assertRaisesRegex(ValueError,'provider_context_unconfirmed'):
                await wire.open(self.snapshot,**self.options)
            self.assertFalse(wire.ready)

    async def test_context_ack_can_follow_conversation_announcement(self):
        class AnnounceBeforeAck(Socket):
            def __init__(self):super().__init__();self.announced=False
            async def receive_json(self):
                if self.sent and self.sent[-1]['type']=='conversation.item.create' and not self.announced:
                    self.announced=True
                    return {'type':'conversation.created','conversation':{'id':'synthetic-conversation'}}
                return await super().receive_json()
        wire=SyntheticRealtimeWire(AnnounceBeforeAck(),Guard())
        await wire.open(self.snapshot,**self.options)
        self.assertTrue(wire.ready);self.assertEqual(wire.conversation_id,'synthetic-conversation')

    async def test_foreign_conversation_announcement_never_becomes_ready(self):
        for before_update in (True,False):
            class ForeignConversation(Socket):
                def __init__(self):super().__init__();self.announced=False
                async def receive_json(self):
                    context_sent=self.sent and self.sent[-1]['type']=='conversation.item.create'
                    if self.sent and not self.announced and bool(context_sent)!=before_update:
                        self.announced=True
                        return {'type':'conversation.created','session_id':'foreign-session',
                                'conversation':{'id':'synthetic-conversation'}}
                    return await super().receive_json()
            wire=SyntheticRealtimeWire(ForeignConversation(),Guard())
            with self.assertRaisesRegex(ValueError,'provider_conversation_invalid'):
                await wire.open(self.snapshot,**self.options)
            self.assertFalse(wire.ready)

    async def test_live_factory_cannot_be_enabled(self):
        with self.assertRaises(TenantLinearError):create_production_provider()
        with self.assertRaises(ValueError):SyntheticRealtimeWire(object(),Guard())

    async def test_session_identity_mismatch_refused(self):
        class WrongSession(Socket):
            async def receive_json(self):
                event=await super().receive_json()
                if event['type']=='session.updated':event['session']['id']='different-session'
                return event
        wire=SyntheticRealtimeWire(WrongSession(),Guard())
        with self.assertRaisesRegex(ValueError,'unsafe_session_readback'):await wire.open(self.snapshot,**self.options)
        self.assertEqual([event['type'] for event in wire.socket.sent],['session.update'])

    async def test_missing_created_id_and_later_foreign_session_refused(self):
        class MissingId(Socket):
            async def receive_json(self):return {'type':'session.created','session':{}}
        wire=SyntheticRealtimeWire(MissingId(),Guard())
        with self.assertRaisesRegex(ValueError,'session_id_missing'):await wire.open(self.snapshot,**self.options)
        self.assertEqual(wire.socket.sent,[])
        await self.open()
        async def foreign():return {'type':'response.created','session_id':'other','response':{'id':'synthetic'}}
        self.socket.receive_json=foreign
        with self.assertRaisesRegex(ValueError,'provider_session_changed'):await self.wire.receive()
        with self.assertRaisesRegex(ValueError,'session_not_ready'):await self.wire.send({'type':'input_audio_buffer.clear'})

    async def test_conversation_announcement_before_readback(self):
        class Interleaved(Socket):
            def __init__(self):super().__init__();self.announced=False
            async def receive_json(self):
                if self.sent and not self.announced:
                    self.announced=True
                    return {'type':'conversation.created','conversation':{'id':'synthetic-conversation'}}
                return await super().receive_json()
        wire=SyntheticRealtimeWire(Interleaved(),Guard())
        await wire.open(self.snapshot,**self.options)
        self.assertEqual(wire.conversation_id,'synthetic-conversation')
        self.assertEqual([e['type'] for e in wire.socket.sent],['session.update','conversation.item.create'])


if __name__=='__main__':unittest.main()
