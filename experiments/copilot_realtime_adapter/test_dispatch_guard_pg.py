"""Durable guard and Session on isolated PG; fake socket, no API calls."""
import asyncio
import base64
import json
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

import tenant_ai_spend_budget as spend
import tenant_operator_voice_context as voice
import tenant_operator_voice_orchestration as orchestration
import tenant_operator_voice_api as voice_api
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from tenant_operator_realtime_wire import SyntheticRealtimeWire
from tenant_operator_voice_session import Session
from tenant_linear_policy import TenantLinearError
from test_integrated_synthetic_pg import IntegratedSynthetic
from test_session import Lease,Client


class GuardPG(IntegratedSynthetic):
    def test_gated_voice_history_deduplicates_orders_and_honors_clear(self):
        sql=Path(__file__).with_name('voice_history_candidate.sql').read_text()
        self.conn.execute(sql);self.conn.commit()
        async def run():
            authority=await orchestration.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
            session_id=str(uuid.uuid4());history=orchestration.VoiceHistory(authority,session_id)
            entry={'role':'employee','text':'synthetic partial','agent_binding':authority.scope.agent_binding,
                   'voice_session_id':session_id,'turn_sequence':0,'transcript_status':'partial',
                   'transcript_quality':'automatic_unverified'}
            # Gate rejects before touching the new tables even with valid input.
            with self.assertRaises(TenantLinearError):await history.append(entry,0)
            with self.assertRaises(TenantLinearError):await asyncio.to_thread(history._append,entry,0)
            with patch.object(orchestration,'require_history_cutover'):
                outcomes=await asyncio.gather(history.append(entry,0),history.append(entry,0))
                self.assertCountEqual(outcomes,['appended','duplicate'])
                with self.assertRaises(TenantLinearError):await history.append({**entry,'text':'changed'},0)
                with self.assertRaises(TenantLinearError):await history.append({**entry,'turn_sequence':2},0)
                with self.assertRaises(ValueError):await history.append({**entry,'audio':'synthetic-bytes'},0)
                missing={**entry,'turn_sequence':1,'text':'','transcript_status':'unavailable','transcript_quality':'unknown'}
                self.assertEqual(await history.append(missing,0),'appended')
                with self.scoped() as conn,conn.cursor() as cur:
                    saved=conn.execute('SELECT turns FROM saas_operator_copilot_threads').fetchone()['turns']
                    self.assertEqual([turn['transcript_status'] for turn in saved],['partial','unavailable'])
                    self.history.clear(cur,self.tenant,7,self.channel,self.conversation)
                with self.assertRaises(TenantLinearError):await history.append(entry,0)
                with self.scoped() as conn:
                    conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                with self.assertRaises(TenantLinearError):await history.append({**entry,'turn_sequence':2},0)
        try:
            with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped):asyncio.run(run())
        finally:
            self.conn.rollback()
            self.conn.execute('DROP TABLE IF EXISTS saas_operator_voice_turns,saas_operator_voice_sessions')
            self.conn.commit()

    def test_actual_http_gateway_provider_pump_and_pg_guard_with_test_gates(self):
        outer=self;profile={'tenant_id':self.tenant,'telegram_id':7};leases=[];sockets=[]
        class FakeSocket:
            def __init__(self):self.events=[];self.queue=asyncio.Queue();self.opened=False
            async def receive_json(self):
                if not self.opened:self.opened=True;return {'type':'session.created','session':{'id':'synthetic-session'}}
                if len(self.events)==1:return {'type':'session.updated','session':{**self.events[-1]['session'],'id':'synthetic-session'}}
                return await self.queue.get()
            async def send_json(self,event):
                self.events.append(event)
                if event['type']=='input_audio_buffer.append':
                    await self.queue.put({'type':'input_audio_buffer.speech_stopped'})
                if event['type']=='response.create':
                    with outer.scoped() as conn:
                        state=conn.execute('SELECT state FROM saas_voice_test_hold WHERE tenant_id=%s',(outer.tenant,)).fetchone()['state']
                    assert state=='dispatching'
                    await self.queue.put({'type':'response.created','response':{'id':'synthetic-r'}})
                    await self.queue.put({'type':'response.output_audio.delta','response_id':'synthetic-r','item_id':'synthetic-a',
                                          'content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
            async def close(self):await self.queue.put(None)
        def create_lease(_authority,_session_id):lease=Lease();leases.append(lease);return lease
        async def socket(_credential,_owner):
            result=FakeSocket();sockets.append(result);return result
        async def fake_context(_self,_scope):
            return {'existing_copilot_instructions':'Never send messages.','lines':[],'extra_context':{}}
        async def fake_revalidate(*_):return True
        async def fake_key(_self):return 'synthetic-key'
        async def run():
            app=web.Application()
            app.router.add_get('/ws',voice_api.handler(lambda _request:profile,'https://crm.pro.az',
                                                        __import__('logging').getLogger('synthetic-voice'),'ws'))
            browser=TestClient(TestServer(app));await browser.start_server()
            try:
                query={'expected_tenant_id':self.tenant,'expected_user_id':'7','lead_id':'41',
                       'channel_id':self.channel,'conversation_id':self.conversation}
                ws=await browser.ws_connect('/ws',params=query,headers={'Origin':'https://crm.pro.az'})
                self.assertEqual((await ws.receive_json())['type'],'ready')
                await ws.send_bytes(bytes(480))
                self.assertEqual((await asyncio.wait_for(ws.receive_json(),2))['type'],'audio')
                self.assertEqual([x['type'] for x in sockets[0].events],['session.update','conversation.item.create',
                                                                        'input_audio_buffer.append','response.create'])
                await ws.close()
                for _ in range(100):
                    if not leases[0].held:break
                    await asyncio.sleep(.01)
                self.assertFalse(leases[0].held)
            finally:await browser.close()
        with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped),\
             patch.object(orchestration,'require_history_cutover'),patch.object(orchestration,'_create_server_lease',side_effect=create_lease),\
             patch('tenant_operator_voice_session._require_live_session_admission'),\
             patch('tenant_operator_realtime_wire._require_live_admission'),\
             patch('tenant_operator_realtime_wire._open_openai_socket',side_effect=socket),\
             patch.object(orchestration.ServerCredentialLoader,'load',fake_key),\
             patch.object(orchestration.CopilotContext,'read_authorized',fake_context),\
             patch.object(orchestration.CopilotContext,'revalidate',fake_revalidate),\
             patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),patch.object(self.grant,'RECONCILIATION_VERIFIED',True),\
             patch.object(spend,'limit_microusd',return_value=0):asyncio.run(run())

    def test_company_credential_loader_rechecks_scope_and_never_exposes_bad_secret(self):
        calls=[]
        class Cipher:
            def decrypt(self,_value):
                calls.append('decrypt')
                return json.dumps({'tenant_id':self_outer.tenant,'provider':'chat_ai','api_key':'synthetic-only'}).encode()
        self_outer=self
        async def run():
            authority=await orchestration.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
            loader=orchestration.ServerCredentialLoader(authority)
            self.assertEqual(await loader.load(),'synthetic-only')
            with self.scoped() as conn:
                conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            with self.assertRaises(TenantLinearError):await loader.load()
            self.assertEqual(calls,['decrypt'])
        with patch.object(voice,'_connect',self.scoped),patch.object(orchestration,'_connect',self.scoped),\
             patch('tenant_platform._fernet',return_value=Cipher()):asyncio.run(run())

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
