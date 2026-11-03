"""Synthetic isolated PostgreSQL tests, never a provider or production DB."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
from threading import Barrier

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import local_connect,reject_libpq_env
import tenant_ai_spend_budget as spend
import tenant_voice_test_grant as grant
from tenant_linear_policy import TenantLinearError
import pinned_dispatch

ROOT=Path(__file__).resolve().parents[1]


def uid():return str(uuid.uuid4())


def scope():
    return {'request_id':uid(),'tenant_id':uid(),'session_id':uid(),'actor_id':1,'channel_id':uid(),
            'conversation_id':'synthetic-dialog','response_number':1,'executor':'synthetic'}


class ProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reject_libpq_env()
        dsn=os.environ.get('VOICE_BUDGET_POC_ADMIN_URL','');p=urlsplit(dsn);pg=conninfo_to_dict(dsn)
        if (p.scheme not in ('postgres','postgresql') or p.query or p.fragment or p.path!='/postgres' or
            p.hostname not in ('localhost','127.0.0.1','::1') or pg.get('host') not in ('localhost','127.0.0.1','::1')):
            raise RuntimeError('localhost_admin_only')
        cls.base=p;cls.name='test_voice_budget_'+uuid.uuid4().hex
        cls.admin=psycopg.connect(dsn,hostaddr='::1' if p.hostname=='::1' else '127.0.0.1',autocommit=True)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.dsn=p._replace(path='/'+cls.name).geturl()
        with local_connect(cls.dsn) as conn:
            spend.ensure_schema(conn)
            conn.execute((ROOT/'voice_budget_poc'/'voice_test_grant.sql').read_text())
            conn.execute((ROOT/'voice_budget_poc'/'migration_dispatch_v2.sql').read_text())
            conn.execute((Path(__file__).parent/'provenance.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()

    def setUp(self):
        with local_connect(self.dsn) as conn:
            # TRUNCATE bypasses row triggers: test-only cleanup, not a safe
            # production privilege. Production access control is not proven.
            conn.execute('TRUNCATE saas_voice_provenance,saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')
            conn.execute('UPDATE saas_voice_test_grant SET enabled=true,frozen=false')
        self.p1=patch.object(grant,'ENFORCEMENT_VERIFIED',True);self.p1.start();self.addCleanup(self.p1.stop)
        self.p2=patch.object(grant,'RECONCILIATION_VERIFIED',True);self.p2.start();self.addCleanup(self.p2.stop)
        self.p3=patch.object(spend,'limit_microusd',return_value=0);self.p3.start();self.addCleanup(self.p3.stop)

    def reserve(self,s):
        with local_connect(self.dsn) as conn,conn.cursor() as cur:return grant.reserve(cur,s)

    def test_new_reservation_atomically_pins_one_provenance_across_executors(self):
        s=scope()
        with ThreadPoolExecutor(max_workers=12) as workers:rows=list(workers.map(lambda _:self.reserve(s),range(20)))
        self.assertEqual(len({str(r['tenant_reservation_id']) for r in rows}),1)
        with local_connect(self.dsn) as conn:
            p=conn.execute('SELECT * FROM saas_voice_provenance WHERE request_id=%s',(s['request_id'],)).fetchone()
            self.assertEqual((str(p['tenant_id']),p['call_key'],p['kind']),(s['tenant_id'],'voice-test:'+s['request_id'],'realtime_voice_test'))
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_provenance').fetchone()['n'],1)
            tenant=conn.execute('SELECT count(*) AS n,sum(reserved_microusd) AS held FROM saas_ai_spend_reservations WHERE tenant_id=%s',(s['tenant_id'],)).fetchone()
            self.assertEqual(tenant,{'n':1,'held':grant.HOLD})

    def test_protected_rows_reject_identity_change_release_and_delete(self):
        s=scope();self.reserve(s)
        statements=[
            ("UPDATE saas_ai_spend_reservations SET kind='other' WHERE tenant_id=%s",(s['tenant_id'],)),
            ("UPDATE saas_ai_spend_reservations SET call_key='other' WHERE tenant_id=%s",(s['tenant_id'],)),
            ("UPDATE saas_ai_spend_reservations SET reserved_microusd=1 WHERE tenant_id=%s",(s['tenant_id'],)),
            ("UPDATE saas_ai_spend_reservations SET state='released',actual_microusd=0 WHERE tenant_id=%s",(s['tenant_id'],)),
            ("DELETE FROM saas_ai_spend_reservations WHERE tenant_id=%s",(s['tenant_id'],)),
            ("UPDATE saas_voice_provenance SET call_key='other' WHERE request_id=%s",(s['request_id'],)),
            ("DELETE FROM saas_voice_provenance WHERE request_id=%s",(s['request_id'],)),
            ("UPDATE saas_voice_test_hold SET fingerprint='wrong' WHERE request_id=%s",(s['request_id'],)),
            ("DELETE FROM saas_voice_test_hold WHERE request_id=%s",(s['request_id'],)),
        ]
        for statement,args in statements:
            with self.assertRaises(psycopg.IntegrityError):
                with local_connect(self.dsn) as conn:conn.execute(statement,args)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'reserved')
            self.assertEqual(conn.execute('SELECT state FROM saas_ai_spend_reservations').fetchone()['state'],'reserved')

    def test_generic_release_and_settle_reject_bound_voice_but_unrelated_work_survives(self):
        s=scope();self.reserve(s)
        with patch.object(spend,'_connect',side_effect=lambda:local_connect(self.dsn)):
            with self.assertRaises(psycopg.IntegrityError):spend.release(s['tenant_id'],'voice-test:'+s['request_id'],track_uncapped=True)
            with self.assertRaises(psycopg.IntegrityError):spend.settle(s['tenant_id'],'voice-test:'+s['request_id'],{'input_tokens':0,'output_tokens':0},track_uncapped=True,reserved_prices=(1,1))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                spend.reserve(cur,s['tenant_id'],'unrelated','text','synthetic',1,0,track_uncapped=True,_bounded_prices=(1_000_000,1_000_000))
            spend.release(s['tenant_id'],'unrelated',track_uncapped=True)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT state FROM saas_ai_spend_reservations WHERE call_key='unrelated'").fetchone()['state'],'released')

    def test_preclaim_cancel_frees_only_exact_voice_hold_without_deleting_provenance(self):
        s=scope();self.reserve(s)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(grant.cancel_before_claim(cur,s)['state'],'released')
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'released')
            self.assertEqual(conn.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations').fetchone(),{'state':'released','actual_microusd':0})
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_provenance').fetchone()['n'],1)
        with self.assertRaises(TenantLinearError):self.reserve(s)

    def test_cancel_and_claim_race_keeps_consistent_protected_hold(self):
        for _ in range(8):
            s=scope();self.reserve(s)
            def run(method):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:return method(cur,s)
            with ThreadPoolExecutor(max_workers=2) as workers:
                a=workers.submit(run,grant.claim);b=workers.submit(run,grant.cancel_before_claim)
                claimed,cancelled=a.result(),b.result()
            self.assertEqual(sum(x is not None for x in (claimed,cancelled)),1)
            with local_connect(self.dsn) as conn:
                voice=conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state']
                tenant=conn.execute('SELECT state FROM saas_ai_spend_reservations').fetchone()['state']
                self.assertEqual((voice,tenant),('claimed','unknown') if claimed else ('released','released'))
                conn.execute('TRUNCATE saas_voice_provenance,saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')

    def test_actual_voice_and_generic_budget_race_do_not_reverse_lock_order(self):
        for _ in range(5):
            s=scope();barrier=Barrier(2)
            def voice():
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    barrier.wait(timeout=5);return grant.reserve(cur,s)
            def normal():
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    barrier.wait(timeout=5)
                    return spend.reserve(cur,s['tenant_id'],'other-'+uid(),'text','synthetic',1,0,
                                         track_uncapped=True,_bounded_prices=(1_000_000,1_000_000))
            with ThreadPoolExecutor(max_workers=2) as workers:
                a=workers.submit(voice);b=workers.submit(normal)
                self.assertTrue(a.result(timeout=8));self.assertTrue(b.result(timeout=8))
            with local_connect(self.dsn) as conn:
                conn.execute('TRUNCATE saas_voice_provenance,saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')

    def test_reversed_global_and_tenant_locks_are_aborted_without_hanging(self):
        tenant=uid();barrier=Barrier(2)
        def lock(order):
            try:
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout='3s'")
                    for index,item in enumerate(order):
                        if item=='global':cur.execute('SELECT grant_key FROM saas_voice_test_grant WHERE grant_key=%s FOR UPDATE',(grant.GRANT_KEY,))
                        else:cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+tenant,))
                        if index==0:barrier.wait(timeout=5)
                    return 'success'
            except (psycopg.errors.DeadlockDetected,psycopg.errors.LockNotAvailable):return 'aborted'
        with ThreadPoolExecutor(max_workers=2) as workers:
            a=workers.submit(lock,('global','tenant'));b=workers.submit(lock,('tenant','global'))
            results=[a.result(timeout=8),b.result(timeout=8)]
        self.assertCountEqual(results,['success','aborted'])

    def test_one_use_dispatch_and_monotonic_usage_work_with_new_provenance(self):
        s=scope();self.reserve(s)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.claim(cur,s)
        def issue(_):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:return pinned_dispatch.issue(cur,s)
        with ThreadPoolExecutor(max_workers=12) as workers:rows=list(workers.map(issue,range(20)))
        self.assertEqual(sum(r is not None for r in rows),1)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(grant.mark_unknown(cur,s)['state'],'unknown')
        with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(grant.note_actual(cur,s,grant.HOLD+1)['state'],'settled')
        with local_connect(self.dsn) as conn:
            tenant=conn.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations').fetchone()
            self.assertEqual(tenant,{'state':'unknown','reserved_microusd':grant.HOLD+1,'actual_microusd':None})
            self.assertTrue(conn.execute('SELECT frozen FROM saas_voice_test_grant').fetchone()['frozen'])

    def test_mismatched_provenance_never_issues_dispatch(self):
        s=scope();self.reserve(s)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.claim(cur,s)
        # Simulates an unsafe privileged operator; production role/ownership
        # constraints are not proved by this offline test.
        with local_connect(self.dsn) as conn:
            conn.execute('ALTER TABLE saas_voice_provenance DISABLE TRIGGER voice_provenance_no_change')
            conn.execute("UPDATE saas_voice_provenance SET fingerprint='damaged' WHERE request_id=%s",(s['request_id'],))
            conn.execute('ALTER TABLE saas_voice_provenance ENABLE TRIGGER voice_provenance_no_change')
        with self.assertRaises(TenantLinearError):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:pinned_dispatch.issue(cur,s)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'claimed')

    def test_legacy_nonempty_history_blocks_migration_without_partial_ddl(self):
        name='test_voice_budget_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        dsn=self.base._replace(path='/'+name).geturl()
        try:
            with local_connect(dsn) as conn:
                spend.ensure_schema(conn);conn.execute((ROOT/'voice_budget_poc'/'voice_test_grant.sql').read_text())
                conn.execute((ROOT/'voice_budget_poc'/'migration_dispatch_v2.sql').read_text())
                conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
            with local_connect(dsn) as conn,conn.cursor() as cur:grant.reserve(cur,scope())
            with local_connect(dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
            with self.assertRaises(psycopg.IntegrityError):
                with local_connect(dsn) as conn:conn.execute((Path(__file__).parent/'provenance.sql').read_text())
            with local_connect(dsn) as conn:
                self.assertIsNone(conn.execute("SELECT to_regclass('saas_voice_provenance') AS rel").fetchone()['rel'])
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],1)
        finally:self.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


if __name__=='__main__':unittest.main()
