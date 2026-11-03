"""Local PostgreSQL tests; no provider requests, credentials, or production."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock,patch
from urllib.parse import urlsplit
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import local_connect,reject_libpq_env
import tenant_ai_spend_budget as spend
from tenant_linear_policy import TenantLinearError
import session_budget as budget

ROOT=Path(__file__).resolve().parents[1]
BASE=(ROOT/'voice_budget_poc'/'voice_test_grant.sql').read_text()
UP=(ROOT/'voice_budget_poc'/'migration_dispatch_v2.sql').read_text()
DOWN=(ROOT/'voice_budget_poc'/'rollback_dispatch_v1.sql').read_text()
SCHEMA=(Path(__file__).parent/'schema.sql').read_text()


def uid():return str(uuid.uuid4())


def scope(**changes):
    value={'session_id':uid(),'tenant_id':uid(),'actor_id':1,'channel_id':uid(),'conversation_id':'synthetic-dialog',
           'executor':'synthetic','source_fingerprint':'a'*64,'response_limit':2,'asr_mode':'off'}
    return {**value,**changes}


def event():
    return {'type':'session.updated','session':{'id':'sess_synthetic','type':'realtime','model':budget.MODEL,
      'output_modalities':['audio'],'tools':[],'tool_choice':'none','max_output_tokens':4096,
      'audio':{'input':{'format':{'type':'audio/pcm','rate':24000},'transcription':None,
                         'turn_detection':{'type':'server_vad','create_response':False,'interrupt_response':False,'idle_timeout_ms':None}},
               'output':{'format':{'type':'audio/pcm','rate':24000}}}}}


class SessionBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reject_libpq_env();dsn=os.environ.get('VOICE_BUDGET_POC_ADMIN_URL','');p=urlsplit(dsn);pg=conninfo_to_dict(dsn)
        if (p.scheme not in ('postgres','postgresql') or p.query or p.fragment or p.path!='/postgres' or
            p.hostname not in ('localhost','127.0.0.1','::1') or pg.get('host') not in ('localhost','127.0.0.1','::1')):
            raise RuntimeError('localhost_admin_only')
        cls.base=p;cls.name='test_voice_budget_'+uuid.uuid4().hex
        cls.admin=psycopg.connect(dsn,hostaddr='::1' if p.hostname=='::1' else '127.0.0.1',autocommit=True)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.dsn=p._replace(path='/'+cls.name).geturl()
        with local_connect(cls.dsn) as conn:
            spend.ensure_schema(conn);conn.execute(BASE);conn.execute(UP);conn.execute(SCHEMA);conn.execute(SCHEMA)

    @classmethod
    def tearDownClass(cls):
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)));cls.admin.close()

    def setUp(self):
        with local_connect(self.dsn) as conn:
            # Test-only TRUNCATE bypasses row-level no-delete guards.
            conn.execute('TRUNCATE saas_voice_session_audio,saas_voice_session_response,saas_voice_session_budget,saas_voice_test_hold,saas_ai_spend_reservations')
            conn.execute('ALTER TABLE saas_voice_test_grant DISABLE TRIGGER voice_test_block_enable')
            conn.execute('UPDATE saas_voice_test_grant SET enabled=true,frozen=false')
            conn.execute('ALTER TABLE saas_voice_test_grant ENABLE TRIGGER voice_test_block_enable')
        for target,name,value in ((budget,'ADMISSION_VERIFIED',True),(budget,'RECONCILIATION_VERIFIED',True),
                                  (budget,'CONFIG_ATTESTATION_VERIFIED',True),(spend,'limit_microusd',lambda _:0)):
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)

    def call(self,fn,*args):
        with local_connect(self.dsn) as conn,conn.cursor() as cur:return fn(cur,*args)

    def ready(self,s):
        self.call(budget.reserve_session,s)
        self.call(budget.issue_open,s)
        self.assertEqual(self.call(budget.accept_config,s,event(),'sess_synthetic')['state'],'ready')

    def test_default_closed_and_unknown_asr_rejected_before_any_sql(self):
        cur=Mock()
        with patch.object(budget,'ADMISSION_VERIFIED',False):
            with self.assertRaisesRegex(TenantLinearError,'enforcement_unverified'):budget.reserve_session(cur,scope())
        with self.assertRaisesRegex(TenantLinearError,'asr_bound_unverified'):budget.reserve_session(cur,scope(asr_mode='gpt-transcribe'))
        cur.execute.assert_not_called()

    def test_single_global_cap_concurrent_executors_two_one_response_sessions(self):
        requests=[scope(response_limit=1,executor='worker_'+str(n)) for n in range(24)]
        def admit(s):
            try:return self.call(budget.reserve_session,s)['state']
            except TenantLinearError as exc:return str(exc)
        with ThreadPoolExecutor(max_workers=12) as workers:results=list(workers.map(admit,requests))
        self.assertEqual(results.count('reserved'),2,results)
        self.assertEqual(sum('budget_exhausted' in x for x in results),22,results)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS held FROM saas_voice_session_budget').fetchone()
            self.assertEqual(row,{'n':2,'held':4_224_000})
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations').fetchone()['n'],2)

    def test_two_response_session_is_full_hold_before_open_and_not_double_counted(self):
        s=scope();row=self.call(budget.reserve_session,s)
        self.assertEqual((row['state'],row['reserved_microusd']),('reserved',4_224_000))
        with self.assertRaisesRegex(TenantLinearError,'budget_exhausted'):self.call(budget.reserve_session,scope(response_limit=1))
        self.assertEqual(self.call(budget.issue_open,s)['state'],'opening')
        self.assertEqual(self.call(budget.accept_config,s,event(),'sess_synthetic')['state'],'ready')
        rid=uid();self.assertEqual(self.call(budget.claim_response,s,rid,1)['state'],'claimed')
        self.assertEqual(self.call(budget.issue_response,s,rid)['state'],'dispatching')
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS held FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(row,{'n':1,'held':4_224_000})

    def test_scope_and_active_session_dedupe(self):
        s=scope();self.call(budget.reserve_session,s)
        self.assertEqual(self.call(budget.reserve_session,s)['session_id'],uuid.UUID(s['session_id']))
        with self.assertRaisesRegex(TenantLinearError,'scope_conflict'):self.call(budget.reserve_session,{**s,'source_fingerprint':'b'*64})
        with self.assertRaisesRegex(TenantLinearError,'already_active'):self.call(budget.reserve_session,{**s,'session_id':uid()})

    def test_open_is_one_use_cross_executor_and_crash_keeps_whole_hold(self):
        s=scope();self.call(budget.reserve_session,s)
        def issue(_):return self.call(budget.issue_open,s)
        with ThreadPoolExecutor(max_workers=12) as workers:out=list(workers.map(issue,range(24)))
        self.assertEqual(sum(r is not None for r in out),1)
        script='''import json,os
from unittest.mock import patch
from ledger import local_connect
import session_budget as b
import tenant_ai_spend_budget as spending
s=json.loads(os.environ['POC_SCOPE'])
with patch.object(b,'ADMISSION_VERIFIED',True),patch.object(b,'RECONCILIATION_VERIFIED',True),patch.object(b,'CONFIG_ATTESTATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0):
    with local_connect(os.environ['POC_DSN']) as conn,conn.cursor() as cur:
        assert b.issue_open(cur,s) is None
print('blocked')
'''
        root=Path(__file__).resolve().parents[2]
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s),
             'PYTHONPATH':str(root/'beintaskbot-main')+':'+str(root/'experiments'/'voice_budget_poc')+':'+str(Path(__file__).parent)}
        result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'blocked')
        self.assertEqual(self.call(budget.mark_uncertain,s)['state'],'uncertain')
        self.assertIsNone(self.call(budget.cancel_before_open,s))
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'uncertain','reserved_microusd':4_224_000})
            self.assertEqual(conn.execute('SELECT state FROM saas_ai_spend_reservations').fetchone()['state'],'unknown')

    def test_process_death_after_open_commit_cannot_reopen(self):
        s=scope();self.call(budget.reserve_session,s)
        root=Path(__file__).resolve().parents[2]
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s),
             'PYTHONPATH':str(root/'beintaskbot-main')+':'+str(root/'experiments'/'voice_budget_poc')+':'+str(Path(__file__).parent)}
        crash='''import json,os
from unittest.mock import patch
from ledger import local_connect
import session_budget as b
import tenant_ai_spend_budget as spending
s=json.loads(os.environ['POC_SCOPE'])
with patch.object(b,'ADMISSION_VERIFIED',True),patch.object(b,'RECONCILIATION_VERIFIED',True),patch.object(b,'CONFIG_ATTESTATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0):
    with local_connect(os.environ['POC_DSN']) as conn,conn.cursor() as cur:
        assert b.issue_open(cur,s)['state']=='opening'
    os._exit(71)
'''
        r=subprocess.run([sys.executable,'-c',crash],env=env,capture_output=True,text=True)
        self.assertEqual(r.returncode,71,r.stderr)
        self.assertIsNone(self.call(budget.issue_open,s));self.assertIsNone(self.call(budget.cancel_before_open,s))
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'opening','reserved_microusd':4_224_000})

    def test_bad_or_unpriced_config_goes_uncertain_and_never_forwards(self):
        mutations=[lambda e:e['session']['audio']['input'].__setitem__('transcription',{'model':'gpt-transcribe'}),
                   lambda e:e['session']['audio']['input']['turn_detection'].__setitem__('create_response',True),
                   lambda e:e['session']['audio']['input']['turn_detection'].__setitem__('idle_timeout_ms',5000),
                   lambda e:e['session'].__setitem__('max_output_tokens',32000),
                   lambda e:e['session'].__setitem__('tools',[{'type':'function'}]),
                   lambda e:e['session'].__setitem__('model','different'),lambda e:e['session'].__setitem__('id','sess_other')]
        for mutate in mutations:
            s=scope(response_limit=1);self.call(budget.reserve_session,s);self.call(budget.issue_open,s)
            bad=event();mutate(bad)
            self.assertEqual(self.call(budget.accept_config,s,bad,'sess_synthetic')['state'],'uncertain')
            self.assertIsNone(self.call(budget.issue_audio,s,uid(),b'\0\0'))
            self.assertIsNone(self.call(budget.claim_response,s,uid(),1))
            with local_connect(self.dsn) as conn:
                conn.execute('TRUNCATE saas_voice_session_audio,saas_voice_session_response,saas_voice_session_budget,saas_voice_test_hold,saas_ai_spend_reservations')

    def test_audio_once_cumulative_caps_and_no_raw_audio_persisted(self):
        s=scope();self.ready(s);chunk=uid()
        with ThreadPoolExecutor(max_workers=12) as workers:rows=list(workers.map(lambda _:self.call(budget.issue_audio,s,chunk,b'\x01\x02'*4800),range(20)))
        self.assertEqual(sum(r is not None for r in rows),1)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT audio_bytes,audio_frames FROM saas_voice_session_budget').fetchone()
            self.assertEqual(row,{'audio_bytes':9600,'audio_frames':1})
            cols=conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='saas_voice_session_audio'").fetchall()
            self.assertEqual({r['column_name'] for r in cols},{'chunk_id','session_id','byte_count','created_at'})
            # Push limits using privileged synthetic fixture only.
            conn.execute('ALTER TABLE saas_voice_session_budget DISABLE TRIGGER voice_session_guard')
            conn.execute('UPDATE saas_voice_session_budget SET audio_bytes=5759998,audio_frames=1199')
            conn.execute('ALTER TABLE saas_voice_session_budget ENABLE TRIGGER voice_session_guard')
        self.assertIsNotNone(self.call(budget.issue_audio,s,uid(),b'\0\0'))
        with self.assertRaisesRegex(TenantLinearError,'audio_session_limit'):self.call(budget.issue_audio,s,uid(),b'\0\0')
        with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_session_audio').fetchone()['n'],2)

    def test_response_claim_is_single_use_and_unknown_disallows_more(self):
        s=scope();self.ready(s);rid=uid()
        with ThreadPoolExecutor(max_workers=12) as workers:rows=list(workers.map(lambda _:self.call(budget.claim_response,s,rid,1),range(20)))
        self.assertEqual(sum(r is not None for r in rows),1)
        with ThreadPoolExecutor(max_workers=12) as workers:issued=list(workers.map(lambda _:self.call(budget.issue_response,s,rid),range(20)))
        self.assertEqual(sum(r is not None for r in issued),1)
        with self.assertRaisesRegex(TenantLinearError,'receipt_unverified'):self.call(budget.record_response_complete,s,rid,'resp_synthetic')
        self.assertEqual(self.call(budget.mark_uncertain,s)['state'],'uncertain')
        self.assertIsNone(self.call(budget.issue_response,s,rid));self.assertIsNone(self.call(budget.claim_response,s,uid(),2))
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'uncertain')
            self.assertEqual(conn.execute('SELECT reserved_microusd FROM saas_voice_session_budget').fetchone()['reserved_microusd'],4_224_000)

    def test_two_subresponses_consume_one_session_hold_without_double_accounting(self):
        s=scope();self.ready(s);a,b=uid(),uid()
        self.call(budget.claim_response,s,a,1);self.call(budget.issue_response,s,a)
        with patch.object(budget,'PROVIDER_RECEIPTS_VERIFIED',True):
            self.assertEqual(self.call(budget.record_response_complete,s,a,'resp_one')['state'],'completed')
            self.assertEqual(self.call(budget.claim_response,s,b,2)['state'],'claimed')
            self.assertEqual(self.call(budget.issue_response,s,b)['state'],'dispatching')
            with self.assertRaises(psycopg.IntegrityError):self.call(budget.record_response_complete,s,b,'resp_one')
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS held FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(row,{'n':1,'held':4_224_000})

    def test_process_death_after_response_dispatch_commit_never_reissues(self):
        s=scope();self.ready(s);rid=uid();self.call(budget.claim_response,s,rid,1)
        root=Path(__file__).resolve().parents[2]
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s),'POC_RESPONSE':rid,
             'PYTHONPATH':str(root/'beintaskbot-main')+':'+str(root/'experiments'/'voice_budget_poc')+':'+str(Path(__file__).parent)}
        script='''import json,os
from unittest.mock import patch
from ledger import local_connect
import session_budget as b
import tenant_ai_spend_budget as spending
s=json.loads(os.environ['POC_SCOPE'])
with patch.object(b,'ADMISSION_VERIFIED',True),patch.object(b,'RECONCILIATION_VERIFIED',True),patch.object(b,'CONFIG_ATTESTATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0):
    with local_connect(os.environ['POC_DSN']) as conn,conn.cursor() as cur:
        assert b.issue_response(cur,s,os.environ['POC_RESPONSE'])['state']=='dispatching'
    os._exit(72)
'''
        r=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True)
        self.assertEqual(r.returncode,72,r.stderr)
        self.assertIsNone(self.call(budget.issue_response,s,rid))
        self.assertEqual(self.call(budget.mark_uncertain,s)['state'],'uncertain')
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT reserved_microusd FROM saas_voice_session_budget').fetchone()['reserved_microusd'],4_224_000)
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_response').fetchone()['state'],'uncertain')

    def test_session_overrun_after_kill_preserves_evidence_and_tenant_cap(self):
        s=scope(response_limit=1);self.ready(s)
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        with patch.object(budget,'ADMISSION_VERIFIED',False),patch.object(budget,'PROVIDER_RECEIPTS_VERIFIED',True):
            self.assertEqual(self.call(budget.record_session_actual,s,2_300_000)['state'],'uncertain')
            self.assertEqual(self.call(budget.record_session_actual,s,1)['actual_microusd'],2_300_000)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone();self.assertTrue(row['frozen'])
            hold=conn.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(hold,{'state':'unknown','reserved_microusd':2_300_000,'actual_microusd':None})
        with patch.object(spend,'limit_microusd',return_value=2_200_000):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    spend.reserve(cur,s['tenant_id'],'unrelated','text','synthetic',1,0,track_uncapped=True,_bounded_prices=(1_000_000,1_000_000))

    def test_unrelated_uncapped_budget_path_not_fenced(self):
        s=scope();self.ready(s)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:
            self.assertIsNone(spend.reserve(cur,s['tenant_id'],'other','text','synthetic'))
            self.assertIsNone(spend.reserve_media(cur,s['tenant_id'],'other-media','media','synthetic','asr',1))

    def test_conflicting_tenant_hold_keeps_global_evidence_and_blocks_only_voice(self):
        s=scope(response_limit=1);self.ready(s)
        with local_connect(self.dsn) as conn:
            conn.execute("UPDATE saas_ai_spend_reservations SET kind='other',state='released',actual_microusd=0 WHERE tenant_id=%s",(s['tenant_id'],))
        with patch.object(budget,'PROVIDER_RECEIPTS_VERIFIED',True):
            row=self.call(budget.record_session_actual,s,2_200_000)
            self.assertEqual((row['state'],row['accounting_conflict'],row['actual_microusd']),('uncertain',True,2_200_000))
        with local_connect(self.dsn) as conn:
            self.assertTrue(conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone()['frozen'])
            hold=conn.execute('SELECT kind,state,actual_microusd FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(hold,{'kind':'other','state':'released','actual_microusd':0})
        with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(spend.reserve(cur,s['tenant_id'],'unrelated-uncapped','text','synthetic'))

    def test_new_dispatch_detects_corrupt_hold_and_commits_voice_freeze(self):
        s=scope(response_limit=1);self.ready(s)
        with local_connect(self.dsn) as conn:
            conn.execute("UPDATE saas_ai_spend_reservations SET kind='other',state='released',actual_microusd=0 WHERE tenant_id=%s",(s['tenant_id'],))
        self.assertIsNone(self.call(budget.issue_audio,s,uid(),b'\0\0'))
        with local_connect(self.dsn) as conn:
            grant=conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone()
            row=conn.execute('SELECT state,accounting_conflict FROM saas_voice_session_budget').fetchone()
            self.assertTrue(grant['frozen']);self.assertEqual(row,{'state':'uncertain','accounting_conflict':True})
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_session_audio').fetchone()['n'],0)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(spend.reserve(cur,s['tenant_id'],'unrelated-after-conflict','text','synthetic'))

    def test_unknown_and_reconciliation_survive_kill_switch(self):
        s=scope();self.call(budget.reserve_session,s);self.call(budget.issue_open,s)
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        with patch.object(budget,'ADMISSION_VERIFIED',False):
            self.assertEqual(self.call(budget.mark_uncertain,s)['state'],'uncertain')
            with self.assertRaisesRegex(TenantLinearError,'enforcement_unverified'):self.call(budget.issue_open,s)
        self.assertIsNone(self.call(budget.cancel_before_open,s))

    def test_expired_quote_and_changed_tenant_cap_fail_closed(self):
        s=scope();self.call(budget.reserve_session,s)
        with patch('session_budget.datetime') as clock:
            clock.now.return_value=datetime(2026,11,4,tzinfo=timezone.utc)
            with self.assertRaisesRegex(TenantLinearError,'quote_expired'):self.call(budget.issue_open,s)
        with patch.object(spend,'limit_microusd',return_value=1):
            with self.assertRaisesRegex(TenantLinearError,'budget_exhausted'):self.call(budget.issue_open,s)
        with local_connect(self.dsn) as conn:self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'reserved')

    def test_versioned_migration_is_repeatable_and_rollback_refuses_session_schema(self):
        s=scope();self.call(budget.reserve_session,s)
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        def migrate(_):
            with local_connect(self.dsn) as conn:conn.execute(SCHEMA)
        with ThreadPoolExecutor(max_workers=6) as workers:list(workers.map(migrate,range(6)))
        with self.assertRaises(psycopg.IntegrityError):
            with local_connect(self.dsn) as conn:conn.execute(DOWN)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_voice_session_budget').fetchone(),{'state':'reserved','reserved_microusd':4_224_000})
            self.assertEqual(conn.execute("SELECT version FROM saas_voice_session_schema_version WHERE component='session'").fetchone()['version'],1)

    def test_migration_tampering_fails_without_overwriting_schema_or_data(self):
        s=scope();self.call(budget.reserve_session,s)
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        with local_connect(self.dsn) as conn:
            conn.execute('ALTER TABLE saas_voice_session_budget DISABLE TRIGGER voice_session_guard')
            with self.assertRaisesRegex(psycopg.IntegrityError,'voice_session_schema_mismatch'):conn.execute(SCHEMA)
            conn.rollback()  # reverses the test-only trigger modification
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state'],'reserved')
            self.assertEqual(conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgname='voice_session_guard'").fetchone()['tgenabled'],'O')
        with local_connect(self.dsn) as conn:
            conn.execute('ALTER TABLE saas_voice_session_budget ALTER COLUMN audio_bytes SET DEFAULT 100')
            with self.assertRaisesRegex(psycopg.IntegrityError,'voice_session_schema_mismatch'):conn.execute(SCHEMA)
            conn.rollback()

    def test_migration_lock_timeout_and_legacy_insert_barrier(self):
        with self.assertRaisesRegex(psycopg.IntegrityError,'voice_session_legacy_insert_blocked'):
            with local_connect(self.dsn) as conn:conn.execute('INSERT INTO saas_voice_test_hold DEFAULT VALUES')
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        with local_connect(self.dsn) as blocking,ThreadPoolExecutor(max_workers=1) as pool:
            blocking.execute('LOCK TABLE saas_voice_test_grant IN ROW EXCLUSIVE MODE')
            def migrate():
                with local_connect(self.dsn) as conn:conn.execute(SCHEMA)
            pending=pool.submit(migrate)
            with self.assertRaises(psycopg.errors.LockNotAvailable):pending.result(timeout=8)
            blocking.rollback()

    def test_generic_and_voice_reservations_compete_under_same_tenant_cap(self):
        s=scope();barrier=threading.Barrier(2)
        def voice():
            barrier.wait()
            try:self.call(budget.reserve_session,s);return 'voice'
            except TenantLinearError as exc:return str(exc)
        def generic():
            barrier.wait()
            try:
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    spend.reserve(cur,s['tenant_id'],'unrelated-text','text','synthetic',1,0,track_uncapped=True,_bounded_prices=(1_000_000_000_000,1))
                return 'generic'
            except TenantLinearError as exc:return str(exc)
        with patch.object(spend,'limit_microusd',return_value=5_000_000):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a,b=pool.submit(voice),pool.submit(generic);out=[a.result(timeout=8),b.result(timeout=8)]
        self.assertEqual(sum(x in ('voice','generic') for x in out),1,out)
        self.assertEqual(sum('budget_exhausted' in x for x in out),1,out)
        with local_connect(self.dsn) as conn:
            self.assertLessEqual(conn.execute('SELECT sum(reserved_microusd) AS n FROM saas_ai_spend_reservations').fetchone()['n'],5_000_000)

    def test_open_and_cancel_race_cannot_release_an_opening_session(self):
        s=scope();self.call(budget.reserve_session,s);barrier=threading.Barrier(2)
        def run(fn):barrier.wait();return self.call(fn,s)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(run,budget.issue_open),pool.submit(run,budget.cancel_before_open)
            opened,cancelled=a.result(timeout=8),b.result(timeout=8)
        self.assertEqual(sum(x is not None for x in (opened,cancelled)),1)
        with local_connect(self.dsn) as conn:
            state=conn.execute('SELECT state FROM saas_voice_session_budget').fetchone()['state']
            hold=conn.execute('SELECT state FROM saas_ai_spend_reservations').fetchone()['state']
        self.assertIn((state,hold),{('opening','unknown'),('released','released')})

    def test_expiry_after_open_never_accepts_config_or_releases_hold(self):
        s=scope();self.call(budget.reserve_session,s);self.call(budget.issue_open,s)
        with patch('session_budget.datetime') as clock:
            clock.now.return_value=datetime(2026,11,4,tzinfo=timezone.utc)
            self.assertEqual(self.call(budget.accept_config,s,event(),'sess_synthetic')['state'],'uncertain')
        self.assertIsNone(self.call(budget.cancel_before_open,s))
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state,reserved_microusd FROM saas_ai_spend_reservations').fetchone(),{'state':'unknown','reserved_microusd':4_224_000})

    def test_upgrade_refuses_existing_legacy_hold_without_data_loss(self):
        name='test_voice_budget_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        dsn=self.base._replace(path='/'+name).geturl()
        try:
            s=scope(response_limit=1);hold=uid()
            with local_connect(dsn) as conn:
                spend.ensure_schema(conn);conn.execute(BASE);conn.execute(UP)
                conn.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s,%s,'old','old','old',1,'unknown')",(s['tenant_id'],hold))
                conn.execute('INSERT INTO saas_voice_test_session(session_id,tenant_id,actor_id,channel_id,conversation_id,executor,fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s)',(s['session_id'],s['tenant_id'],s['actor_id'],s['channel_id'],s['conversation_id'],s['executor'],'synthetic'))
                conn.execute("""INSERT INTO saas_voice_test_hold(request_id,tenant_id,session_id,actor_id,channel_id,conversation_id,executor,response_number,fingerprint,quote_version,model,reserved_microusd,tenant_reservation_id,state)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,1,'legacy','legacy','legacy',1,%s,'unknown')""",(uid(),s['tenant_id'],s['session_id'],s['actor_id'],s['channel_id'],s['conversation_id'],s['executor'],hold))
            with self.assertRaisesRegex(psycopg.IntegrityError,'voice_session_legacy_history_requires_review'):
                with local_connect(dsn) as conn:conn.execute(SCHEMA)
            with local_connect(dsn) as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'unknown')
                self.assertIsNone(conn.execute("SELECT to_regclass('saas_voice_session_budget') AS table_name").fetchone()['table_name'])
        finally:self.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


if __name__=='__main__':unittest.main()
