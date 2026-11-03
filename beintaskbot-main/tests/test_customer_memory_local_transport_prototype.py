"""Single-process cut/start tests; synthetic local HTTP and isolated PG only."""
import importlib
import os
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from psycopg import sql

fixture=importlib.import_module('test_customer_memory_postgres')
import customer_memory_local_transport_prototype as proto


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.connection.settimeout(2)
        length=int(self.headers.get('Content-Length','0'))
        body=self.rfile.read(length)
        self.server.received.append((self.path,body))
        self.server.arrived.set()
        self.send_response(200)
        self.send_header('Content-Length','0')
        self.end_headers()

    def log_message(self,*_args):
        return


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class LocalTransportPrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Install cleanup before fixture startup so an interrupted schema/setup
        # cannot leave the process configured for the synthetic test database.
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)

    def setUp(self):
        self.schema='synthetic_memory_transport_'+uuid.uuid4().hex
        self.tenant=str(uuid.uuid4());self.subject=str(uuid.uuid4())
        self.schema_created=False
        self.addCleanup(self._drop_schema)
        proto.create_test_schema(fixture.DSN,self.schema)
        self.schema_created=True
        proto.add_test_subject(fixture.DSN,self.schema,self.tenant,self.subject)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),_Handler)
        self.addCleanup(self.server.server_close)
        self.server.daemon_threads=True
        self.server.received=[];self.server.arrived=threading.Event()
        self.server_thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.coordinator=proto.LocalCoordinator(fixture.DSN,self.schema,self.tenant,self.subject,self.server.server_port)

    def _stop_server(self):
        self.server.shutdown();self.server.server_close();self.server_thread.join(2)

    def _drop_schema(self):
        if not self.schema_created:return
        with proto._connection(fixture.DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='1s'")
                cur.execute("SET LOCAL statement_timeout='2s'")
                cur.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(proto._schema(self.schema)))
            conn.commit()

    def _start_thread(self,thread,*releases):
        thread.start()
        def cleanup():
            for event in releases:event.set()
            thread.join(4)
        self.addCleanup(cleanup)

    def test_committed_pending_prevents_previously_reserved_paused_sender(self):
        token=self.coordinator.reserve();self.assertEqual(token,1)
        waiting=threading.Event();resume=threading.Event();done=threading.Event();result=[]
        def sender():
            waiting.set();resume.wait(2)
            try:result.append(self.coordinator.send(token))
            finally:done.set()
        thread=threading.Thread(target=sender);self._start_thread(thread,resume)
        self.assertTrue(waiting.wait(2));self.assertEqual(self.coordinator.mark_pending(),2)
        resume.set();thread.join(3)
        self.assertTrue(done.is_set());self.assertEqual(result,['denied'])
        self.assertFalse(self.server.arrived.wait(.1));self.assertEqual(self.server.received,[])

    def test_mutex_can_allow_late_write_after_erasure_request(self):
        token=self.coordinator.reserve();at_gate=threading.Event();release=threading.Event()
        eraser_entered=threading.Event();erased=threading.Event();done=threading.Event();order=[];result=[]
        def before_write():
            at_gate.set();self.assertTrue(release.wait(3))
        def after_write():order.append('local_write')
        def sender():
            try:result.append(self.coordinator.send(token,before_write=before_write,after_write=after_write))
            finally:done.set()
        def erase():
            eraser_entered.set();self.coordinator.mark_pending();order.append('pending_committed');erased.set()
        thread=threading.Thread(target=sender);self._start_thread(thread,release);self.assertTrue(at_gate.wait(2))
        eraser=threading.Thread(target=erase);self._start_thread(eraser,release);self.assertTrue(eraser_entered.wait(2))
        self.assertFalse(erased.wait(.1),'same mutex must serialize pending with local start')
        # The sender is paused after its DB validation and before the local
        # write. This separate backend can take a row lock immediately, proving
        # the sender is not carrying a PostgreSQL row lock over the wait/write.
        with proto._connection(fixture.DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('SELECT pending FROM {}.gate WHERE tenant=%s AND subject=%s FOR UPDATE NOWAIT').format(proto._schema(self.schema)),(self.tenant,self.subject))
                self.assertEqual(cur.fetchone(),(False,))
            conn.rollback()
        release.set();thread.join(4);eraser.join(4)
        self.assertTrue(done.is_set());self.assertTrue(erased.is_set())
        self.assertEqual(order,['local_write','pending_committed'])
        self.assertEqual(result,['accepted']);self.assertTrue(self.server.arrived.wait(2))
        self.assertEqual(self.server.received,[('/synthetic',b'synthetic fixture only')])

    @unittest.skipUnless(os.environ.get('CUSTOMER_MEMORY_ENFORCE_ERASURE_GATE')=='1','known request/start gap: release gate disabled')
    def test_received_erasure_request_prevents_not_yet_started_local_post(self):
        token=self.coordinator.reserve();at_gate=threading.Event();release=threading.Event()
        erasure_received=threading.Event();done=threading.Event();result=[]
        def before_write():
            at_gate.set()
            if not release.wait(3):raise RuntimeError('release missing')
        def sender():
            try:result.append(self.coordinator.send(token,before_write=before_write))
            finally:done.set()
        def erase():
            erasure_received.set();self.coordinator.mark_pending()
        thread=threading.Thread(target=sender);self._start_thread(thread,release);self.assertTrue(at_gate.wait(2))
        eraser=threading.Thread(target=erase);self._start_thread(eraser,release);self.assertTrue(erasure_received.wait(2))
        release.set();thread.join(4);eraser.join(4)
        self.assertTrue(done.is_set());self.assertEqual(result,['denied'],'POST began after erasure request arrived')
        self.assertFalse(self.server.arrived.wait(.1))

    def test_restart_reads_persisted_pending_before_any_local_post(self):
        import json
        import os
        import subprocess
        import sys
        version=self.coordinator.reserve();self.assertEqual(self.coordinator.mark_pending(),2)
        restarted=proto.LocalCoordinator(fixture.DSN,self.schema,self.tenant,self.subject,self.server.server_port)
        self.assertIsNone(restarted.reserve());self.assertEqual(restarted.send(version),'denied')
        # A fresh interpreter has no inherited in-process coordinator/mutex.
        # Pass only the synthetic test DSN and necessary runtime environment.
        script='''import json,os,sys
import customer_memory_local_transport_prototype as p
c=p.LocalCoordinator(os.environ['CUSTOMER_MEMORY_TEST_DSN'],sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4]))
print(json.dumps([c.reserve(),c.send(int(sys.argv[5]))]))'''
        env={'PATH':os.environ.get('PATH',''), 'PYTHONPATH':'tests/isolated_database_guard:.:tests',
             'CUSTOMER_MEMORY_TEST_ISOLATED':'1','CUSTOMER_MEMORY_TEST_DSN':fixture.DSN}
        child=subprocess.run([sys.executable,'-c',script,self.schema,self.tenant,self.subject,
                              str(self.server.server_port),str(version)],env=env,capture_output=True,text=True,timeout=5,check=True)
        self.assertEqual(json.loads(child.stdout),[None,'denied'])
        self.assertFalse(self.server.arrived.wait(.1))

    def test_independent_coordinators_do_not_fence_paused_stale_sender(self):
        # Deliberately demonstrates the known failure when processes have
        # independent locks; it must not be interpreted as a desired behavior.
        token=self.coordinator.reserve();at_gate=threading.Event();resume=threading.Event();done=threading.Event();result=[]
        other=proto.LocalCoordinator(fixture.DSN,self.schema,self.tenant,self.subject,self.server.server_port)
        def before_write():at_gate.set();self.assertTrue(resume.wait(3))
        def sender():
            try:result.append(self.coordinator.send(token,before_write=before_write))
            finally:done.set()
        thread=threading.Thread(target=sender);self._start_thread(thread,resume);self.assertTrue(at_gate.wait(2))
        # The sender's independent mutex cannot prevent this committed pending.
        self.assertEqual(other.mark_pending(),2)
        with proto._connection(fixture.DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('SELECT pending FROM {}.gate WHERE tenant=%s AND subject=%s').format(proto._schema(self.schema)),(self.tenant,self.subject))
                self.assertEqual(cur.fetchone(),(True,))
            conn.commit()
        resume.set();thread.join(4);self.assertTrue(done.is_set())
        self.assertEqual(result,['accepted']);self.assertTrue(self.server.arrived.wait(2))
        self.assertEqual(self.server.received,[('/synthetic',b'synthetic fixture only')])

    def test_nonlocal_database_and_invalid_schema_are_rejected_before_connect(self):
        with self.assertRaises(RuntimeError):proto._connection('postgresql://example.invalid/customer_memory_test')
        with self.assertRaises(ValueError):proto.create_test_schema(fixture.DSN,'public')
