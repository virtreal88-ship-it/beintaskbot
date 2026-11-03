"""Isolated synthetic sockets + real disposable PostgreSQL only."""
import asyncio
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
import test_session_budget as base
import sender

SCHEMA=(Path(__file__).parent/'sender_schema.sql').read_text()

class BoundSenderTests(base.SessionBudgetTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with local_connect(cls.dsn) as conn:conn.execute(SCHEMA)
    def setUp(self):
        with local_connect(self.dsn) as conn:
            conn.execute('TRUNCATE saas_voice_bound_sender_receipt,saas_voice_session_audio,saas_voice_session_response,saas_voice_session_budget,saas_voice_test_hold,saas_ai_spend_reservations')
            conn.execute('ALTER TABLE saas_voice_test_grant DISABLE TRIGGER voice_test_block_enable')
            conn.execute('UPDATE saas_voice_test_grant SET enabled=true,frozen=false')
            conn.execute('ALTER TABLE saas_voice_test_grant ENABLE TRIGGER voice_test_block_enable')
        import tenant_ai_spend_budget as spend
        for target,name,value in ((budget,'ADMISSION_VERIFIED',True),(budget,'RECONCILIATION_VERIFIED',True),
                                  (budget,'CONFIG_ATTESTATION_VERIFIED',True),(spend,'limit_microusd',lambda _:0),
                                  (sender,'SYNTHETIC_ENABLED',True)):
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)
    def make(self,scope,mode='normal'):return sender.SyntheticBoundSender(self.dsn,scope,mode)
    def test_bytes_are_exactly_the_committed_wire_digest_no_raw_method(self):
        async def run():
            s=base.scope();self.ready(s);chunk=base.uid();pcm=b'\x11\x22'*300
            out=self.make(s)
            with self.assertRaisesRegex(ValueError,'immutable_bytes'):await out.audio(chunk,bytearray(pcm))
            self.assertTrue(await out.audio(chunk,pcm))
            frame=out.evidence()[0];self.assertEqual(json.loads(frame)['audio'],__import__('base64').b64encode(pcm).decode())
            self.assertFalse(hasattr(out,'send_json'));self.assertFalse(hasattr(out,'socket'))
            with local_connect(self.dsn) as conn:
                receipt=conn.execute('SELECT * FROM saas_voice_bound_sender_receipt WHERE operation_id=%s',(chunk,)).fetchone()
                self.assertEqual((receipt['wire_sha256'],receipt['wire_bytes']),(hashlib.sha256(frame).hexdigest(),len(frame)))
                self.assertEqual(receipt['scope_fingerprint'],budget._fingerprint(s))
                with self.assertRaises(Exception):conn.execute("UPDATE saas_voice_bound_sender_receipt SET wire_bytes=1")
            self.assertFalse(await out.audio(chunk,b'\x44\x55'*300))
            self.assertEqual(len(out.evidence()),1)
        asyncio.run(run())
    def test_cross_executor_audio_race_one_write_and_one_durable_receipt(self):
        async def run():
            s=base.scope();self.ready(s);chunk=base.uid();a,b=self.make(s),self.make(s)
            result=await asyncio.gather(a.audio(chunk,b'\0\1'*500),b.audio(chunk,b'\2\3'*500))
            self.assertEqual(sorted(result),[False,True])
            frames=a.evidence()+b.evidence();self.assertEqual(len(frames),1)
            with local_connect(self.dsn) as conn:
                row=conn.execute('SELECT wire_sha256 FROM saas_voice_bound_sender_receipt').fetchone()
                self.assertEqual(row['wire_sha256'],hashlib.sha256(frames[0]).hexdigest())
        asyncio.run(run())
    def test_response_exact_scope_and_cross_executor_one_send(self):
        async def run():
            s=base.scope();self.ready(s);response=base.uid();self.call(budget.claim_response,s,response,1)
            a,b=self.make(s),self.make(s)
            self.assertEqual(sorted(await asyncio.gather(a.response(response),b.response(response))),[False,True])
            frame=(a.evidence()+b.evidence())[0]
            self.assertEqual(frame,b'{"type":"response.create"}')
            with local_connect(self.dsn) as conn:
                row=conn.execute('SELECT operation,scope_fingerprint,wire_sha256 FROM saas_voice_bound_sender_receipt').fetchone()
                self.assertEqual(row,{'operation':'response','scope_fingerprint':budget._fingerprint(s),'wire_sha256':hashlib.sha256(frame).hexdigest()})
        asyncio.run(run())
    def test_internal_mismatch_cannot_create_receipt_or_consume_allowance(self):
        s=base.scope();self.ready(s);out=self.make(s);chunk=base.uid();response=base.uid()
        self.call(budget.claim_response,s,response,1)
        bad=sender._wire({'type':'input_audio_buffer.append','audio':'AQI='})
        with self.assertRaisesRegex(ValueError,'wire_pcm_mismatch'):out._commit('audio',chunk,bad,b'\x03\x04')
        with self.assertRaisesRegex(ValueError,'wire_response_mismatch'):
            out._commit('response',response,b'{"type":"response.create","response":{"tools":[1]}}')
        self.assertEqual(out.evidence(),())
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_bound_sender_receipt').fetchone()['n'],0)
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'claimed')
            self.assertEqual(conn.execute('SELECT audio_bytes FROM saas_voice_session_budget').fetchone()['audio_bytes'],0)
    def test_wrong_scope_cannot_consume_response_or_write(self):
        async def run():
            s=base.scope();self.ready(s);response=base.uid();self.call(budget.claim_response,s,response,1)
            out=self.make({**s,'source_fingerprint':'b'*64})
            with self.assertRaises(Exception):await out.response(response)
            self.assertEqual(out.evidence(),())
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'claimed')
        asyncio.run(run())
    def test_timeout_after_possible_send_poisoned_no_retry_and_full_hold(self):
        async def run():
            s=base.scope();self.ready(s);chunk=base.uid();out=self.make(s,'timeout')
            with self.assertRaises(TimeoutError):await out.audio(chunk,b'\0\1'*40)
            self.assertEqual(len(out.evidence()),1)
            with self.assertRaisesRegex(RuntimeError,'sender_poisoned'):await out.audio(base.uid(),b'\0\1')
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_bound_sender_receipt').fetchone()['n'],1)
        asyncio.run(run())
    def test_partial_error_never_retries_on_fresh_sender(self):
        async def run():
            s=base.scope();self.ready(s);chunk=base.uid();out=self.make(s,'partial_error')
            with self.assertRaises(ConnectionError):await out.audio(chunk,b'\0\1'*40)
            other=self.make(s)
            self.assertFalse(await other.audio(chunk,b'\0\1'*40));self.assertEqual(other.evidence(),())
        asyncio.run(run())
    def test_cancellation_after_possible_write_poisons_and_retains_hold(self):
        async def run():
            s=base.scope();self.ready(s);chunk=base.uid();out=self.make(s,'timeout')
            task=asyncio.create_task(out.audio(chunk,b'\0\1'*40))
            for _ in range(100):
                if out.evidence():break
                await asyncio.sleep(0.001)
            self.assertEqual(len(out.evidence()),1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            with self.assertRaisesRegex(RuntimeError,'sender_poisoned'):await out.audio(base.uid(),b'\0\1')
            with local_connect(self.dsn) as conn:
                self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
        asyncio.run(run())
    def test_cancel_after_database_commit_before_sender_resumes_never_writes(self):
        async def run():
            s=base.scope();self.ready(s);chunk=base.uid();out=self.make(s)
            committed=threading.Event();release=threading.Event();original=sender.SyntheticBoundSender._commit
            def delayed(instance,*args,**kwargs):
                result=original(instance,*args,**kwargs);committed.set()
                if not release.wait(5):raise RuntimeError('test_barrier_timeout')
                return result
            with patch.object(sender.SyntheticBoundSender,'_commit',delayed):
                task=asyncio.create_task(out.audio(chunk,b'\0\1'*40))
                self.assertTrue(await asyncio.to_thread(committed.wait,3))
                task.cancel()
                try:
                    with self.assertRaises(asyncio.CancelledError):await task
                    self.assertEqual(out.evidence(),())
                    with local_connect(self.dsn) as conn:
                        self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
                        self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_bound_sender_receipt').fetchone()['n'],1)
                finally:release.set()
            await asyncio.sleep(0.01)
            self.assertEqual(out.evidence(),())
        asyncio.run(run())
    def test_process_dies_after_committed_receipt_before_any_send(self):
        s=base.scope();self.ready(s);chunk=base.uid()
        root=Path(__file__).resolve().parents[2]
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s),'POC_CHUNK':chunk,
             'PYTHONPATH':str(root/'beintaskbot-main')+':'+str(root/'experiments'/'voice_budget_poc')+':'+str(root/'experiments'/'voice_session_budget_poc')+':'+str(Path(__file__).parent)}
        script='''import json,os
from unittest.mock import patch
import sender,session_budget as b,tenant_ai_spend_budget as spending
s=json.loads(os.environ['POC_SCOPE']);pcm=b'\\0\\1'*40
with patch.object(sender,'SYNTHETIC_ENABLED',True),patch.object(b,'ADMISSION_VERIFIED',True),patch.object(b,'RECONCILIATION_VERIFIED',True),patch.object(b,'CONFIG_ATTESTATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0):
 out=sender.SyntheticBoundSender(os.environ['POC_DSN'],s)
 frame=sender._wire({'type':'input_audio_buffer.append','audio':__import__('base64').b64encode(pcm).decode()})
 assert out._commit('audio',os.environ['POC_CHUNK'],frame,pcm)
 os._exit(73)
'''
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,73,result.stderr)
        async def retry():
            out=self.make(s);self.assertFalse(await out.audio(chunk,b'\0\1'*40));self.assertEqual(out.evidence(),())
        asyncio.run(retry())
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT reserved_microusd FROM saas_voice_session_budget').fetchone()['reserved_microusd'],4_224_000)
    def test_process_dies_after_response_receipt_before_any_send(self):
        s=base.scope();self.ready(s);response=base.uid();self.call(budget.claim_response,s,response,1)
        root=Path(__file__).resolve().parents[2]
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s),'POC_RESPONSE':response,
             'PYTHONPATH':str(root/'beintaskbot-main')+':'+str(root/'experiments'/'voice_budget_poc')+':'+str(root/'experiments'/'voice_session_budget_poc')+':'+str(Path(__file__).parent)}
        script='''import json,os
from unittest.mock import patch
import sender,session_budget as b,tenant_ai_spend_budget as spending
s=json.loads(os.environ['POC_SCOPE'])
with patch.object(sender,'SYNTHETIC_ENABLED',True),patch.object(b,'ADMISSION_VERIFIED',True),patch.object(b,'RECONCILIATION_VERIFIED',True),patch.object(b,'CONFIG_ATTESTATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0):
 out=sender.SyntheticBoundSender(os.environ['POC_DSN'],s)
 assert out._commit('response',os.environ['POC_RESPONSE'],sender._wire({'type':'response.create'}))
 os._exit(74)
'''
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,74,result.stderr)
        async def retry():
            out=self.make(s);self.assertFalse(await out.response(response));self.assertEqual(out.evidence(),())
        asyncio.run(retry())
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'dispatching')


def load_tests(loader,tests,pattern):
    # Parent helpers are reused; the base suite runs separately in its own DB.
    import unittest
    return unittest.TestSuite(BoundSenderTests(name) for name in sorted(BoundSenderTests.__dict__) if name.startswith('test_'))
