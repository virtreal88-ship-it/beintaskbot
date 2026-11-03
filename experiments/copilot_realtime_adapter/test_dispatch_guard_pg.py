"""Durable guard and Session on isolated PG; fake socket, no API calls."""
import asyncio
import threading
import uuid
from unittest.mock import patch

import tenant_ai_spend_budget as spend
import tenant_operator_voice_context as voice
import tenant_operator_voice_orchestration as orchestration
from tenant_operator_realtime_wire import SyntheticRealtimeWire
from tenant_operator_voice_session import Session
from tenant_linear_policy import TenantLinearError
from test_integrated_synthetic_pg import IntegratedSynthetic
from test_session import Lease,Client


class GuardPG(IntegratedSynthetic):
    def test_session_dispatch_commits_before_socket_and_replay_fails(self):
        self.exercise_dispatch(False)

    def test_uncertain_socket_send_retains_hold_and_no_replay(self):
        self.exercise_dispatch(True)

    def test_dispatch_transaction_failure_never_reaches_socket(self):
        original=self.grant.issue_dispatch
        def fail_before_commit(cur,item):
            row=original(cur,item)
            if row is not None:raise OSError('synthetic rollback')
            return row
        with patch.object(self.grant,'issue_dispatch',fail_before_commit):
            self.exercise_dispatch(False,fail_issue=True)

    def test_guard_rejects_forged_receipts_and_revoked_actor(self):
        async def run():
            authority=await orchestration.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
            session_id=str(uuid.uuid4());budget=orchestration.ScopedBudget(authority,session_id)
            guard=orchestration.ScopedDispatchGuard(budget)
            request={'request_id':str(uuid.uuid4()),'tenant_id':self.tenant,'session_id':session_id,'actor_id':7,
                     'channel_id':self.channel,'conversation_id':self.conversation,'response_number':1,'executor':'copilot-voice-server'}
            await budget.reserve(request);receipt=await budget.claim(request)
            for change in ({'fingerprint':'wrong'},{'reserved_microusd':1},{'quote_version':'wrong'},
                           {'state':'reserved'},{'actual_microusd':1}):
                forged={**receipt,**change}
                self.assertFalse(await guard.consume(request,forged))
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'claimed')
                conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            with self.assertRaises(TenantLinearError):await guard.consume(request,receipt)
            with self.scoped() as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'claimed')
        with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped),\
             patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),patch.object(self.grant,'RECONCILIATION_VERIFIED',True),\
             patch.object(spend,'limit_microusd',return_value=0):asyncio.run(run())

    def test_mismatched_hold_id_never_authorizes_send(self):
        async def run():
            authority=await orchestration.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
            session_id=str(uuid.uuid4());budget=orchestration.ScopedBudget(authority,session_id)
            guard=orchestration.ScopedDispatchGuard(budget)
            request={'request_id':str(uuid.uuid4()),'tenant_id':self.tenant,'session_id':session_id,'actor_id':7,
                     'channel_id':self.channel,'conversation_id':self.conversation,'response_number':1,'executor':'copilot-voice-server'}
            await budget.reserve(request);receipt=await budget.claim(request)
            self.assertFalse(await guard.consume(request,{**receipt,'tenant_reservation_id':str(uuid.uuid4())}))
            # Mismatch is detected after committed CAS; retain rather than retry.
            with self.scoped() as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'dispatching')
            self.assertFalse(await guard.consume(request,receipt))
        with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped),\
             patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),patch.object(self.grant,'RECONCILIATION_VERIFIED',True),\
             patch.object(spend,'limit_microusd',return_value=0):asyncio.run(run())

    def test_cancelled_issue_may_commit_but_never_reuses_hold(self):
        entered=threading.Event();release=threading.Event();original=self.grant.issue_dispatch
        def blocked(cur,item):
            row=original(cur,item);entered.set()
            if not release.wait(3):raise RuntimeError('synthetic timeout')
            return row
        async def run():
            authority=await orchestration.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
            session_id=str(uuid.uuid4());budget=orchestration.ScopedBudget(authority,session_id)
            guard=orchestration.ScopedDispatchGuard(budget)
            request={'request_id':str(uuid.uuid4()),'tenant_id':self.tenant,'session_id':session_id,'actor_id':7,
                     'channel_id':self.channel,'conversation_id':self.conversation,'response_number':1,'executor':'copilot-voice-server'}
            await budget.reserve(request);receipt=await budget.claim(request)
            pending=asyncio.create_task(guard.consume(request,receipt))
            self.assertTrue(await asyncio.to_thread(entered.wait,1));pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
            release.set()
            for _ in range(100):
                with self.scoped() as conn:state=conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state']
                if state=='dispatching':break
                await asyncio.sleep(.01)
            self.assertEqual(state,'dispatching')
            self.assertFalse(await guard.consume(request,receipt))
        try:
            with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped),\
                 patch.object(self.grant,'issue_dispatch',blocked),patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),\
                 patch.object(self.grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):asyncio.run(run())
        finally:release.set()

    def exercise_dispatch(self,fail_send,fail_issue=False):
        outer=self
        profile={'tenant_id':self.tenant,'telegram_id':7}
        class Context:
            async def read_authorized(self,_):
                return {'existing_copilot_instructions':'Never send messages or take external actions.',
                        'lines':[],'extra_context':{}}
            async def revalidate(self,_):return True
        class Socket:
            synthetic_only=True
            def __init__(self):self.events=[];self.committed=[]
            async def receive_json(self):
                if not self.events:return {'type':'session.created','session':{'id':'synthetic-session'}}
                return {'type':'session.updated','session':{**self.events[-1]['session'],'id':'synthetic-session'}}
            async def send_json(self,event):
                if event['type']=='response.create':
                    with outer.scoped() as conn:
                        row=conn.execute('SELECT state FROM saas_voice_test_hold WHERE tenant_id=%s',(outer.tenant,)).fetchone()
                    self.committed.append(row['state'])
                    if fail_send:raise OSError('synthetic uncertain send')
                self.events.append(event)
            async def close(self):pass
        async def run():
            authority=await orchestration.ScopedAuthority.create(profile,self.route)
            session_id=str(uuid.uuid4());budget=orchestration.ScopedBudget(authority,session_id)
            guard=orchestration.ScopedDispatchGuard(budget);socket=Socket()
            provider=SyntheticRealtimeWire(socket,guard);client=Client();lease=Lease()
            session=Session(authority.session_scope,authority=authority,ledger=budget,lease=lease,
                            context=Context(),provider=provider,client=client,session_id=session_id,
                            executor='copilot-voice-server')
            await session.start()
            if fail_send or fail_issue:
                with self.assertRaises(OSError):await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            else:
                await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            self.assertEqual(socket.committed,[] if fail_issue else ['dispatching'])
            with self.scoped() as conn:
                row=conn.execute('SELECT * FROM saas_voice_test_hold WHERE tenant_id=%s',(self.tenant,)).fetchone()
                state=row['state']
            self.assertEqual(state,'unknown' if fail_send or fail_issue else 'dispatching')
            request={k:(str(row[k]) if k in ('request_id','tenant_id','session_id','channel_id') else row[k])
                     for k in ('request_id','tenant_id','session_id','actor_id','channel_id','conversation_id','response_number','executor')}
            # Original claim cannot be used again after the committed CAS.
            previous={**row,'state':'claimed'}
            self.assertFalse(await guard.consume(request,previous))
            self.assertEqual(len(socket.committed),0 if fail_issue else 1)
            await session.end('test')
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold WHERE tenant_id=%s',(self.tenant,)).fetchone()['state'],'unknown')
        with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped),\
             patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),patch.object(self.grant,'RECONCILIATION_VERIFIED',True),\
             patch.object(spend,'limit_microusd',return_value=0):
            asyncio.run(run())
