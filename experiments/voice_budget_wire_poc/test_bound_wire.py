"""Real #480 _RealtimeWire, fake sockets, disposable real PostgreSQL only."""
import asyncio
import base64
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch
import uuid

from ledger import local_connect
import session_budget as budget
import test_sender as base
import test_session_budget as session_tests
import bound_wire as integration
from tenant_operator_realtime_wire import MODEL,QUOTE,MINIMUM_HOLD

SNAPSHOT={'existing_copilot_instructions':'Never send messages, call tools or perform external actions.','lines':[],'extra_context':{}}
OPTIONS={'tools':[],'create_response':False,'interrupt_response':False,'transcription':False,'reconnect':False}

def scoped(**changes):
    s=session_tests.scope(**changes)
    s['source_fingerprint']=integration.synthetic_source_fingerprint(s,SNAPSHOT)
    return s

class Guard:
    async def consume(self,scope,receipt):return True  # only #480 interface; not authorization

def good_config():
    return {'type':'session.update','session':{'type':'realtime','model':MODEL,'instructions':SNAPSHOT['existing_copilot_instructions'],
      'output_modalities':['audio'],'tools':[],'tool_choice':'none','max_output_tokens':4096,
      'audio':{'input':{'format':{'type':'audio/pcm','rate':24000},'transcription':None,
        'turn_detection':{'type':'server_vad','create_response':False,'interrupt_response':False,'idle_timeout_ms':None}},
        'output':{'format':{'type':'audio/pcm','rate':24000}}}}}

class WireBudgetTests(base.BoundSenderTests):
    def setUp(self):
        super().setUp()
        p=patch.object(integration,'ENABLED_SYNTHETIC',True);p.start();self.addCleanup(p.stop)
    def claim(self,s):
        request=session_tests.uid();self.call(budget.claim_response,s,request,1)
        with local_connect(self.dsn) as conn:hold=conn.execute('SELECT tenant_reservation_id FROM saas_voice_session_budget').fetchone()['tenant_reservation_id']
        scope={'request_id':request,'tenant_id':s['tenant_id'],'session_id':s['session_id'],'actor_id':s['actor_id'],
               'channel_id':s['channel_id'],'conversation_id':s['conversation_id'],'executor':s['executor'],'response_number':1}
        # This legacy shape is simulated only. Real #480 cannot yet use the
        # new ledger's claim type without a reviewed interface migration.
        receipt={**scope,'state':'claimed','model':MODEL,'quote_version':QUOTE,'reserved_microusd':MINIMUM_HOLD,'tenant_reservation_id':str(hold)}
        return scope,receipt
    def test_hold_commits_before_connection_and_no_hold_no_fake_socket(self):
        async def run():
            s=scoped()
            with patch.object(integration,'_FakeSocket',wraps=integration._FakeSocket) as fake:
                with self.assertRaises(Exception):await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
                fake.assert_not_called()
                self.call(budget.reserve_session,s)
                wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
                self.assertEqual(fake.call_count,1)
                with local_connect(self.dsn) as conn:
                    self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'opening','reserved_microusd':4_224_000})
                with self.assertRaisesRegex(ValueError,'open_capability_unavailable'):await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
                self.assertEqual(fake.call_count,1)
        asyncio.run(run())
    def test_real_wire_config_context_audio_response_all_match_committed_bytes(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            pcm=b'\0\1'*200
            await wire.send({'type':'input_audio_buffer.append','audio':base64.b64encode(pcm).decode()})
            claim,receipt=self.claim(s);await wire.bind_claim(claim,receipt);await wire.send({'type':'response.create'})
            await wire.send({'type':'input_audio_buffer.clear'})
            frames=wire.socket.evidence()
            self.assertEqual([json.loads(x)['type'] for x in frames],['session.update','conversation.item.create','input_audio_buffer.append','response.create','input_audio_buffer.clear'])
            with local_connect(self.dsn) as conn:
                rows=conn.execute('SELECT operation,scope_fingerprint,wire_sha256,wire_bytes FROM saas_voice_bound_sender_receipt').fetchall()
                self.assertEqual({x['operation'] for x in rows},{'config','context','audio','response','control'})
                self.assertEqual({(r['wire_sha256'],r['wire_bytes']) for r in rows},{(hashlib.sha256(x).hexdigest(),len(x)) for x in frames})
                self.assertTrue(all(r['scope_fingerprint']==budget._fingerprint(s) for r in rows))
                self.assertEqual(conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS held FROM saas_ai_spend_reservations').fetchone(),{'n':1,'held':4_224_000})
        asyncio.run(run())
    def test_direct_unsafe_config_and_early_audio_are_rejected_before_receipt(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
            with self.assertRaises(ValueError):await wire.socket.send_json({'type':'session.update','session':{'tools':[{'name':'send'}]}})
            mutations=[lambda x:x['session']['audio']['input'].__setitem__('transcription',{'model':'gpt-transcribe'}),
                       lambda x:x['session']['audio']['input']['turn_detection'].__setitem__('create_response',True),
                       lambda x:x['session']['audio']['input']['turn_detection'].__setitem__('idle_timeout_ms',5000),
                       lambda x:x['session'].__setitem__('instructions','Never send messages. Other instructions.'),
                       lambda x:x['session'].__setitem__('tools',[{'type':'function'}]),
                       lambda x:x['session'].__setitem__('max_output_tokens','inf')]
            for change in mutations:
                unsafe=copy.deepcopy(good_config());change(unsafe)
                with self.assertRaisesRegex(ValueError,'config_invalid'):await wire.socket.send_json(unsafe)
            with self.assertRaises(ValueError):await wire.socket.send_json({'type':'input_audio_buffer.append','audio':'AAA='})
            self.assertEqual(wire.socket.evidence(),())
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_bound_sender_receipt').fetchone()['n'],0)
        asyncio.run(run())
    def test_mismatched_synthetic_snapshot_rejected_before_connect_and_open(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            changed={**SNAPSHOT,'existing_copilot_instructions':'Never send messages. Other instructions.'}
            with patch.object(integration,'_FakeSocket',wraps=integration._FakeSocket) as fake:
                with self.assertRaisesRegex(ValueError,'snapshot_scope_mismatch'):
                    await integration.connect_synthetic(self.dsn,s,Guard(),changed)
                fake.assert_not_called()
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
            with self.assertRaisesRegex(ValueError,'snapshot_scope_mismatch'):await wire.open(changed,**OPTIONS)
            self.assertEqual(wire.socket.evidence(),())
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'uncertain')
        asyncio.run(run())
    def test_quote_expiry_while_waiting_for_tenant_lock_prevents_cas_and_send(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            expires=datetime.now(timezone.utc)+timedelta(seconds=.7);checked=threading.Event();original=budget._quote_fresh
            def check():original();checked.set()
            with local_connect(self.dsn) as holder:
                holder.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
                with patch.object(budget,'QUOTE_EXPIRES',expires),patch.object(budget,'_quote_fresh',check):
                    task=asyncio.create_task(wire.send({'type':'input_audio_buffer.append','audio':'AAA='}))
                    self.assertTrue(await asyncio.to_thread(checked.wait,2))
                    await asyncio.sleep(max(0,(expires-datetime.now(timezone.utc)).total_seconds())+.05)
                    holder.commit()
                    with self.assertRaisesRegex(Exception,'quote_expired'):await task
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchone()['n'],0)
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'uncertain')
        asyncio.run(run())
    def test_quote_expiry_after_commit_before_write_keeps_hold_without_send(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            original=integration._BudgetSocket._commit;committed=threading.Event();release=threading.Event()
            def pause(*args):
                result=original(*args)
                if args[1]=='audio':
                    committed.set()
                    if not release.wait(5):raise RuntimeError('test_timeout')
                return result
            with patch.object(integration._BudgetSocket,'_commit',pause):
                task=asyncio.create_task(wire.send({'type':'input_audio_buffer.append','audio':'AAA='}))
                self.assertTrue(await asyncio.to_thread(committed.wait,3))
                with patch.object(budget,'QUOTE_EXPIRES',datetime.now(timezone.utc)-timedelta(seconds=1)):
                    release.set()
                    with self.assertRaisesRegex(Exception,'quote_expired'):await task
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchone()['n'],1)
                self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
        asyncio.run(run())
    def test_expiry_after_final_sql_check_can_still_commit_but_prewrite_stops(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            original=budget._quote_fresh_db;checked=threading.Event();release=threading.Event();calls=0
            def pause(cur):
                nonlocal calls
                original(cur);calls+=1
                if calls==2:
                    checked.set()
                    if not release.wait(5):raise RuntimeError('test_timeout')
            with patch.object(budget,'_quote_fresh_db',pause):
                task=asyncio.create_task(wire.send({'type':'input_audio_buffer.append','audio':'AAA='}))
                self.assertTrue(await asyncio.to_thread(checked.wait,3))
                with patch.object(budget,'QUOTE_EXPIRES',datetime.now(timezone.utc)-timedelta(seconds=1)):
                    release.set()
                    with self.assertRaisesRegex(Exception,'quote_expired'):await task
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchone()['n'],1)
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'uncertain')
        asyncio.run(run())
    def test_mismatched_response_scope_never_dispatches(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            scope,receipt=self.claim(s);scope['actor_id']+=1;receipt['actor_id']=scope['actor_id']
            with self.assertRaisesRegex(ValueError,'response_scope_invalid'):await wire.bind_claim(scope,receipt)
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'claimed')
        asyncio.run(run())
    def test_mismatched_response_ordinal_rolls_back_dispatch_and_sends_nothing(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            scope,receipt=self.claim(s);scope['response_number']=2;receipt['response_number']=2
            await wire.bind_claim(scope,receipt)
            with self.assertRaisesRegex(ValueError,'response_ordinal_mismatch'):await wire.send({'type':'response.create'})
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'uncertain')
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='response'").fetchone()['n'],0)
        asyncio.run(run())
    def test_audio_cumulative_limit_and_reused_receipt_never_retransmits(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            with local_connect(self.dsn) as conn:
                conn.execute('ALTER TABLE saas_voice_session_budget DISABLE TRIGGER voice_session_guard')
                conn.execute('UPDATE saas_voice_session_budget SET audio_bytes=5759998,audio_frames=1199')
                conn.execute('ALTER TABLE saas_voice_session_budget ENABLE TRIGGER voice_session_guard')
            with patch.object(integration.uuid,'uuid4',return_value=uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')):
                await wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
                with self.assertRaises(ValueError):await wire.send({'type':'input_audio_buffer.append','audio':'AQI='})
            self.assertEqual(len(wire.socket.evidence()),3)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT audio_bytes,audio_frames FROM saas_voice_session_budget').fetchone(),{'audio_bytes':5760000,'audio_frames':1200})
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchone()['n'],1)
        asyncio.run(run())
    def test_ambiguous_commit_never_sends_and_retains_full_hold(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            original=integration._BudgetSocket._commit
            def lost(*args):
                result=original(*args)
                if args[1]=='audio':raise ConnectionError('synthetic_lost_commit_ack')
                return result
            with patch.object(integration._BudgetSocket,'_commit',lost):
                with self.assertRaises(ConnectionError):await wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchone()['n'],1)
        asyncio.run(run())
    def test_audio_new_receipt_cannot_exceed_cumulative_limit(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            with local_connect(self.dsn) as conn:
                conn.execute('ALTER TABLE saas_voice_session_budget DISABLE TRIGGER voice_session_guard')
                conn.execute('UPDATE saas_voice_session_budget SET audio_bytes=5759998,audio_frames=1199')
                conn.execute('ALTER TABLE saas_voice_session_budget ENABLE TRIGGER voice_session_guard')
            await wire.send({'type':'input_audio_buffer.append','audio':'AAA='})
            with self.assertRaises(Exception):await wire.send({'type':'input_audio_buffer.append','audio':'AQI='})
            self.assertEqual(len(wire.socket.evidence()),3)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT audio_bytes,audio_frames,state FROM saas_voice_session_budget').fetchone(),{'audio_bytes':5760000,'audio_frames':1200,'state':'uncertain'})
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchone()['n'],1)
        asyncio.run(run())
    def test_concurrent_distinct_audio_each_has_exactly_one_matching_receipt(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            await asyncio.gather(*(wire.send({'type':'input_audio_buffer.append','audio':base64.b64encode(x).decode()}) for x in (b'\x01\x02'*100,b'\x03\x04'*250)))
            frames=wire.socket.evidence()[2:];self.assertEqual(len(frames),2)
            with local_connect(self.dsn) as conn:
                rows=conn.execute("SELECT wire_sha256,wire_bytes FROM saas_voice_bound_sender_receipt WHERE operation='audio'").fetchall()
                self.assertEqual({(x['wire_sha256'],x['wire_bytes']) for x in rows},{(hashlib.sha256(x).hexdigest(),len(x)) for x in frames})
                self.assertEqual(conn.execute('SELECT audio_bytes,audio_frames FROM saas_voice_session_budget').fetchone(),{'audio_bytes':700,'audio_frames':2})
        asyncio.run(run())
    def test_cancel_response_after_commit_and_before_write_never_retries(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);await wire.open(SNAPSHOT,**OPTIONS)
            claim,receipt=self.claim(s);await wire.bind_claim(claim,receipt)
            original=integration._BudgetSocket._commit;committed=threading.Event();release=threading.Event()
            def pause(*args):
                result=original(*args)
                if args[1]=='response':
                    committed.set()
                    if not release.wait(5):raise RuntimeError('test_timeout')
                return result
            with patch.object(integration._BudgetSocket,'_commit',pause):
                task=asyncio.create_task(wire.send({'type':'response.create'}))
                self.assertTrue(await asyncio.to_thread(committed.wait,3));task.cancel()
                try:
                    with self.assertRaises(asyncio.CancelledError):await task
                finally:release.set()
                await asyncio.sleep(.01)
            self.assertEqual(len(wire.socket.evidence()),2)
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'uncertain')
                self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='response'").fetchone()['n'],1)
        asyncio.run(run())
    def test_two_executors_cannot_open_same_session(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            with patch.object(integration,'_FakeSocket',wraps=integration._FakeSocket) as fake:
                result=await asyncio.gather(integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT),integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT),return_exceptions=True)
                self.assertEqual(sum(isinstance(x,integration.BoundSyntheticWire) for x in result),1)
                self.assertEqual(fake.call_count,1)
        asyncio.run(run())
    def test_cancel_after_open_hold_commit_creates_no_socket_and_never_reopens(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            committed=threading.Event();release=threading.Event();original=integration._consume_open
            def pause(*args):
                result=original(*args);committed.set()
                if not release.wait(5):raise RuntimeError('test_timeout')
                return result
            with patch.object(integration,'_consume_open',pause),patch.object(integration,'_FakeSocket',wraps=integration._FakeSocket) as fake:
                task=asyncio.create_task(integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT))
                self.assertTrue(await asyncio.to_thread(committed.wait,3));task.cancel()
                try:
                    with self.assertRaises(asyncio.CancelledError):await task
                    fake.assert_not_called()
                finally:release.set()
                await asyncio.sleep(.01)
                with self.assertRaisesRegex(ValueError,'open_capability_unavailable'):await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
                fake.assert_not_called()
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'opening')
        asyncio.run(run())
    def test_caller_scope_mutation_after_open_commit_does_not_rebind_socket(self):
        async def run():
            s=scoped();original_scope=copy.deepcopy(s);self.call(budget.reserve_session,s)
            committed=threading.Event();release=threading.Event();original=integration._consume_open
            def pause(*args):
                result=original(*args);committed.set()
                if not release.wait(5):raise RuntimeError('test_timeout')
                return result
            with patch.object(integration,'_consume_open',pause):
                task=asyncio.create_task(integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT))
                self.assertTrue(await asyncio.to_thread(committed.wait,3))
                s['actor_id']+=1;s['source_fingerprint']='b'*64;release.set()
                wire=await task
            await wire.open(SNAPSHOT,**OPTIONS)
            with local_connect(self.dsn) as conn:
                rows=conn.execute('SELECT scope_fingerprint FROM saas_voice_bound_sender_receipt').fetchall()
                self.assertEqual({r['scope_fingerprint'] for r in rows},{budget._fingerprint(original_scope)})
        asyncio.run(run())
    def test_unsafe_provider_ack_never_sends_context_and_marks_uncertain(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);original=integration._FakeSocket.read
            async def bad(socket):
                event=await original(socket)
                if event['type']=='session.updated':event['session']['audio']['input']['transcription']={'model':'gpt-transcribe'}
                return event
            with patch.object(integration._FakeSocket,'read',bad):
                with self.assertRaises(ValueError):await wire.open(SNAPSHOT,**OPTIONS)
            self.assertEqual([json.loads(x)['type'] for x in wire.socket.evidence()],['session.update'])
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'uncertain')
        asyncio.run(run())
    def test_conversation_announcement_before_config_or_context_ack(self):
        async def run():
            original=integration._FakeSocket.read
            for desired in ('session.update','conversation.item.create'):
                s=scoped(response_limit=1);self.call(budget.reserve_session,s)
                wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);announced=False
                async def interleaved(socket):
                    nonlocal announced
                    if socket.frames and not announced and json.loads(socket.frames[-1])['type']==desired:
                        announced=True
                        return {'type':'conversation.created','session_id':'sess_fake','conversation':{'id':'conv_fake'}}
                    return await original(socket)
                with patch.object(integration._FakeSocket,'read',interleaved):await wire.open(SNAPSHOT,**OPTIONS)
                self.assertTrue(announced);self.assertTrue(wire.ready)
                self.assertEqual(wire.conversation_id,'conv_fake')
        asyncio.run(run())
    def test_foreign_conversation_announcement_does_not_advance_or_send_context(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT);original=integration._FakeSocket.read
            async def foreign(socket):
                if socket.frames:return {'type':'conversation.created','session_id':'sess_other','conversation':{'id':'conv_fake'}}
                return await original(socket)
            with patch.object(integration._FakeSocket,'read',foreign):
                with self.assertRaisesRegex(ValueError,'provider_conversation_invalid'):await wire.open(SNAPSHOT,**OPTIONS)
            self.assertEqual(len(wire.socket.evidence()),1)
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'uncertain')
        asyncio.run(run())
    def test_concurrent_config_cannot_be_sent_twice(self):
        async def run():
            s=scoped();self.call(budget.reserve_session,s)
            wire=await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
            config=good_config()
            results=await asyncio.gather(wire.socket.send_json(config),wire.socket.send_json(config),return_exceptions=True)
            self.assertEqual(sum(isinstance(x,Exception) for x in results),1)
            self.assertEqual(len(wire.socket.evidence()),1)
            with local_connect(self.dsn) as conn:self.assertEqual(conn.execute("SELECT count(*) AS n FROM saas_voice_bound_sender_receipt WHERE operation='config'").fetchone()['n'],1)
        asyncio.run(run())
    def test_death_after_config_receipt_prevents_reconnect(self):
        s=scoped();self.call(budget.reserve_session,s)
        root=Path(__file__).resolve().parents[2]
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s),'PYTHONPATH':':'.join(str(root/x) for x in ('beintaskbot-main','experiments/voice_budget_poc','experiments/voice_session_budget_poc','experiments/voice_bound_sender_poc','experiments/voice_budget_wire_poc'))}
        script='''import asyncio,json,os
from unittest.mock import patch
import bound_wire as w,session_budget as b,tenant_ai_spend_budget as spending
class G:
 async def consume(self,*args):return True
s=json.loads(os.environ['POC_SCOPE'])
original=w._BudgetSocket._commit
def crash(*args):
 result=original(*args)
 if args[1]=='config':os._exit(75)
 return result
async def main():
 with patch.object(w,'ENABLED_SYNTHETIC',True),patch.object(b,'ADMISSION_VERIFIED',True),patch.object(b,'RECONCILIATION_VERIFIED',True),patch.object(b,'CONFIG_ATTESTATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0),patch.object(w._BudgetSocket,'_commit',crash):
  snapshot={'existing_copilot_instructions':'Never send messages, call tools or perform external actions.','lines':[],'extra_context':{}}
  wire=await w.connect_synthetic(os.environ['POC_DSN'],s,G(),snapshot)
  await wire.open(snapshot,tools=[],create_response=False,interrupt_response=False,transcription=False,reconnect=False)
asyncio.run(main())
'''
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,75,result.stderr)
        async def retry():
            with self.assertRaisesRegex(ValueError,'open_capability_unavailable'):await integration.connect_synthetic(self.dsn,s,Guard(),SNAPSHOT)
        asyncio.run(retry())
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT operation FROM saas_voice_bound_sender_receipt').fetchone()['operation'],'config')
            self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'opening','reserved_microusd':4_224_000})

def load_tests(loader,tests,pattern):
    import unittest
    return unittest.TestSuite(WireBudgetTests(name) for name in sorted(WireBudgetTests.__dict__) if name.startswith('test_'))
