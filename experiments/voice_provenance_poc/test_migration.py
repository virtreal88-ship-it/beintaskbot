"""Versioned upgrade/rollback tests on disposable localhost PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
from queue import Queue
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from ledger import local_connect,reject_libpq_env
import tenant_ai_spend_budget as spend
import tenant_voice_test_grant as grant

ROOT=Path(__file__).resolve().parents[1]
BASE=(ROOT/'voice_budget_poc'/'voice_test_grant.sql').read_text()
UP=(ROOT/'voice_budget_poc'/'migration_dispatch_v2.sql').read_text()
DOWN=(ROOT/'voice_budget_poc'/'rollback_dispatch_v1.sql').read_text()
OLD=BASE.replace("'dispatching',",'')
PROVENANCE=(Path(__file__).parent/'provenance.sql').read_text()


def uid():return str(uuid.uuid4())


def scope():
    return {'request_id':uid(),'tenant_id':uid(),'session_id':uid(),'actor_id':1,'channel_id':uid(),
            'conversation_id':'synthetic-dialog','response_number':1,'executor':'synthetic'}


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reject_libpq_env()
        dsn=os.environ.get('VOICE_BUDGET_POC_ADMIN_URL','');p=urlsplit(dsn);pg=conninfo_to_dict(dsn)
        if (p.scheme not in ('postgres','postgresql') or p.query or p.fragment or p.path!='/postgres' or
            p.hostname not in ('localhost','127.0.0.1','::1') or pg.get('host') not in ('localhost','127.0.0.1','::1')):
            raise RuntimeError('localhost_admin_only')
        cls.base=p
        cls.admin=psycopg.connect(dsn,hostaddr='::1' if p.hostname=='::1' else '127.0.0.1',autocommit=True)

    @classmethod
    def tearDownClass(cls):cls.admin.close()

    def setUp(self):
        self.name='test_voice_budget_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)))
        self.addCleanup(lambda:self.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(self.name))))
        self.dsn=self.base._replace(path='/'+self.name).geturl()
        with local_connect(self.dsn) as conn:
            spend.ensure_schema(conn);conn.execute(OLD);conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        self.p1=patch.object(grant,'ENFORCEMENT_VERIFIED',True);self.p1.start();self.addCleanup(self.p1.stop)
        self.p2=patch.object(grant,'RECONCILIATION_VERIFIED',True);self.p2.start();self.addCleanup(self.p2.stop)
        self.p3=patch.object(spend,'limit_microusd',return_value=0);self.p3.start();self.addCleanup(self.p3.stop)

    def run_sql(self,script):
        with local_connect(self.dsn) as conn:conn.execute(script)

    def create(self):
        s=scope()
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.reserve(cur,s)
        return s

    def disable_grant(self):
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')

    def synthetic_enable_as_db_owner(self):
        # Local test fixture deliberately bypasses the persistent guard to
        # exercise dispatch; this is not an authorized production operation.
        with local_connect(self.dsn) as conn:
            conn.execute('ALTER TABLE saas_voice_test_grant DISABLE TRIGGER voice_test_block_enable')
            conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
            conn.execute('ALTER TABLE saas_voice_test_grant ENABLE TRIGGER voice_test_block_enable')

    def state(self,s):
        with local_connect(self.dsn) as conn:
            return conn.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_voice_test_hold WHERE request_id=%s',(s['request_id'],)).fetchone()

    def test_upgrade_reentrant_and_safe_rollback_without_data_loss(self):
        s=self.create();before=self.state(s)
        self.disable_grant()
        self.run_sql(UP);self.run_sql(UP)
        self.assertEqual(self.state(s),before)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT version FROM saas_voice_test_schema_versions WHERE component='dispatch'").fetchone()['version'],2)
        with self.assertRaises(psycopg.IntegrityError):
            with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        self.synthetic_enable_as_db_owner()
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(DOWN)  # grant enabled
        self.disable_grant()
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(DOWN)  # reservation unresolved
        self.assertEqual(self.state(s),before)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:self.assertEqual(grant.cancel_before_claim(cur,s)['state'],'released')
        self.run_sql(DOWN);self.run_sql(DOWN)
        self.assertEqual(self.state(s)['state'],'released')
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT version FROM saas_voice_test_schema_versions WHERE component='dispatch'").fetchone()['version'],1)
        self.run_sql(UP);self.assertEqual(self.state(s)['state'],'released')

    def test_dispatching_blocks_rollback_until_synthetic_result_reconciled(self):
        s=self.create();self.disable_grant();self.run_sql(UP);self.synthetic_enable_as_db_owner()
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.claim(cur,s)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.issue_dispatch(cur,s)
        with local_connect(self.dsn) as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(DOWN)
        self.assertEqual(self.state(s)['state'],'dispatching')
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.mark_unknown(cur,s)
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(DOWN)
        # This is synthetic accounting, no provider request was made.
        with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.note_actual(cur,s,0)
        self.run_sql(DOWN)
        self.assertEqual(self.state(s),{'state':'settled','reserved_microusd':grant.HOLD,'actual_microusd':0})

    def test_unknown_constraint_and_version_mismatch_fail_closed(self):
        s=self.create();before=self.state(s)
        self.disable_grant()
        with local_connect(self.dsn) as conn:
            conn.execute('ALTER TABLE saas_voice_test_hold DROP CONSTRAINT saas_voice_test_hold_state_check')
            conn.execute("ALTER TABLE saas_voice_test_hold ADD CONSTRAINT saas_voice_test_hold_state_check CHECK(state <> 'forbidden')")
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(UP)
        self.assertEqual(self.state(s),before)
        with local_connect(self.dsn) as conn:
            self.assertIsNone(conn.execute("SELECT to_regclass('saas_voice_test_schema_versions') AS rel").fetchone()['rel'])

    def test_recorded_v2_with_old_constraint_is_rejected(self):
        s=self.create();before=self.state(s)
        self.disable_grant()
        with local_connect(self.dsn) as conn:
            conn.execute('CREATE TABLE saas_voice_test_schema_versions(component TEXT PRIMARY KEY,version SMALLINT NOT NULL)')
            conn.execute("INSERT INTO saas_voice_test_schema_versions(component,version) VALUES('dispatch',2)")
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(UP)
        self.assertEqual(self.state(s),before)

    def test_blocked_table_lock_times_out_without_mutation(self):
        s=self.create();before=self.state(s)
        self.disable_grant()
        with local_connect(self.dsn) as blocking,ThreadPoolExecutor(max_workers=1) as pool:
            blocking.execute('LOCK TABLE saas_voice_test_hold IN ACCESS SHARE MODE')
            future=pool.submit(self.run_sql,UP)
            with self.assertRaises(psycopg.errors.LockNotAvailable):future.result(timeout=8)
            blocking.rollback()
        self.assertEqual(self.state(s),before)

    def test_provenance_requires_v2_disabled_and_blocks_unreviewed_downgrade(self):
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(PROVENANCE)
        self.disable_grant()
        self.run_sql(UP)
        self.synthetic_enable_as_db_owner()
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(PROVENANCE)
        self.disable_grant()
        self.run_sql(PROVENANCE)
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(DOWN)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT version FROM saas_voice_test_schema_versions WHERE component='dispatch'").fetchone()['version'],2)

    def test_provenance_install_lock_is_bounded_and_retriable(self):
        self.disable_grant()
        self.run_sql(UP)
        with local_connect(self.dsn) as blocking,ThreadPoolExecutor(max_workers=1) as pool:
            blocking.execute('LOCK TABLE saas_voice_test_hold IN ROW EXCLUSIVE MODE')
            future=pool.submit(self.run_sql,PROVENANCE)
            with self.assertRaises(psycopg.errors.LockNotAvailable):future.result(timeout=8)
            blocking.rollback()
        with local_connect(self.dsn) as conn:
            self.assertIsNone(conn.execute("SELECT to_regclass('saas_voice_provenance') AS rel").fetchone()['rel'])
        self.run_sql(PROVENANCE)

    def test_concurrent_reentrant_upgrade_keeps_rows(self):
        s=self.create();before=self.state(s)
        self.disable_grant()
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:self.run_sql(UP),range(8)))
        self.assertEqual(self.state(s),before)
        with local_connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT version FROM saas_voice_test_schema_versions WHERE component='dispatch'").fetchone()['version'],2)

    def test_enable_cannot_race_upgrade_rollback_or_provenance_install(self):
        self.disable_grant()
        def enable(start):
            with local_connect(self.dsn) as conn:
                start.put(conn.info.backend_pid);conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        def race(script):
            start=Queue()
            with local_connect(self.dsn) as first,ThreadPoolExecutor(max_workers=1) as workers:
                first.execute(script)
                pending=workers.submit(enable,start)
                pid=start.get(timeout=5);blocker=first.info.backend_pid;deadline=time.monotonic()+3
                observed=False
                while time.monotonic()<deadline:
                    with local_connect(self.dsn) as inspector:
                        observed=inspector.execute('SELECT %s=ANY(pg_blocking_pids(%s)) AS blocked',(blocker,pid)).fetchone()['blocked']
                    if observed:break
                    time.sleep(.02)
                self.assertTrue(observed,'concurrent enable must wait on migration transaction')
                first.commit()
                with self.assertRaises(psycopg.IntegrityError):pending.result(timeout=8)
            with local_connect(self.dsn) as conn:
                self.assertFalse(conn.execute('SELECT enabled FROM saas_voice_test_grant').fetchone()['enabled'])
        race(UP)
        race(DOWN)
        self.run_sql(UP)
        race(PROVENANCE)

    def test_up_fails_if_enable_committed_first_and_guard_tampering_fails_closed(self):
        # setUp starts with enabled=true; migration must not run until disabled.
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(UP)
        self.disable_grant();self.run_sql(UP)
        with local_connect(self.dsn) as conn:
            conn.execute('ALTER TABLE saas_voice_test_grant DISABLE TRIGGER voice_test_block_enable')
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(UP)
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(DOWN)
        with self.assertRaises(psycopg.IntegrityError):self.run_sql(PROVENANCE)

    def test_guard_rejects_delete_or_enabled_insert_and_survives_rollback(self):
        self.disable_grant();self.run_sql(UP);self.run_sql(DOWN)
        for query in (
            'DELETE FROM saas_voice_test_grant',
            "INSERT INTO saas_voice_test_grant(grant_key,cap_microusd,enabled) VALUES('voice-test-owner-5usd-2026',5000000,true)",
            'UPDATE saas_voice_test_grant SET enabled=true',
        ):
            with self.assertRaisesRegex(psycopg.IntegrityError,'voice_activation_requires_review'):
                with local_connect(self.dsn) as conn:conn.execute(query)
        with local_connect(self.dsn) as conn:
            self.assertFalse(conn.execute('SELECT enabled FROM saas_voice_test_grant').fetchone()['enabled'])


if __name__=='__main__':unittest.main()
