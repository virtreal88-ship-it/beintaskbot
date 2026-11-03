"""Lock-manager visibility research; no application integration or network calls.

This does not prove subject/tenant attribution, reader safety after DB-session
loss, contact erasure, HTTP ordering or multi-channel lock-order compatibility.
"""
import importlib
import time
import unittest
import uuid
import psycopg

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class PgLocksResearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)

    @staticmethod
    def connect():return psycopg.connect(fixture.DSN)

    def marker_exists(self,conn,pid,marker):
        return bool(conn.execute('''SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objsubid=1
          AND mode='ShareLock' AND granted AND pid=%s
          AND classid::bigint=((hashtextextended(%s,0)>>32) & 4294967295)
          AND objid::bigint=(hashtextextended(%s,0) & 4294967295)''',(pid,marker,marker)).fetchone())

    def await_release(self,conn,pid,marker,token):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if not self.marker_exists(conn,pid,marker):
                if conn.execute('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',(token,)).fetchone()[0]:return
            time.sleep(.01)
        self.fail('synthetic reader session did not release its locks')

    def test_live_advisory_marker_is_visible_after_repeatable_read_snapshot(self):
        marker='synthetic-scope:'+str(uuid.uuid4());token='synthetic-reader:'+str(uuid.uuid4())
        with self.connect() as old_snapshot,self.connect() as reader:
            old_snapshot.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
            old_snapshot.execute('SELECT count(*) FROM pg_class').fetchone()
            pid=reader.execute('SELECT pg_backend_pid()').fetchone()[0]
            reader.execute('SELECT pg_advisory_lock_shared(hashtextextended(%s,0))',(marker,))
            reader.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(token,))
            reader.commit()
            self.assertTrue(self.marker_exists(old_snapshot,pid,marker))
            self.assertFalse(old_snapshot.execute('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',(token,)).fetchone()[0])
            reader.close()
            self.await_release(old_snapshot,pid,marker,token)
            old_snapshot.rollback()

    def test_backend_loss_does_not_erase_materialized_context(self):
        marker='synthetic-scope:'+str(uuid.uuid4());token='synthetic-reader:'+str(uuid.uuid4())
        reader=self.connect()
        try:
            pid=reader.execute('SELECT pg_backend_pid()').fetchone()[0]
            reader.execute('SELECT pg_advisory_lock_shared(hashtextextended(%s,0))',(marker,))
            reader.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(token,))
            # This object stands in for a Python value only, never a real fact.
            retained={'content':'synthetic'}
        finally:reader.close()
        with self.connect() as inspector:
            self.await_release(inspector,pid,marker,token)
        self.assertEqual(retained['content'],'synthetic')
