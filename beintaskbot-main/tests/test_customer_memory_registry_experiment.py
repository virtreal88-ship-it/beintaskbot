"""Isolated lock experiment, NOT a CRM or AI implementation.

Shows a promising READ COMMITTED ordering and why an old REPEATABLE READ
snapshot still causes a serialization failure.  Synthetic UUIDs only.
"""
import importlib
import threading
import time
import unittest
import uuid

import psycopg
from psycopg import sql

fixture = importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN, 'CUSTOMER_MEMORY_TEST_DSN is not set')
class RegistryExperiment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)
        cls.schema = 'synthetic_memory_registry_' + uuid.uuid4().hex
        cls.addClassCleanup(cls.drop_schema)
        with cls.connect() as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
            db.execute(sql.SQL('CREATE TABLE {}.channels(id UUID PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0)').format(sql.Identifier(cls.schema)))
            db.execute(sql.SQL('CREATE TABLE {}.readers(channel UUID NOT NULL, token UUID NOT NULL, PRIMARY KEY(channel,token))').format(sql.Identifier(cls.schema)))
            db.execute(sql.SQL('CREATE TABLE {}.unique_erasures(channel UUID PRIMARY KEY)').format(sql.Identifier(cls.schema)))
            db.execute(sql.SQL('CREATE TABLE {}.event_erasures(id UUID PRIMARY KEY, channel UUID NOT NULL)').format(sql.Identifier(cls.schema)))
            db.commit()

    @classmethod
    def drop_schema(cls):
        with cls.connect() as db:
            db.execute("SET LOCAL lock_timeout='2s'")
            db.execute("SET LOCAL statement_timeout='3s'")
            db.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(cls.schema)))
            db.commit()

    @staticmethod
    def connect():
        return psycopg.connect(fixture.DSN)

    def setUp(self):
        self.x, self.y = uuid.uuid4(), uuid.uuid4()
        with self.connect() as db:
            db.execute(sql.SQL('INSERT INTO {}.channels(id) VALUES(%s),(%s)').format(sql.Identifier(self.schema)), (self.x,self.y))
            db.commit()

    @staticmethod
    def channel_lock(channel):return 'synthetic-registration:' + str(channel)

    @staticmethod
    def reader_lock(token):return 'synthetic-reader:' + str(token)

    def register(self, reader, channel, token):
        # Session lock acquired first, then a committed registry transaction.
        reader.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(self.reader_lock(token),))
        with self.connect() as db:
            if not db.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(self.channel_lock(channel),)).fetchone()[0]:
                raise RuntimeError('registration busy')
            db.execute(sql.SQL('UPDATE {}.channels SET generation=generation+1 WHERE id=%s').format(sql.Identifier(self.schema)),(channel,))
            db.execute(sql.SQL('INSERT INTO {}.readers VALUES(%s,%s)').format(sql.Identifier(self.schema)),(channel,token))
            db.commit()

    def guard_delete(self, db, channel):
        # Deleters share both locks. The reader holds its own lock exclusively.
        db.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',(self.channel_lock(channel),))
        row=db.execute(sql.SQL('SELECT generation FROM {}.channels WHERE id=%s FOR SHARE').format(sql.Identifier(self.schema)),(channel,)).fetchone()
        if not row:raise RuntimeError('missing generation marker')
        tokens=db.execute(sql.SQL('SELECT token FROM {}.readers WHERE channel=%s ORDER BY token').format(sql.Identifier(self.schema)),(channel,)).fetchall()
        for (token,) in tokens:
            db.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',(self.reader_lock(token),))

    def test_opposite_channel_orders_can_share_guards(self):
        barrier=threading.Barrier(2);results=[]
        def run(first,second):
            try:
                with self.connect() as db:
                    db.execute("SET LOCAL lock_timeout='2s'")
                    self.guard_delete(db,first);barrier.wait(3);self.guard_delete(db,second);db.commit()
                results.append('committed')
            except Exception as exc:results.append(getattr(exc,'sqlstate',type(exc).__name__))
        threads=[threading.Thread(target=run,args=(self.x,self.y)),threading.Thread(target=run,args=(self.y,self.x))]
        for t in threads:t.start();self.addCleanup(t.join,6)
        for t in threads:t.join(4);self.assertFalse(t.is_alive())
        self.assertCountEqual(results,['committed','committed'])

    def test_registered_reader_delays_erasure_but_deleters_share_gate(self):
        token=uuid.uuid4();started=threading.Event();finished=threading.Event();out=[];pids=[]
        reader=self.connect()
        try:
            self.register(reader,self.x,token)
            holder=reader.execute('SELECT pg_backend_pid()').fetchone()[0]
            def erase():
                try:
                    with self.connect() as db:
                        db.execute("SET LOCAL lock_timeout='2s'")
                        pids.append(db.execute('SELECT pg_backend_pid()').fetchone()[0])
                        started.set();self.guard_delete(db,self.x);db.commit()
                    out.append('committed')
                except Exception as exc:out.append(getattr(exc,'sqlstate',type(exc).__name__))
                finally:finished.set()
            thread=threading.Thread(target=erase);thread.start()
            self.addCleanup(thread.join,4)
            self.assertTrue(started.wait(2))
            observed=False;deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if finished.wait(.01):break
                with self.connect() as monitor:
                    state=monitor.execute('SELECT %s=ANY(pg_blocking_pids(%s))',(holder,pids[0])).fetchone()
                if state and state[0]:observed=True;break
            self.assertTrue(observed,'synthetic deletion did not reach the reader lock')
            self.assertFalse(finished.is_set())
        finally:reader.close()
        thread.join(3);self.assertTrue(finished.is_set());self.assertEqual(out,['committed'])

    def test_old_repeatable_read_snapshot_aborts_instead_of_missing_reader(self):
        token=uuid.uuid4();reader=self.connect()
        try:
            with self.connect() as deleting:
                deleting.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
                deleting.execute("SET LOCAL lock_timeout='2s'")
                deleting.execute(sql.SQL('SELECT count(*) FROM {}.readers').format(sql.Identifier(self.schema))).fetchone()
                self.register(reader,self.x,token)
                with self.assertRaises(psycopg.errors.SerializationFailure):self.guard_delete(deleting,self.x)
                deleting.rollback()
        finally:reader.close()

    def test_orphaned_reader_lock_released_when_session_dies(self):
        token=uuid.uuid4();reader=self.connect()
        try:self.register(reader,self.x,token)
        finally:reader.close()
        with self.connect() as deleting:
            deleting.execute("SET LOCAL lock_timeout='1s'")
            self.guard_delete(deleting,self.x);deleting.commit()

    def test_new_registration_rejected_while_deletion_holds_gate(self):
        token=uuid.uuid4();reader=self.connect()
        try:
            with self.connect() as deleting:
                self.guard_delete(deleting,self.x)
                with self.assertRaisesRegex(RuntimeError,'registration busy'):
                    self.register(reader,self.x,token)
                deleting.rollback()
        finally:reader.close()
        with self.connect() as db:
            count=db.execute(sql.SQL('SELECT count(*) FROM {}.readers WHERE channel=%s').format(sql.Identifier(self.schema)),(self.x,)).fetchone()[0]
            self.assertEqual(count,0)

    def test_shared_gates_do_not_prevent_unique_tombstone_deadlock(self):
        barrier=threading.Barrier(2);results=[]
        def erase(first,second):
            try:
                with self.connect() as db:
                    db.execute("SET LOCAL lock_timeout='3s'")
                    self.guard_delete(db,first)
                    db.execute(sql.SQL('INSERT INTO {}.unique_erasures VALUES(%s) ON CONFLICT DO NOTHING').format(sql.Identifier(self.schema)),(first,))
                    barrier.wait(3)
                    self.guard_delete(db,second)
                    db.execute(sql.SQL('INSERT INTO {}.unique_erasures VALUES(%s) ON CONFLICT DO NOTHING').format(sql.Identifier(self.schema)),(second,))
                    db.commit()
                results.append('committed')
            except Exception as exc:results.append(getattr(exc,'sqlstate',type(exc).__name__))
        threads=[threading.Thread(target=erase,args=(self.x,self.y)),threading.Thread(target=erase,args=(self.y,self.x))]
        for thread in threads:thread.start();self.addCleanup(thread.join,6)
        for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertCountEqual(results,['40P01','committed'])

    def test_independent_tombstone_events_do_not_conflict_on_channel(self):
        barrier=threading.Barrier(2);results=[]
        def erase(first,second):
            try:
                with self.connect() as db:
                    db.execute("SET LOCAL lock_timeout='3s'")
                    self.guard_delete(db,first)
                    db.execute(sql.SQL('INSERT INTO {}.event_erasures VALUES(%s,%s)').format(sql.Identifier(self.schema)),(uuid.uuid4(),first))
                    barrier.wait(3)
                    self.guard_delete(db,second)
                    db.execute(sql.SQL('INSERT INTO {}.event_erasures VALUES(%s,%s)').format(sql.Identifier(self.schema)),(uuid.uuid4(),second))
                    db.commit()
                results.append('committed')
            except Exception as exc:results.append(getattr(exc,'sqlstate',type(exc).__name__))
        threads=[threading.Thread(target=erase,args=(self.x,self.y)),threading.Thread(target=erase,args=(self.y,self.x))]
        for thread in threads:thread.start();self.addCleanup(thread.join,6)
        for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertCountEqual(results,['committed','committed'])
