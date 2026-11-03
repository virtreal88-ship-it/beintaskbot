"""Synthetic demonstration of dispatch TOCTOU; never contacts an AI provider.

The synthetic gate is *not* integrated into CRM, identity, ACL or model send.
It demonstrates which ordering one database row can and cannot provide, not a
production-safe locking protocol. Only a disposable loopback database is used.
"""
import importlib
import threading
import time
import unittest
import uuid

import psycopg
from psycopg import sql

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN, 'CUSTOMER_MEMORY_TEST_DSN is not set')
class DispatchBoundaryExperiments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Inherit the existing local-only DSN/environment guard and use its
        # isolated fixture, not DATABASE_URL from the deployed application.
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)
        cls.schema='synthetic_memory_gate_'+uuid.uuid4().hex
        cls.addClassCleanup(cls.drop_schema)
        with cls.connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
            conn.execute(sql.SQL('CREATE TABLE {}.gate (tenant UUID NOT NULL, subject UUID NOT NULL, version INTEGER NOT NULL, enabled BOOLEAN NOT NULL, PRIMARY KEY(tenant,subject))').format(sql.Identifier(cls.schema)))
            conn.commit()

    @classmethod
    def drop_schema(cls):
        with cls.connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(cls.schema)))
            conn.commit()

    @staticmethod
    def connect():
        # DSN was validated by the shared fixture before test collection, and
        # overrides system service/host-address options via setUpClass.
        return psycopg.connect(fixture.DSN)

    def setUp(self):
        self.tenant=uuid.uuid4();self.subject=uuid.uuid4();self.calls=[]
        with self.connect() as conn:
            conn.execute(sql.SQL('INSERT INTO {}.gate VALUES (%s,%s,1,TRUE)').format(sql.Identifier(self.schema)),(self.tenant,self.subject))
            conn.commit()

    def erase(self):
        with self.connect() as conn:
            conn.execute(sql.SQL('DELETE FROM {}.gate WHERE tenant=%s AND subject=%s').format(sql.Identifier(self.schema)),(self.tenant,self.subject))
            conn.commit()

    def read(self,conn,*,lock=False):
        query=sql.SQL('SELECT version,enabled FROM {}.gate WHERE tenant=%s AND subject=%s').format(sql.Identifier(self.schema))
        if lock:query += sql.SQL(' FOR SHARE')
        return conn.execute(query,(self.tenant,self.subject)).fetchone()

    def test_separate_recheck_and_external_dispatch_are_not_atomic(self):
        with self.connect() as conn:
            self.assertEqual(self.read(conn),(1,True))
            conn.commit()
        self.erase()
        # This deliberately models the bug: stale bytes can be sent after an
        # erasure commits, even though a pre-dispatch SELECT just succeeded.
        self.calls.append('stale dispatch')
        with self.connect() as conn:self.assertIsNone(self.read(conn))
        self.assertEqual(self.calls,['stale dispatch'])

    def test_erasure_committed_before_locked_guard_denies_dispatch(self):
        self.erase()
        with self.connect() as conn:
            if self.read(conn,lock=True):self.calls.append('dispatch')
            conn.commit()
        self.assertEqual(self.calls,[])

    def test_locked_guard_orders_simulated_dispatch_before_erasure_commit(self):
        started=threading.Event();finished=threading.Event();out=[];pids=[]
        def eraser():
            try:
                with self.connect() as conn:
                    conn.execute("SET LOCAL lock_timeout='2s'")
                    pids.append(conn.execute('SELECT pg_backend_pid()').fetchone()[0])
                    started.set()
                    conn.execute(sql.SQL('DELETE FROM {}.gate WHERE tenant=%s AND subject=%s').format(sql.Identifier(self.schema)),(self.tenant,self.subject))
                    conn.commit()
                out.append('erasure committed')
            except Exception as exc:out.append(type(exc).__name__)
            finally:finished.set()
        with self.connect() as conn:
            self.assertEqual(self.read(conn,lock=True),(1,True))
            holder=conn.execute('SELECT pg_backend_pid()').fetchone()[0]
            thread=threading.Thread(target=eraser);thread.start()
            self.addCleanup(thread.join,4)
            self.assertTrue(started.wait(2));observed=False;deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if finished.wait(.01):break
                with self.connect() as monitor:
                    state=monitor.execute('SELECT %s=ANY(pg_blocking_pids(%s))',(holder,pids[0])).fetchone()
                if state and state[0]:observed=True;break
            self.assertTrue(observed,'eraser did not wait for the synthetic guard transaction')
            self.assertFalse(finished.is_set())
            # Simulated local callback only; no HTTP, no paid operation.
            out.append('dispatch accepted')
            conn.commit()
        thread.join(3)
        self.assertTrue(finished.is_set())
        self.assertEqual(out,['dispatch accepted','erasure committed'])

    def test_revocation_during_generation_discards_draft_before_delivery(self):
        with self.connect() as conn:
            self.assertEqual(self.read(conn,lock=True),(1,True))
            self.calls.append('simulated model dispatch')
            conn.commit()
        # No lock is held during the simulated generation.
        self.erase()
        with self.connect() as conn:
            if self.read(conn,lock=True):self.calls.append('customer delivery')
            conn.commit()
        self.assertEqual(self.calls,['simulated model dispatch'])

    def test_pre_delivery_select_alone_cannot_prevent_late_erasure(self):
        with self.connect() as conn:
            self.assertEqual(self.read(conn),(1,True))
            conn.commit()
        self.erase()
        self.calls.append('stale delivery')
        self.assertEqual(self.calls,['stale delivery'])

    def test_guard_is_scoped_to_tenant_and_subject(self):
        other=uuid.uuid4()
        with self.connect() as conn:
            row=conn.execute(sql.SQL('SELECT version FROM {}.gate WHERE tenant=%s AND subject=%s FOR SHARE').format(sql.Identifier(self.schema)),(other,self.subject)).fetchone()
            self.assertIsNone(row)
            conn.commit()
