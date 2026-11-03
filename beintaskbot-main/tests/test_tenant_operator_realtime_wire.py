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
        if not self.sent:return {'type':'session.created'}
        config=copy.deepcopy(self.sent[-1]['session'])
        if self.mutate:self.mutate(config)
        return {'type':'session.updated','session':config}
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
        self.options={'tools':[],'create_response':False,'interrupt_response':True,'transcription':False,'reconnect':False}

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
                 lambda s:s['audio']['input'].__setitem__('transcription',{'model':'unapproved'}),
                 lambda s:s.__setitem__('tools',[{'name':'send'}]),lambda s:s.__setitem__('max_output_tokens',999999),
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

    async def test_live_factory_cannot_be_enabled(self):
        with self.assertRaises(TenantLinearError):create_production_provider()
        with self.assertRaises(ValueError):SyntheticRealtimeWire(object(),Guard())


if __name__=='__main__':unittest.main()
