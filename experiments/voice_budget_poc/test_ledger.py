"""Disposable PostgreSQL tests. Never point this module at a real database."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import Ledger, SCHEMA, local_connect, RESERVE_MICROUSD, CAP_MICROUSD


def u(): return str(uuid.uuid4())


def scope(**overrides):
    s = {'request_id':u(),'tenant_id':u(),'session_id':u(),'actor_id':1,'response_number':1,'executor':'worker_a'}
    return {**s,**overrides}


class LedgerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ.get('VOICE_BUDGET_POC_ADMIN_URL', '')
        parsed = urlsplit(dsn)
        parsed_pg = conninfo_to_dict(dsn)
        if (parsed.scheme not in ('postgres','postgresql') or parsed.query or parsed.fragment or parsed.path != '/postgres' or
            parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed_pg.get('host') not in ('localhost','127.0.0.1','::1')):
            raise RuntimeError('localhost_admin_only')
        cls.name = 'test_voice_budget_' + uuid.uuid4().hex
        cls.admin = psycopg.connect(dsn, autocommit=True)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.dsn = parsed._replace(path='/'+cls.name).geturl()
        with local_connect(cls.dsn) as conn:
            conn.execute(SCHEMA)
        cls.l = Ledger(cls.dsn)

    @classmethod
    def tearDownClass(cls):
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()

    def setUp(self):
        with local_connect(self.dsn) as conn:
            conn.execute('TRUNCATE poc_voice_reservations')
            conn.execute('UPDATE poc_voice_budget SET frozen=false WHERE id=1')

    def test_quote_and_admin_guards(self):
        self.assertEqual(RESERVE_MICROUSD,2_112_000)
        self.assertEqual(CAP_MICROUSD,5_000_000)
        for dsn in (self.dsn+'?host=example.invalid',self.dsn+'?options=-csearch_path=x',
                    self.dsn.replace('127.0.0.1','example.invalid'),self.dsn.replace('test_voice_budget_','prod_')):
            with self.assertRaisesRegex(ValueError,'local_test_database_only'): local_connect(dsn)

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

    def test_invalid_scope_does_not_create_reservation(self):
        for changes in ({'actor_id':True},{'response_number':3},{'executor':'../../secret'},{'tenant_id':'bad'}, {'cost':1}):
            with self.assertRaises((ValueError,TypeError)):self.l.reserve({**scope(),**changes})
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM poc_voice_reservations').fetchone()['n'],0)


if __name__=='__main__': unittest.main()
