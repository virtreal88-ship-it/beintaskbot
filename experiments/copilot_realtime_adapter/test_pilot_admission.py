"""Local policy checks; no database or provider calls."""
import asyncio
import unittest
import uuid
from session import Session, Refused  # Adds the application module path.
from test_session import Authority as SessionAuthority, Lease as SessionLease, Context, Client
from tenant_operator_voice_pilot import PilotAdmission, PilotAdmissionError


class Authority:
    def __init__(self):self.allowed=True
    async def check(self,scope,stage):
        if not self.allowed:raise PilotAdmissionError('revoked')


class Lease:
    def __init__(self):self.current=True
    async def claim(self,scope):return 1
    async def assert_current(self,scope,fence):
        if not self.current or fence!=1:raise PilotAdmissionError('lease_lost')
    async def confirm_provider_open(self,scope,fence):
        await self.assert_current(scope,fence)
        return True
    async def release(self,scope,fence):return True


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope=('tenant',1,'channel','conversation');self.authority=Authority();self.lease=Lease()
        self.session_id=str(uuid.uuid4());self.policy=PilotAdmission(self.authority,self.lease,self.scope,self.session_id)
        await self.policy.admit(self.scope,self.session_id,1)

    async def test_single_use_under_parallel_dispatch(self):
        token=await self.policy.issue(self.scope,str(uuid.uuid4()))
        results=await asyncio.gather(self.policy.consume(self.scope,token),self.policy.consume(self.scope,token))
        self.assertEqual(sorted(results),[False,True])

    async def test_revocation_consumes_permit_without_dispatch(self):
        token=await self.policy.issue(self.scope,str(uuid.uuid4()));self.authority.allowed=False
        with self.assertRaisesRegex(PilotAdmissionError,'revoked'):await self.policy.consume(self.scope,token)
        self.authority.allowed=True
        self.assertFalse(await self.policy.consume(self.scope,token))

    async def test_lost_lease_consumes_permit_without_dispatch(self):
        token=await self.policy.issue(self.scope,str(uuid.uuid4()));self.lease.current=False
        with self.assertRaisesRegex(PilotAdmissionError,'lease_lost'):await self.policy.consume(self.scope,token)
        self.lease.current=True
        self.assertFalse(await self.policy.consume(self.scope,token))

    async def test_wrong_scope_and_closed_policy_refuse(self):
        token=await self.policy.issue(self.scope,str(uuid.uuid4()))
        self.assertFalse(await self.policy.consume(('other',1,'channel','conversation'),token))
        await self.policy.close()
        self.assertFalse(await self.policy.consume(self.scope,token))

    async def test_request_identity_cannot_be_reissued(self):
        request_id=str(uuid.uuid4());await self.policy.issue(self.scope,request_id)
        with self.assertRaisesRegex(PilotAdmissionError,'request_reused'):await self.policy.issue(self.scope,request_id)

    async def test_missing_token_cannot_replay_a_consumed_permit(self):
        receipt=await self.policy.issue(self.scope,str(uuid.uuid4()))
        self.assertTrue(await self.policy.consume(self.scope,receipt))
        self.assertFalse(await self.policy.consume(self.scope,{**receipt,'token':None}))


class MockPilotProvider:
    mock_only=True
    def __init__(self,policy):self.policy=policy;self.bound=None;self.events=[];self.closed=False
    async def open(self,snapshot,**config):self.config=config
    async def bind_pilot_claim(self,scope,receipt):self.bound=(scope,receipt)
    async def send(self,event):
        if event['type']=='response.create':
            scope,receipt=self.bound;self.bound=None
            if not await self.policy.consume((scope['tenant_id'],scope['actor_id'],scope['channel_id'],scope['conversation_id']),receipt):
                raise Refused('pilot_claim_not_consumed')
        self.events.append(event)
    async def close(self):self.closed=True


async def complete_initial_greeting(session):
    """Model the provider audio and browser playback ack required before input."""
    import base64
    await session.receive_provider({'type':'response.created','response':{'id':'greeting-response'}})
    await session.receive_provider({'type':'response.output_audio.delta','response_id':'greeting-response',
                                    'item_id':'greeting-audio','content_index':0,
                                    'delta':base64.b64encode(bytes(3200)).decode()})
    await session.receive_provider({'type':'response.output_audio.done','response_id':'greeting-response',
                                    'item_id':'greeting-audio','content_index':0})
    await session.receive_provider({'type':'response.done','response':{'id':'greeting-response','output':[]}})
    await asyncio.sleep(.11)  # playback ack must follow the modeled audio duration
    await session.receive_client({'type':'played_all','item_id':'greeting-audio'})


class PilotSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_turns_with_no_financial_ledger(self):
        scope=('tenant',42,'channel','conversation');authority=SessionAuthority();lease=SessionLease()
        sid=str(uuid.uuid4());policy=PilotAdmission(authority,lease,scope,sid);provider=MockPilotProvider(policy)
        session=Session(scope,authority=authority,ledger=None,lease=lease,context=Context(),provider=provider,
                        client=Client(),session_id=sid,pilot_policy=policy,max_seconds=5,idle_seconds=4)
        await session.start()
        await complete_initial_greeting(session)
        for n in range(3):
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await session.receive_provider({'type':'input_audio_buffer.committed','item_id':f'input-{n}'})
            await session.receive_provider({'type':'response.created','response':{'id':f'response-{n}'}})
            await session.receive_provider({'type':'response.done','response':{'id':f'response-{n}','output':[]}})
        self.assertEqual(sum(event['type']=='response.create' for event in provider.events),4)
        self.assertTrue(await session.end('test'))
        self.assertTrue(provider.closed);self.assertFalse(lease.held)
