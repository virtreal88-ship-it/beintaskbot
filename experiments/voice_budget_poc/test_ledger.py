"""Disposable PostgreSQL tests. Never point this module at a real database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import itertools
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from urllib.parse import urlsplit
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import Ledger, SCHEMA, local_connect, reject_libpq_env, RESERVE_MICROUSD, CAP_MICROUSD
import tenant_ai_spend_budget as tenant_spend
import tenant_voice_test_grant as candidate
from tenant_linear_policy import TenantLinearError


def u(): return str(uuid.uuid4())


def scope(**overrides):
    s = {'request_id':u(),'tenant_id':u(),'session_id':u(),'actor_id':1,'response_number':1,'executor':'worker_a'}
    return {**s,**overrides}


def server_scope(**overrides):
    return {**scope(**overrides),'channel_id':u(),'conversation_id':'synthetic-dialog'}


class LedgerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reject_libpq_env()
        dsn = os.environ.get('VOICE_BUDGET_POC_ADMIN_URL', '')
        parsed = urlsplit(dsn)
        parsed_pg = conninfo_to_dict(dsn)
        if (parsed.scheme not in ('postgres','postgresql') or parsed.query or parsed.fragment or parsed.path != '/postgres' or
            parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed_pg.get('host') not in ('localhost','127.0.0.1','::1')):
            raise RuntimeError('localhost_admin_only')
        cls.name = 'test_voice_budget_' + uuid.uuid4().hex
        cls.admin = psycopg.connect(dsn, hostaddr='::1' if parsed.hostname=='::1' else '127.0.0.1', autocommit=True)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.dsn = parsed._replace(path='/'+cls.name).geturl()
        with local_connect(cls.dsn) as conn:
            conn.execute(SCHEMA)
            tenant_spend.ensure_schema(conn)
            conn.execute((Path(__file__).parent/'voice_test_grant.sql').read_text())
        cls.l = Ledger(cls.dsn)

    @classmethod
    def tearDownClass(cls):
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()

    def setUp(self):
        with local_connect(self.dsn) as conn:
            conn.execute('TRUNCATE poc_voice_reservations')
            conn.execute('UPDATE poc_voice_budget SET frozen=false WHERE id=1')
            conn.execute('TRUNCATE saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')
            conn.execute('UPDATE saas_voice_test_grant SET enabled=false,frozen=false')

    def test_quote_and_admin_guards(self):
        self.assertEqual(RESERVE_MICROUSD,2_130_018)
        self.assertEqual(CAP_MICROUSD,5_000_000)
        for dsn in (self.dsn+'?host=example.invalid',self.dsn+'?options=-csearch_path=x',
                    self.dsn.replace('127.0.0.1','example.invalid'),self.dsn.replace('test_voice_budget_','prod_')):
            with self.assertRaisesRegex(ValueError,'local_test_database_only'): local_connect(dsn)
        with patch.dict(os.environ,{'PGHOSTADDR':'192.0.2.1'}), patch('ledger.psycopg.connect') as connect:
            with self.assertRaisesRegex(ValueError,'unexpected_libpq_environment'):local_connect(self.dsn)
            connect.assert_not_called()

    def test_expired_or_changed_quote_cannot_be_claimed(self):
        s=scope();self.l.reserve(s)
        with patch('ledger.datetime') as clock:
            clock.now.return_value=datetime(2026,11,4,tzinfo=timezone.utc)
            with self.assertRaisesRegex(ValueError,'quote_expired'):self.l.claim(s)
        with local_connect(self.dsn) as conn:
            conn.execute("UPDATE poc_voice_reservations SET quote_version='changed' WHERE request_id=%s",(s['request_id'],))
        with self.assertRaisesRegex(ValueError,'quote_policy_mismatch'):self.l.claim(s)

    def test_old_claim_can_be_reconciled_after_quote_update(self):
        s=scope();self.l.reserve(s);self.l.claim(s)
        with patch('ledger.QUOTE_VERSION','new-quote'), patch('ledger.MODEL','new-model'), patch('ledger.RESERVE_MICROUSD',3_000_000):
            self.assertEqual(self.l.note_actual(s,2_112_001)['actual_microusd'],2_112_001)
            with self.assertRaisesRegex(ValueError,'budget_frozen_or_invalid'):self.l.reserve(scope())

    def test_shared_atomic_cap_for_concurrent_executors(self):
        requests=[scope(executor=f'worker_{n}') for n in range(32)]
        def invoke(s):
            try: return self.l.reserve(s)['state']
            except ValueError as exc: return str(exc)
        with ThreadPoolExecutor(max_workers=16) as workers:
            results=list(workers.map(invoke,requests))
        self.assertEqual(results.count('reserved'),2)
        self.assertEqual(results.count('budget_exhausted'),30)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS used FROM poc_voice_reservations').fetchone()
        self.assertEqual(row,{'n':2,'used':4_224_000})

    def test_identical_request_idempotent_and_changed_scope_denied(self):
        s=scope()
        with ThreadPoolExecutor(max_workers=16) as workers:
            rows=list(workers.map(lambda _: self.l.reserve(s),range(24)))
        self.assertEqual(len({str(r['request_id']) for r in rows}),1)
        for changes in ({'executor':'worker_b'},{'session_id':u()},{'actor_id':2},{'response_number':2}):
            with self.assertRaisesRegex(ValueError,'request_scope_conflict'): self.l.reserve({**s,**changes})
        with self.assertRaisesRegex(ValueError,'response_already_reserved'):
            self.l.reserve({**s,'request_id':u()})

    def test_only_one_claim_across_processes_and_restart_keeps_unknown(self):
        s=scope();self.l.reserve(s)
        with ThreadPoolExecutor(max_workers=16) as workers:
            claimed=list(workers.map(lambda _:self.l.claim(s),range(30)))
        self.assertEqual(sum(x is not None for x in claimed),1)
        script=('import json,os; from ledger import Ledger; '
                'l=Ledger(os.environ["POC_DSN"]); s=json.loads(os.environ["POC_SCOPE"]); '
                'print("claimed" if l.claim(s) else "blocked")')
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s)}
        r=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).parent,env=env,text=True,capture_output=True,check=True)
        self.assertEqual(r.stdout.strip(),'blocked')
        self.assertEqual(self.l.mark_unknown(s)['state'],'unknown')
        self.assertIsNone(self.l.cancel_before_claim(s))
        self.assertIsNone(self.l.claim(s))
        self.assertEqual(self.l.reserve(s)['state'],'unknown')
        self.l.reserve(scope())
        with self.assertRaisesRegex(ValueError,'budget_exhausted'): self.l.reserve(scope())

    def test_process_death_after_claim_preserves_hold_and_prevents_replay(self):
        s=scope();self.l.reserve(s)
        env={'PATH':os.environ['PATH'],'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(s)}
        crash=('import json,os; from ledger import Ledger; '
               'l=Ledger(os.environ["POC_DSN"]); s=json.loads(os.environ["POC_SCOPE"]); '
               'assert l.claim(s); os._exit(71)')
        r=subprocess.run([sys.executable,'-c',crash],cwd=Path(__file__).parent,env=env,text=True,capture_output=True)
        self.assertEqual(r.returncode,71,r.stderr)
        retry=('import json,os; from ledger import Ledger; '
               'l=Ledger(os.environ["POC_DSN"]); s=json.loads(os.environ["POC_SCOPE"]); '
               'assert l.claim(s) is None; print(l.reserve(s)["state"])')
        r=subprocess.run([sys.executable,'-c',retry],cwd=Path(__file__).parent,env=env,text=True,capture_output=True,check=True)
        self.assertEqual(r.stdout.strip(),'claimed')
        self.l.reserve(scope())
        with self.assertRaisesRegex(ValueError,'budget_exhausted'):self.l.reserve(scope())

    def test_cancel_and_claim_race_has_single_winner(self):
        for _ in range(8):
            s=scope();self.l.reserve(s)
            with ThreadPoolExecutor(max_workers=2) as workers:
                a=workers.submit(self.l.claim,s);b=workers.submit(self.l.cancel_before_claim,s)
                claimed,released=a.result(),b.result()
            self.assertEqual(sum(x is not None for x in (claimed,released)),1)
            if claimed: self.assertIsNone(self.l.cancel_before_claim(s))
            else:
                self.assertIsNone(self.l.claim(s))
                with self.assertRaisesRegex(ValueError,'reservation_released'): self.l.reserve(s)
            with local_connect(self.dsn) as conn: conn.execute('TRUNCATE poc_voice_reservations')

    def test_settled_never_refunds_and_overrun_freezes(self):
        a,b=scope(),scope();self.l.reserve(a);self.l.claim(a)
        self.assertEqual(self.l.note_actual(a,1)['state'],'settled')
        self.l.reserve(b)
        with self.assertRaisesRegex(ValueError,'budget_exhausted'):self.l.reserve(scope())
        self.l.claim(b);self.l.note_actual(b,RESERVE_MICROUSD+1)
        with self.assertRaisesRegex(ValueError,'budget_frozen_or_invalid'):self.l.reserve(scope())

    def test_overrun_after_other_reservation_blocks_claim(self):
        a,b=scope(),scope();self.l.reserve(a);self.l.reserve(b);self.l.claim(a)
        self.l.note_actual(a,RESERVE_MICROUSD+1)
        with self.assertRaisesRegex(ValueError,'budget_frozen_or_invalid'):self.l.claim(b)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM poc_voice_reservations WHERE request_id=%s',(b['request_id'],)).fetchone()['state'],'reserved')

    def test_freeze_and_claim_serialize_on_budget_row(self):
        a=scope();self.l.reserve(a)
        with local_connect(self.dsn) as conn:
            conn.execute('SELECT id FROM poc_voice_budget WHERE id=1 FOR UPDATE')
            with ThreadPoolExecutor(max_workers=1) as worker:
                pending=worker.submit(self.l.claim,a)
                conn.execute('UPDATE poc_voice_budget SET frozen=true WHERE id=1')
                conn.commit()
                with self.assertRaisesRegex(ValueError,'budget_frozen_or_invalid'):pending.result(timeout=5)
        with local_connect(self.dsn) as conn:
            conn.execute('UPDATE poc_voice_budget SET frozen=false WHERE id=1')
        self.assertIsNotNone(self.l.claim(a))
        # Claims that commit first cannot be recalled by a later freeze.
        self.l.note_actual(a,RESERVE_MICROUSD+1)
        with self.assertRaisesRegex(ValueError,'budget_frozen_or_invalid'):self.l.claim(a)

    def test_late_usage_correction_monotonic_and_freezes(self):
        a,b=scope(),scope();self.l.reserve(a);self.l.reserve(b);self.l.claim(a)
        self.assertEqual(self.l.note_actual(a,100)['actual_microusd'],100)
        self.assertEqual(self.l.note_actual(a,40)['actual_microusd'],100)
        self.assertEqual(self.l.note_actual(a,RESERVE_MICROUSD+2)['actual_microusd'],RESERVE_MICROUSD+2)
        with self.assertRaisesRegex(ValueError,'budget_frozen_or_invalid'):self.l.claim(b)

    def test_invalid_scope_does_not_create_reservation(self):
        for changes in ({'actor_id':True},{'response_number':3},{'executor':'../../secret'},{'tenant_id':'bad'}, {'cost':1}):
            with self.assertRaises((ValueError,TypeError)):self.l.reserve({**scope(),**changes})
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM poc_voice_reservations').fetchone()['n'],0)

    def test_server_candidate_closed_before_any_sql(self):
        with local_connect(self.dsn) as conn:
            with conn.cursor() as cur:
                with self.assertRaises(TenantLinearError):candidate.reserve(cur,server_scope())
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],0)
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,server_scope())

    def test_server_candidate_reserves_tenant_and_global_in_one_transaction(self):
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        def reserve_one(s):
            try:
                with local_connect(self.dsn) as conn,conn.cursor() as cur:return candidate.reserve(cur,s)['state']
            except TenantLinearError as exc:return str(exc)
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with ThreadPoolExecutor(max_workers=12) as workers:
                results=list(workers.map(reserve_one,[server_scope(executor=f'worker_{n}') for n in range(16)]))
        self.assertEqual(results.count('reserved'),2,results)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],2)
            tenant=conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS held FROM saas_ai_spend_reservations').fetchone()
        self.assertEqual(tenant,{'n':2,'held':4_224_000})

    def test_server_candidate_rolls_back_global_if_tenant_budget_denies(self):
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=1):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,server_scope())
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations').fetchone()['n'],0)

    def test_server_candidate_claim_freeze_and_late_usage_correction(self):
        a,b=server_scope(),server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            for s in (a,b):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,s)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(candidate.claim(cur,a)['state'],'claimed')
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.note_actual(cur,a,20)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.note_actual(cur,a,RESERVE_MICROUSD+3)
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,b)
            with local_connect(self.dsn) as conn:
                row=conn.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],)).fetchone()
            self.assertEqual(row,{'state':'unknown','actual_microusd':None})

    def test_server_candidate_cancel_is_atomic_and_claim_cannot_be_cancelled(self):
        a,b=server_scope(),server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            for s in (a,b):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,s)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(candidate.cancel_before_claim(cur,a)['state'],'released')
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(candidate.claim(cur,b)['state'],'claimed')
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(candidate.cancel_before_claim(cur,b))
        with local_connect(self.dsn) as conn:
            global_state=conn.execute('SELECT state FROM saas_voice_test_hold WHERE request_id=%s',(a['request_id'],)).fetchone()['state']
            tenant_state=conn.execute('SELECT state FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],)).fetchone()['state']
        self.assertEqual((global_state,tenant_state),('released','released'))

    def test_server_claim_rechecks_tenant_hold_and_changed_limit(self):
        s=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,s)
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=1):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,s)
        with local_connect(self.dsn) as conn:
            conn.execute("UPDATE saas_ai_spend_reservations SET state='released' WHERE tenant_id=%s",(s['tenant_id'],))
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,s)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold WHERE request_id=%s',(s['request_id'],)).fetchone()['state'],'reserved')

    def test_server_candidate_reconciles_old_quote_without_refunding(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            with patch.object(candidate,'QUOTE','changed'),patch.object(candidate,'MODEL','changed'),patch.object(candidate,'HOLD',3_000_000):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.note_actual(cur,a,10)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],)).fetchone()
        self.assertEqual(row,{'state':'unknown','actual_microusd':None})

    def test_server_session_immutable_across_actor_channel_dialog_executor_and_tenant(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            for delta in ({'tenant_id':u()},{'actor_id':2},{'channel_id':u()},{'conversation_id':'other'},{'executor':'other'}):
                b={**a,**delta,'request_id':u(),'response_number':2}
                with self.assertRaises(TenantLinearError):
                    with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,b)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                self.assertEqual(candidate.reserve(cur,{**a,'request_id':u(),'response_number':2})['state'],'reserved')

    def test_server_generic_release_and_settle_cannot_free_claimed_hold(self):
        a=server_scope();key='voice-test:'+a['request_id']
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0),patch.object(tenant_spend,'_connect',side_effect=lambda:local_connect(self.dsn)):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            tenant_spend.release(a['tenant_id'],key,track_uncapped=True)
            tenant_spend.settle(a['tenant_id'],key,{'input_tokens':0,'output_tokens':0},track_uncapped=True,reserved_prices=(1,1))
            tenant_spend.settle_media(a['tenant_id'],key,operation='asr',attempted_units=0)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],)).fetchone()
        self.assertEqual(row,{'state':'unknown','actual_microusd':None})

    def test_server_claim_and_generic_release_race_cannot_dispatch_without_hold(self):
        for kind, _ in itertools.product(('release','settle'),range(8)):
            a=server_scope();key='voice-test:'+a['request_id']
            with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true,frozen=false')
            with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0),patch.object(tenant_spend,'_connect',side_effect=lambda:local_connect(self.dsn)):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
                def claim():
                    try:
                        with local_connect(self.dsn) as conn,conn.cursor() as cur:return bool(candidate.claim(cur,a))
                    except TenantLinearError:return False
                def generic_finish():
                    if kind=='release':return tenant_spend.release(a['tenant_id'],key,track_uncapped=True)
                    return tenant_spend.settle(a['tenant_id'],key,{'input_tokens':0,'output_tokens':0},track_uncapped=True,reserved_prices=(1,1))
                with ThreadPoolExecutor(max_workers=2) as workers:
                    claim_f=workers.submit(claim);release_f=workers.submit(generic_finish)
                    claimed=claim_f.result();release_f.result()
            with local_connect(self.dsn) as conn:
                row=conn.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],)).fetchone()
                self.assertEqual(row['state'],'unknown' if claimed else ('released' if kind=='release' else 'settled'))
                self.assertEqual(row['actual_microusd'],None if claimed else 0)
                conn.execute('TRUNCATE saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')

    def test_server_exact_released_hold_is_restored_and_overrun_freezes_voice(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            # Simulated inconsistent database state: generic API cannot do this
            # after claim, but manual corruption must still freeze and persist.
            with local_connect(self.dsn) as conn:conn.execute("UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0 WHERE tenant_id=%s",(a['tenant_id'],))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                row=candidate.note_actual(cur,a,RESERVE_MICROUSD+7)
                self.assertEqual((row['state'],row['tenant_repair_state'],row['actual_microusd']),('settled','restored',RESERVE_MICROUSD+7))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                row=candidate.note_actual(cur,a,RESERVE_MICROUSD+8)
                self.assertEqual(row['tenant_repair_state'],'restored')
        with local_connect(self.dsn) as conn:
            self.assertTrue(conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone()['frozen'])
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'settled')
            hold=conn.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(hold,{'state':'unknown','reserved_microusd':RESERVE_MICROUSD+8,'actual_microusd':0})

    def test_server_mismatched_scope_is_not_modified_and_is_quarantined(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            with local_connect(self.dsn) as conn:conn.execute("UPDATE saas_ai_spend_reservations SET kind='other',state='released',actual_microusd=0 WHERE tenant_id=%s",(a['tenant_id'],))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                row=candidate.note_actual(cur,a,RESERVE_MICROUSD+7)
                self.assertEqual((row['state'],row['tenant_repair_state']),('quarantined','conflict'))
        with local_connect(self.dsn) as conn:
            self.assertTrue(conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone()['frozen'])
            row=conn.execute('SELECT kind,state,actual_microusd FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(row,{'kind':'other','state':'released','actual_microusd':0})
        with patch.object(candidate,'RECONCILIATION_VERIFIED',True):
            with local_connect(self.dsn) as conn:conn.execute("UPDATE saas_ai_spend_reservations SET kind='realtime_voice_test',state='unknown' WHERE tenant_id=%s",(a['tenant_id'],))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                row=candidate.note_actual(cur,a,RESERVE_MICROUSD+8)
                self.assertEqual((row['state'],row['tenant_repair_state']),('quarantined','conflict'))

    def test_server_recreates_exact_missing_hold_in_same_transaction(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                # Simulate a damaged constraint in the disposable DB only.
                cur.execute("SELECT conname FROM pg_constraint WHERE conrelid='saas_voice_test_hold'::regclass AND confrelid='saas_ai_spend_reservations'::regclass")
                constraint=cur.fetchone()['conname']
                cur.execute(sql.SQL('ALTER TABLE saas_voice_test_hold DROP CONSTRAINT {}').format(sql.Identifier(constraint)))
                cur.execute('DELETE FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],))
                row=candidate.note_actual(cur,a,RESERVE_MICROUSD+9)
                self.assertEqual((row['state'],row['tenant_repair_state']),('settled','recreated'))
                cur.execute(sql.SQL('ALTER TABLE saas_voice_test_hold ADD CONSTRAINT {} FOREIGN KEY (tenant_id,tenant_reservation_id) REFERENCES saas_ai_spend_reservations(tenant_id,id)').format(sql.Identifier(constraint)))
        with local_connect(self.dsn) as conn:
            hold=conn.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(hold,{'state':'unknown','reserved_microusd':RESERVE_MICROUSD+9,'actual_microusd':None})
            self.assertTrue(conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone()['frozen'])

    def test_server_conflicting_id_and_call_key_never_mutates_either_row(self):
        a=server_scope();key='voice-test:'+a['request_id']
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            with local_connect(self.dsn) as conn:
                conn.execute("UPDATE saas_ai_spend_reservations SET call_key='tampered' WHERE tenant_id=%s",(a['tenant_id'],))
                conn.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,kind,model,reserved_microusd,state) VALUES(%s,%s,%s,'other','other',100,'released')",(a['tenant_id'],u(),key))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                self.assertEqual(candidate.note_actual(cur,a,RESERVE_MICROUSD+3)['tenant_repair_state'],'conflict')
        with local_connect(self.dsn) as conn:
            rows=conn.execute('SELECT call_key,kind,state,reserved_microusd FROM saas_ai_spend_reservations ORDER BY call_key').fetchall()
            self.assertEqual({(r['call_key'],r['kind'],r['state'],r['reserved_microusd']) for r in rows},
                             {('tampered','realtime_voice_test','unknown',RESERVE_MICROUSD),(key,'other','released',100)})

    def test_server_exact_hold_repair_races_real_generic_release(self):
        for _ in range(8):
            a=server_scope();key='voice-test:'+a['request_id']
            with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true,frozen=false')
            with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0),patch.object(tenant_spend,'_connect',side_effect=lambda:local_connect(self.dsn)):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
                with local_connect(self.dsn) as conn:conn.execute("UPDATE saas_ai_spend_reservations SET state='reserved' WHERE tenant_id=%s",(a['tenant_id'],))
                def repair():
                    with local_connect(self.dsn) as conn,conn.cursor() as cur:return candidate.note_actual(cur,a,RESERVE_MICROUSD+3)
                with ThreadPoolExecutor(max_workers=2) as workers:
                    repaired=workers.submit(repair);released=workers.submit(tenant_spend.release,a['tenant_id'],key,track_uncapped=True)
                    self.assertIn(repaired.result()['tenant_repair_state'],('restored','intact'));released.result()
            with local_connect(self.dsn) as conn:
                hold=conn.execute('SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s',(a['tenant_id'],)).fetchone()
                self.assertEqual(hold,{'state':'unknown','reserved_microusd':RESERVE_MICROUSD+3})
                conn.execute('TRUNCATE saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')

    def test_server_overrun_increases_tenant_hold_without_false_actual_and_blocks_other_ai(self):
        a=server_scope();cap=2_200_000;actual=2_300_000
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=cap):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.note_actual(cur,a,actual)
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    tenant_spend.reserve(cur,a['tenant_id'],'unrelated-ai','text','synthetic',1,0,
                                         track_uncapped=True,_bounded_prices=(1_000_000,1_000_000))
        with local_connect(self.dsn) as conn:
            tenant=conn.execute('SELECT state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations').fetchone()
        self.assertEqual(tenant,{'state':'unknown','actual_microusd':None,'reserved_microusd':actual})

    def test_server_kill_switch_does_not_block_late_reconciliation(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        # Admission code switch is off, but trusted reconciliation is separately
        # available for this test. Both gates default closed in the real code.
        with patch.object(candidate,'RECONCILIATION_VERIFIED',True):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,server_scope())
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.mark_unknown(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.note_actual(cur,a,RESERVE_MICROUSD+1)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT h.state,h.actual_microusd,g.frozen FROM saas_voice_test_hold h CROSS JOIN saas_voice_test_grant g').fetchone()
        self.assertEqual(row,{'state':'settled','actual_microusd':RESERVE_MICROUSD+1,'frozen':True})

    def test_dispatch_capability_is_single_use_across_concurrent_connections(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            def issue(_):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:return candidate.issue_dispatch(cur,a)
            with ThreadPoolExecutor(max_workers=16) as pool:
                issued=list(pool.map(issue,range(40)))
            self.assertEqual(sum(x is not None for x in issued),1)
            self.assertEqual(next(x['state'] for x in issued if x),'dispatching')
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(candidate.issue_dispatch(cur,a))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(candidate.mark_unknown(cur,a)['state'],'unknown')
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(candidate.issue_dispatch(cur,a))

    def test_dispatch_committed_before_crash_is_never_reissued_after_restart(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            module_path=Path(__file__).resolve().parents[2]/'beintaskbot-main'
            exp_path=Path(__file__).resolve().parent
            env={'PATH':os.environ['PATH'],'PYTHONPATH':str(module_path)+':'+str(exp_path),'POC_DSN':self.dsn,'POC_SCOPE':json.dumps(a)}
            script='''import json,os
from unittest.mock import patch
from ledger import local_connect
import tenant_voice_test_grant as candidate
import tenant_ai_spend_budget as spending
s=json.loads(os.environ['POC_SCOPE'])
with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(spending,'limit_microusd',return_value=0):
    with local_connect(os.environ['POC_DSN']) as conn,conn.cursor() as cur:
        assert candidate.issue_dispatch(cur,s)['state']=='dispatching'
    os._exit(71)
'''
            child=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True)
            self.assertEqual(child.returncode,71,child.stderr)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(candidate.issue_dispatch(cur,a))
            with local_connect(self.dsn) as conn:
                row=conn.execute('SELECT state,reserved_microusd FROM saas_voice_test_hold WHERE request_id=%s',(a['request_id'],)).fetchone()
            self.assertEqual(row,{'state':'dispatching','reserved_microusd':RESERVE_MICROUSD})

    def test_dispatch_rechecks_grant_tenant_scope_quote_and_expiry(self):
        a,b=server_scope(),server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            for s in (a,b):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,s)
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,s)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(candidate.issue_dispatch(cur,{**a,'executor':'wrong'}))
            with patch('tenant_voice_test_grant.datetime') as clock:
                clock.now.return_value=datetime(2026,11,4,tzinfo=timezone.utc)
                with self.assertRaises(TenantLinearError):
                    with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.issue_dispatch(cur,a)
            with local_connect(self.dsn) as conn:conn.execute("UPDATE saas_ai_spend_reservations SET state='released' WHERE tenant_id=%s",(a['tenant_id'],))
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.issue_dispatch(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.note_actual(cur,b,RESERVE_MICROUSD+1)
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.issue_dispatch(cur,a)

    def test_dispatch_and_mark_unknown_race_never_permits_two_dispatches(self):
        a=server_scope()
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        with patch.object(candidate,'ENFORCEMENT_VERIFIED',True),patch.object(candidate,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.reserve(cur,a)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:candidate.claim(cur,a)
            def run(method):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:return method(cur,a)
            with ThreadPoolExecutor(max_workers=2) as workers:
                dispatch=workers.submit(run,candidate.issue_dispatch);unknown=workers.submit(run,candidate.mark_unknown)
                dispatched,marked=dispatch.result(),unknown.result()
            self.assertIsNotNone(marked)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertIsNone(candidate.issue_dispatch(cur,a))
            self.assertTrue(dispatched is None or dispatched['state']=='dispatching')


if __name__=='__main__': unittest.main()
