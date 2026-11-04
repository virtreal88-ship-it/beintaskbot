import asyncio
import copy
import unittest
import uuid
from tenant_linear_policy import TenantLinearError
from tenant_operator_realtime_wire import SyntheticRealtimeWire,create_production_provider,MODEL,QUOTE,MINIMUM_HOLD


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
        self.options={'tools':[],'create_response':False,'interrupt_response':False,'transcription':False,'reconnect':False}

    async def open(self):await self.wire.open(self.snapshot,**self.options)

    def scope(self):
        return {'request_id':str(uuid.uuid4()),'tenant_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4()),
                'channel_id':str(uuid.uuid4()),'conversation_id':'synthetic','actor_id':7,'response_number':1,'executor':'test'}

    def receipt(self,scope):return {**scope,'state':'claimed','model':MODEL,'quote_version':QUOTE,'reserved_microusd':MINIMUM_HOLD,
                                    'tenant_reservation_id':str(uuid.uuid4())}

    async def test_effective_policy_must_be_read_back_before_audio(self):
        with self.assertRaises(ValueError):await self.wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
        await self.open();self.assertEqual(self.socket.sent[0]['type'],'session.update')
        await self.wire.send({'type':'input_audio_buffer.append','audio':'AAA='});self.assertEqual(self.socket.sent[-1]['type'],'input_audio_buffer.append')

    async def test_mismatched_policy_closes_admission(self):
        changes=[lambda s:s['audio']['input']['turn_detection'].__setitem__('create_response',True),
                 lambda s:s['audio']['input']['turn_detection'].__setitem__('interrupt_response',True),
                 lambda s:s['audio']['input']['turn_detection'].__setitem__('idle_timeout_ms',1000),
                 lambda s:s['audio']['input']['turn_detection'].__setitem__('future_trigger',True),
                 lambda s:s['audio']['input'].__setitem__('transcription',{'model':'unapproved'}),
                 lambda s:s.__setitem__('tools',[{'name':'send'}]),lambda s:s.__setitem__('max_output_tokens',999999),
                 lambda s:s.__setitem__('max_output_tokens',32000),
                 lambda s:s.__setitem__('model','other')]
        for change in changes:
            wire=SyntheticRealtimeWire(Socket(change),Guard())
            with self.assertRaisesRegex(ValueError,'unsafe_session_readback'):await wire.open(self.snapshot,**self.options)
            with self.assertRaises(ValueError):await wire.send({'type':'response.create'})

    async def test_one_committed_scoped_claim_per_response(self):
        await self.open()
        with self.assertRaisesRegex(ValueError,'claim_missing'):await self.wire.send({'type':'response.create'})
        scope=self.scope();receipt=self.receipt(scope);await self.wire.bind_claim(scope,receipt)
        await self.wire.send({'type':'response.create'})
        with self.assertRaisesRegex(ValueError,'claim_reused'):await self.wire.bind_claim(scope,receipt)
        with self.assertRaisesRegex(ValueError,'claim_missing'):await self.wire.send({'type':'response.create'})

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
