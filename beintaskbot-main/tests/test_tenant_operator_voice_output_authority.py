"""Synthetic latency and authority tests; no provider or production database."""
import asyncio
import base64
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tenant_linear_policy import TenantLinearError
from tenant_operator_realtime_wire import SyntheticRealtimeWire
from tenant_operator_voice_lease import VoiceSessionLease
from tenant_operator_voice_orchestration import ScopedAuthority
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_voice_session import Session, ProviderEventGuardRejected, ProviderResponseRejected
from tenant_operator_voice_gateway import _provider_failure_fields
from tests.test_tenant_operator_voice_input_authority import FakeDatabase
from tests.test_tenant_operator_realtime_wire import Socket
import tenant_operator_voice_lease as lease_module
import tenant_operator_voice_orchestration as orchestration


class OutputDispatchAuthorityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tenant=str(uuid.uuid4());channel=str(uuid.uuid4());actor=7
        self.scope=SimpleNamespace(tenant=tenant,actor=actor,channel_id=channel,conversation_id='synthetic',
                                   lead_id=1,agent_binding='synthetic',config_version='v1')
        self.identity={'value':{'tenant_id':tenant,'telegram_id':actor}}
        self.route={'provider':'connect','lead_id':1,'channel_id':channel,'conversation_id':'synthetic'}
        self.authority=ScopedAuthority({},self.route,self.scope,lambda:self.identity['value'])
        self.lease=VoiceSessionLease(self.authority,str(uuid.uuid4()));self.lease.fence=9
        self.database=FakeDatabase();self.database.lease=self.lease;self.database.owner=self.lease.owner_id
        self.config_changed=False
        for p in (patch.object(lease_module,'_connect',side_effect=self.database.connect),
                  patch.object(orchestration,'require_pilot'),
                  patch.object(orchestration.voice,'check',side_effect=AssertionError('redundant separate check')),
                  patch.object(orchestration.voice,'authorize',side_effect=self.authorize)):
            p.start();self.addCleanup(p.stop)

    def authorize(self,_cur,profile,route):
        self.database.authorizations+=1
        self.assertEqual(profile,{'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor})
        self.assertEqual(route,self.route)
        if not self.database.route_valid:raise TenantLinearError('synthetic denial',403)
        if self.config_changed:return SimpleNamespace(**{**vars(self.scope),'config_version':'v2'})
        return self.scope

    async def make_session(self):
        policy=PilotAdmission(self.authority,self.lease,self.authority.session_scope,self.lease.session_id)
        policy._fence=9;socket=Socket();wire=SyntheticRealtimeWire(socket,policy)
        await wire.open({'existing_copilot_instructions':'Never send messages','lines':[],'extra_context':{}},
                        tools=[],create_response=False,interrupt_response=True,transcription=None,reconnect=False,duplex=True)
        class Context:
            def __init__(self):self.calls=0;self.valid=True
            async def revalidate(self,_):
                self.calls+=1
                if not self.valid:raise TenantLinearError('synthetic context change',409)
        class Client:
            def __init__(self):self.events=[]
            async def send(self,event):self.events.append(event)
        context=Context();client=Client()
        s=Session(self.authority.session_scope,authority=self.authority,ledger=None,lease=self.lease,
                  context=context,provider=wire,client=client,session_id=self.lease.session_id,
                  pilot_policy=policy,max_seconds=600,duplex=True)
        s.closed=False;s.fence=9;s.started=time.monotonic();s.VAD_SETTLE_SECONDS=.001
        s._remember_context_item(wire.context_id,usable=True,role='user')
        return s,policy,socket,context,client

    @staticmethod
    def audio():
        return {'type':'response.output_audio.delta','response_id':'synthetic-response',
                'item_id':'synthetic-item','content_index':0,'delta':base64.b64encode(bytes(480)).decode()}

    async def test_each_provider_event_rechecks_full_authority_and_fence(self):
        s,_,_,_,client=await self.make_session()
        s.response_inflight=True;s.provider_response_id='synthetic-response'
        await s._receive_provider(self.audio());await s._receive_provider(self.audio())
        self.assertEqual(self.database.connects,2);self.assertEqual(self.database.authorizations,2)
        self.assertEqual([event['type'] for event in client.events],['audio','audio'])

    async def test_revocation_or_changed_fence_prevents_output_audio(self):
        for mode in ('identity','route','config','fence','owner','expired','state'):
            with self.subTest(mode=mode):
                self.identity['value']={'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor}
                self.database.route_valid=True;self.config_changed=False;self.database.fence=9
                self.database.owner=self.lease.owner_id;self.database.expired=False;self.database.active=True
                s,_,_,_,client=await self.make_session();s.response_inflight=True;s.provider_response_id='synthetic-response'
                if mode=='identity':self.identity['value']=None
                if mode=='route':self.database.route_valid=False
                if mode=='config':self.config_changed=True
                if mode=='fence':self.database.fence=10
                if mode=='owner':self.database.owner=str(uuid.uuid4())
                if mode=='expired':self.database.expired=True
                if mode=='state':self.database.active=False
                with self.assertRaises(ProviderEventGuardRejected) as caught:await s._receive_provider(self.audio())
                self.assertEqual(caught.exception.diagnostic_code,'provider_authority_or_lease_rejected')
                self.assertFalse(any(event['type']=='audio' for event in client.events))

    async def test_transcript_failure_diagnostic_has_only_safe_combined_code(self):
        s,_,_,_,_=await self.make_session();s.response_inflight=True;s.provider_response_id='synthetic-response'
        self.identity['value']=None
        event={'type':'response.output_audio_transcript.delta','response_id':'synthetic-response','delta':'private words'}
        with self.assertRaises(ProviderResponseRejected) as caught:await s._receive_provider(event)
        fields=_provider_failure_fields('provider_event',event,caught.exception)
        self.assertEqual(fields[0],'output_authority_or_lease_rejected')
        self.assertNotIn('private',repr(fields));self.assertNotIn('synthetic-response',repr(fields))

    async def test_other_dependencies_keep_separate_output_checks(self):
        s,_,_,_,_=await self.make_session()
        authority=SimpleNamespace(check=AsyncMock());lease=SimpleNamespace(assert_current=AsyncMock())
        s.authority=authority;s.lease=lease
        await s._receive_provider({'type':'rate_limits.updated'})
        authority.check.assert_awaited_once_with(s.scope,'output')
        lease.assert_current.assert_awaited_once_with(s.scope,s.fence)

    async def test_mismatched_bound_authority_never_forwards_output(self):
        s,_,_,_,client=await self.make_session()
        s.authority=ScopedAuthority({},self.route,self.scope,lambda:self.identity['value'])
        s.response_inflight=True;s.provider_response_id='synthetic-response'
        with self.assertRaises(ProviderEventGuardRejected):await s._receive_provider(self.audio())
        self.assertFalse(any(event['type']=='audio' for event in client.events))

    async def test_close_during_output_authorization_never_forwards_audio(self):
        s,_,_,_,client=await self.make_session()
        s.response_inflight=True;s.provider_response_id='synthetic-response'
        entered=asyncio.Event();release=asyncio.Event()
        async def blocked(*_):entered.set();await release.wait()
        self.lease.assert_current=blocked
        pending=asyncio.create_task(s._receive_provider(self.audio()))
        await entered.wait();s.closed=True;release.set()
        with self.assertRaises(Exception):await pending
        self.assertFalse(any(event['type']=='audio' for event in client.events))

    async def test_issue_and_consume_each_recheck_and_permit_is_single_use(self):
        s,policy,_,_,_=await self.make_session();request_id=str(uuid.uuid4())
        receipt=await policy.issue(s.scope,request_id)
        self.assertTrue(await policy.consume(s.scope,receipt))
        self.assertFalse(await policy.consume(s.scope,receipt))
        self.assertEqual(self.database.authorizations,2)

    async def test_tts_live_admission_rechecks_each_send_in_one_authorized_transaction(self):
        s,policy,_,_,_=await self.make_session()
        await policy.assert_live(s.scope,s.session_id)
        await policy.assert_live(s.scope,s.session_id)
        self.assertEqual(self.database.connects,2)
        self.assertEqual(self.database.authorizations,2)
        for mode in ('identity','route','config','fence','owner','expired','state'):
            with self.subTest(mode=mode):
                self.identity['value']={'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor}
                self.database.route_valid=True;self.config_changed=False;self.database.fence=9
                self.database.owner=self.lease.owner_id;self.database.expired=False;self.database.active=True
                if mode=='identity':self.identity['value']=None
                if mode=='route':self.database.route_valid=False
                if mode=='config':self.config_changed=True
                if mode=='fence':self.database.fence=10
                if mode=='owner':self.database.owner=str(uuid.uuid4())
                if mode=='expired':self.database.expired=True
                if mode=='state':self.database.active=False
                with self.assertRaises(Exception):await policy.assert_live(s.scope,s.session_id)

    async def test_revocation_during_consumption_cannot_reuse_the_permit(self):
        s,policy,_,_,_=await self.make_session();request_id=str(uuid.uuid4())
        receipt=await policy.issue(s.scope,request_id);self.identity['value']=None
        with self.assertRaises(Exception):await policy.consume(s.scope,receipt)
        self.assertIsNone(policy._permits[request_id])
        self.identity['value']={'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor}
        self.assertFalse(await policy.consume(s.scope,receipt))

    async def test_permit_issue_rechecks_and_nonproduction_pair_keeps_both_checks(self):
        s,policy,_,_,_=await self.make_session();self.identity['value']=None
        with self.assertRaises(Exception):await policy.issue(s.scope,str(uuid.uuid4()))
        self.assertEqual(policy._permits,{})
        authority=SimpleNamespace(check=AsyncMock());lease=SimpleNamespace(assert_current=AsyncMock())
        fallback=PilotAdmission(authority,lease,s.scope,s.session_id);fallback._fence=9
        receipt=await fallback.issue(s.scope,str(uuid.uuid4()))
        self.assertTrue(await fallback.consume(s.scope,receipt))
        self.assertEqual(authority.check.await_count,2);self.assertEqual(lease.assert_current.await_count,2)

    async def test_one_response_keeps_all_four_live_boundaries_and_context_checks(self):
        s,policy,socket,context,_=await self.make_session()
        async def turn():
            await s.receive_provider({'type':'input_audio_buffer.speech_started'})
            await s.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await s.receive_provider({'type':'conversation.item.added',
                                      'item':{'id':'turn1','type':'message','role':'user'}})
            await s.receive_provider({'type':'input_audio_buffer.committed','item_id':'turn1'})
        await turn()
        self.assertEqual(self.database.authorizations,8,'four provider events and four dispatch boundaries')
        self.assertEqual(context.calls,3)
        self.assertEqual(sum(event['type']=='response.create' for event in socket.sent),1)
        self.assertEqual(list(policy._permits.values()),[None])

    async def test_dispatch_rechecks_after_waiting_for_write_lock(self):
        for mode in ('identity','fence','context','closed'):
            with self.subTest(mode=mode):
                self.identity['value']={'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor}
                self.database.fence=9
                s,policy,socket,context,_=await self.make_session()
                s._remember_context_item('turn1',usable=True,role='user')
                s.pending_input_item_id='turn1';s.response_pending=True
                class TrackedLock(asyncio.Lock):
                    def __init__(self):super().__init__();self.waiting=asyncio.Event()
                    async def __aenter__(self):
                        self.waiting.set()
                        return await super().__aenter__()
                s.io_lock=TrackedLock()
                await s.io_lock.acquire()
                pending=asyncio.create_task(s._dispatch_pending())
                for _ in range(1000):
                    if policy._permits and list(policy._permits.values())==[None]:break
                    await asyncio.sleep(.001)
                self.assertEqual(list(policy._permits.values()),[None])
                await asyncio.wait_for(s.io_lock.waiting.wait(),1)
                self.assertFalse(pending.done())
                if mode=='identity':self.identity['value']=None
                if mode=='fence':self.database.fence=10
                if mode=='context':context.valid=False
                if mode=='closed':s.closed=True
                s.io_lock.release()
                with self.assertRaises(Exception):await asyncio.wait_for(pending,2)
                self.assertEqual(sum(event['type']=='response.create' for event in socket.sent),0)
                self.assertEqual(list(policy._permits.values()),[None])

    async def test_fixed_latency_model_counts_checks_without_claiming_real_latency(self):
        s,_,_,_,_=await self.make_session();elapsed=[0.0]
        async def fixed_latency(fn,*args,**kwargs):
            elapsed[0]+=.15
            return fn(*args,**kwargs)
        with patch.object(orchestration.voice,'check',return_value=self.scope),patch.object(asyncio,'to_thread',side_effect=fixed_latency):
            for _ in range(20):
                await self.authority.check(s.scope,'output');await self.lease.assert_current(s.scope,s.fence)
            old=elapsed[0];elapsed[0]=0
            for _ in range(20):await s._receive_provider({'type':'rate_limits.updated'})
            new=elapsed[0]
        self.assertAlmostEqual(old,6.0);self.assertAlmostEqual(new,3.0)


class AnalyticsOutputDispatchAuthorityTest(OutputDispatchAuthorityTest):
    """Run the #593 revocation and queue contract against the analyst authority."""
    def setUp(self):
        super().setUp()
        import tenant_ai_business_analytics_voice as analytics_voice
        import tenant_ai_business_analytics as analytics
        self.authority=analytics_voice.AnalyticsVoiceAuthority({},self.scope,lambda:self.identity['value'])
        self.lease=VoiceSessionLease(self.authority,str(uuid.uuid4()));self.lease.fence=9
        self.database.lease=self.lease;self.database.owner=self.lease.owner_id
        def owner(cur,profile):
            self.database.authorizations+=1
            self.assertEqual(profile,self.authority.profile)
            if not self.database.route_valid:raise TenantLinearError('synthetic owner revoked',403)
            return self.scope.tenant,self.scope.actor
        for p in (patch.object(analytics_voice,'_connect',side_effect=self.database.connect),
                  patch.object(analytics,'_owner',side_effect=owner),
                  patch.object(analytics_voice,'_voice_connection',side_effect=lambda *_:'v2' if self.config_changed else 'v1')):
            p.start();self.addCleanup(p.stop)

    async def test_analyst_input_uses_one_live_transaction_and_rejects_revocation(self):
        s,_,socket,_,_=await self.make_session()
        await s._receive_client({'type':'audio','pcm':bytes(480)})
        self.assertEqual(self.database.connects,1)
        self.assertEqual(self.database.authorizations,1)
        self.assertEqual(sum(e['type']=='input_audio_buffer.append' for e in socket.sent),1)
        self.database.route_valid=False
        with self.assertRaises(Exception):await s._receive_client({'type':'audio','pcm':bytes(480)})
        self.assertEqual(sum(e['type']=='input_audio_buffer.append' for e in socket.sent),1)


if __name__=='__main__':unittest.main()
