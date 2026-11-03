"""Synthetic protocol experiments; never a real CRM, HTTP, or provider call."""
import importlib
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from customer_memory_erasure_model import Denied,ErasureModel

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class TwoPhaseExperiment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)
        cls.schema='synthetic_erasure_'+uuid.uuid4().hex
        cls.addClassCleanup(cls.drop_schema)
        cls.model=ErasureModel(cls.connect,cls.schema)
        with cls.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
                cls.model.install(cur)
            conn.commit()

    @classmethod
    def drop_schema(cls):
        with cls.connect() as conn:
            conn.execute("SET LOCAL lock_timeout='2s'")
            conn.execute("SET LOCAL statement_timeout='3s'")
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(cls.schema)))
            conn.commit()

    @staticmethod
    def connect():return psycopg.connect(fixture.DSN)

    def setUp(self):
        self.tenant=uuid.uuid4();self.subject=uuid.uuid4();self.model.seed(self.tenant,self.subject)

    def test_erasure_first_cancels_queued_and_denies_model_and_delivery(self):
        model_job=uuid.uuid4();delivery_job=uuid.uuid4()
        self.model.queue(self.tenant,self.subject,model_job,'model',1)
        self.model.queue(self.tenant,self.subject,delivery_job,'delivery',1)
        self.model.request_erasure(self.tenant,self.subject,1)
        for job in (model_job,delivery_job):
            with self.assertRaises(Denied):self.model.admit(self.tenant,self.subject,job,1)
        with self.assertRaises(Denied):self.model.queue(self.tenant,self.subject,uuid.uuid4(),'delivery',2)
        state,jobs=self.model.state(self.tenant,self.subject)
        self.assertEqual(state,(2,'pending'));self.assertEqual({x[2] for x in jobs},{'cancelled'})
        self.model.complete_erasure(self.tenant,self.subject)
        self.assertEqual(self.model.state(self.tenant,self.subject)[0],(2,'completed'))

    def test_admitted_before_erasure_may_physically_start_later_and_blocks_completion(self):
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'model',1)
        admitted=threading.Event();allow_network=threading.Event();started=threading.Event();errors=[]
        def simulated_worker():
            try:
                self.model.admit(self.tenant,self.subject,job,1)
                admitted.set()
                if not allow_network.wait(4):raise RuntimeError('test timeout')
                # Synthetic marker only: no provider/HTTP call. This is later
                # than the pending erasure transaction, by design.
                started.set()
            except Exception as exc:errors.append(type(exc).__name__)
        worker=threading.Thread(target=simulated_worker);worker.start()
        self.addCleanup(worker.join,5);self.addCleanup(allow_network.set)
        self.assertTrue(admitted.wait(3))
        # Must commit despite worker waiting: no DB lock is held over network.
        self.model.request_erasure(self.tenant,self.subject,1)
        self.assertFalse(started.is_set())
        with self.assertRaises(Denied):self.model.complete_erasure(self.tenant,self.subject)
        allow_network.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertFalse(errors);self.assertTrue(started.is_set())
        self.model.finish_attempt(self.tenant,self.subject,job,True)
        self.model.complete_erasure(self.tenant,self.subject)
        # A model result cannot be delivered under the now-erased revision.
        with self.assertRaises(Denied):self.model.queue(self.tenant,self.subject,uuid.uuid4(),'delivery',2)

    def test_unknown_outcome_is_hold_and_never_automatically_readmitted(self):
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'model',1)
        self.model.admit(self.tenant,self.subject,job,1)
        # Process/provider outcome can be unknown even before physical POST.
        self.model.finish_attempt(self.tenant,self.subject,job,False)
        self.model.request_erasure(self.tenant,self.subject,1)
        with self.assertRaises(Denied):self.model.admit(self.tenant,self.subject,job,1)
        with self.assertRaises(Denied):self.model.complete_erasure(self.tenant,self.subject)
        with self.assertRaises(Denied):self.model.reconcile(self.tenant,self.subject,job,'assume')
        # A label is not evidence. The prototype models only state transitions.
        self.model.reconcile(self.tenant,self.subject,job,'operator_asserts_not_sent')
        self.model.complete_erasure(self.tenant,self.subject)

    def test_unresolved_claim_after_simulated_crash_stays_admitted_and_blocks_completion(self):
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'model',1)
        self.model.admit(self.tenant,self.subject,job,1)
        # No callback or transition follows the process loss. No automatic
        # retry, unknown resolution, or provider inference is performed.
        self.model.request_erasure(self.tenant,self.subject,1)
        self.assertEqual(self.model.state(self.tenant,self.subject)[1][0][2],'admitted')
        with self.assertRaises(Denied):self.model.complete_erasure(self.tenant,self.subject)
        with self.assertRaises(Denied):self.model.admit(self.tenant,self.subject,job,1)

    def test_two_asserted_outcomes_remain_distinct_without_claiming_verification(self):
        done=uuid.uuid4();unsent=uuid.uuid4()
        for job in (done,unsent):
            self.model.queue(self.tenant,self.subject,job,'model',1)
            self.model.admit(self.tenant,self.subject,job,1)
            self.model.finish_attempt(self.tenant,self.subject,job,False)
        self.model.request_erasure(self.tenant,self.subject,1)
        self.model.reconcile(self.tenant,self.subject,done,'operator_asserts_completed')
        self.model.reconcile(self.tenant,self.subject,unsent,'operator_asserts_not_sent')
        states={row[0]:row[2] for row in self.model.state(self.tenant,self.subject)[1]}
        self.assertEqual(states,{done:'reconciled_completed',unsent:'reconciled_not_sent'})
        self.model.complete_erasure(self.tenant,self.subject)

    def test_delivery_admitted_before_pending_can_start_after_pending(self):
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'delivery',1)
        admitted=threading.Event();release=threading.Event();started=threading.Event();errors=[]
        def synthetic_delivery():
            try:
                self.model.admit(self.tenant,self.subject,job,1);admitted.set()
                if not release.wait(4):raise RuntimeError('test timeout')
                started.set()  # No customer message or HTTP call occurs.
            except Exception as exc:errors.append(type(exc).__name__)
        worker=threading.Thread(target=synthetic_delivery);worker.start()
        self.addCleanup(worker.join,5);self.addCleanup(release.set)
        self.assertTrue(admitted.wait(3));self.model.request_erasure(self.tenant,self.subject,1)
        self.assertFalse(started.is_set())
        with self.assertRaises(Denied):self.model.complete_erasure(self.tenant,self.subject)
        release.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertFalse(errors);self.assertTrue(started.is_set())
        self.model.finish_attempt(self.tenant,self.subject,job,True)
        self.model.complete_erasure(self.tenant,self.subject)

    def test_unfenced_paused_worker_can_resume_after_logical_completion(self):
        # Deliberately demonstrates why asserted reconciliation is not safe:
        # no real network, and success of this test does NOT mean privacy holds.
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'model',1)
        admitted=threading.Event();release=threading.Event();resumed=threading.Event();errors=[]
        def paused_worker():
            try:
                self.model.admit(self.tenant,self.subject,job,1);admitted.set()
                if not release.wait(4):raise RuntimeError('test timeout')
                resumed.set()  # Represents, but does not perform, a late POST.
            except Exception as exc:errors.append(type(exc).__name__)
        worker=threading.Thread(target=paused_worker);worker.start()
        self.addCleanup(worker.join,5);self.addCleanup(release.set)
        self.assertTrue(admitted.wait(3));self.model.request_erasure(self.tenant,self.subject,1)
        # A simulated reaper and human assert an outcome while the original
        # worker is merely paused. They cannot establish that it cannot send.
        self.model.finish_attempt(self.tenant,self.subject,job,False)
        self.model.reconcile(self.tenant,self.subject,job,'operator_asserts_not_sent')
        self.model.complete_erasure(self.tenant,self.subject)
        self.assertEqual(self.model.state(self.tenant,self.subject)[0],(2,'completed'))
        self.assertFalse(resumed.is_set());release.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertFalse(errors);self.assertTrue(resumed.is_set())

    def test_concurrent_admission_and_erasure_have_one_serial_order(self):
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'model',1)
        gate=threading.Barrier(2);results=[]
        def claim():
            try:gate.wait(3);self.model.admit(self.tenant,self.subject,job,1);results.append('admitted')
            except Denied:results.append('denied')
            except Exception as exc:results.append(type(exc).__name__)
        def erase():
            try:gate.wait(3);self.model.request_erasure(self.tenant,self.subject,1);results.append('pending')
            except Exception as exc:results.append(type(exc).__name__)
        threads=[threading.Thread(target=claim),threading.Thread(target=erase)]
        for t in threads:t.start();self.addCleanup(t.join,5)
        for t in threads:t.join(4);self.assertFalse(t.is_alive())
        self.assertIn('pending',results);self.assertEqual(len(results),2)
        self.assertEqual(self.model.state(self.tenant,self.subject)[0],(2,'pending'))
        job_state=self.model.state(self.tenant,self.subject)[1][0][2]
        if 'admitted' in results:
            self.assertEqual(job_state,'admitted')
            with self.assertRaises(Denied):self.model.complete_erasure(self.tenant,self.subject)
        else:
            self.assertIn('denied',results);self.assertEqual(job_state,'cancelled')
            self.model.complete_erasure(self.tenant,self.subject)

    def test_each_real_lock_order_linearizes_admission_and_erasure(self):
        for winner in ('claim','erase'):
            with self.subTest(winner=winner):
                tenant=uuid.uuid4();subject=uuid.uuid4();job=uuid.uuid4()
                self.model.seed(tenant,subject);self.model.queue(tenant,subject,job,'model',1)
                locked=threading.Event();release=threading.Event();other_started=threading.Event();out=[];pids=[]
                original=self.model._subject
                def gated(cur,tenant_arg,subject_arg):
                    row=original(cur,tenant_arg,subject_arg)
                    if threading.current_thread().name==winner:
                        cur.execute('SELECT pg_backend_pid()');pids.append(cur.fetchone()[0]);locked.set()
                        if not release.wait(4):raise RuntimeError('test timeout')
                    return row
                def claim():
                    if winner!='claim':other_started.set()
                    try:self.model.admit(tenant,subject,job,1);out.append('admitted')
                    except Denied:out.append('denied')
                    except Exception as exc:out.append(type(exc).__name__)
                def erase():
                    if winner!='erase':other_started.set()
                    try:self.model.request_erasure(tenant,subject,1);out.append('pending')
                    except Exception as exc:out.append(type(exc).__name__)
                tasks={'claim':claim,'erase':erase};loser='erase' if winner=='claim' else 'claim'
                first=threading.Thread(target=tasks[winner],name=winner)
                second=threading.Thread(target=tasks[loser],name=loser)
                def join_started(thread):
                    if thread.ident is not None:thread.join(5)
                self.addCleanup(join_started,first);self.addCleanup(join_started,second);self.addCleanup(release.set)
                with patch.object(self.model,'_subject',gated):
                    first.start();self.assertTrue(locked.wait(3));second.start();self.assertTrue(other_started.wait(3))
                    observed=False;deadline=time.monotonic()+2
                    while time.monotonic()<deadline:
                        with self.connect() as monitor:
                            row=monitor.execute('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)))',(pids[0],)).fetchone()
                        if row[0]:observed=True;break
                        time.sleep(.01)
                    self.assertTrue(observed,'second transaction did not wait on the held subject row')
                    release.set();first.join(4);second.join(4)
                self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
                self.assertCountEqual(out,['pending','admitted' if winner=='claim' else 'denied'])
                state,jobs=self.model.state(tenant,subject)
                self.assertEqual(state,(2,'pending'))
                self.assertEqual(jobs[0][2],'admitted' if winner=='claim' else 'cancelled')
                if winner=='claim':
                    with self.assertRaises(Denied):self.model.complete_erasure(tenant,subject)
                else:self.model.complete_erasure(tenant,subject)

    def test_rollback_does_not_leave_pending_or_cancelled(self):
        job=uuid.uuid4();self.model.queue(self.tenant,self.subject,job,'model',1)
        with self.assertRaises(psycopg.errors.DivisionByZero):
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql.SQL("UPDATE {} SET state='pending',revision=revision+1 WHERE tenant=%s AND subject=%s").format(self.model.table('subjects')),(self.tenant,self.subject))
                    cur.execute(sql.SQL("UPDATE {} SET state='cancelled' WHERE tenant=%s AND subject=%s").format(self.model.table('jobs')),(self.tenant,self.subject))
                    cur.execute('SELECT 1/0')
                conn.commit()
        state,jobs=self.model.state(self.tenant,self.subject)
        self.assertEqual(state,(1,'active'));self.assertEqual(jobs[0][2],'queued')
        self.model.admit(self.tenant,self.subject,job,1)

    def test_tenant_isolation_and_metadata_only(self):
        other=uuid.uuid4();self.model.seed(other,self.subject);job=uuid.uuid4()
        self.model.queue(self.tenant,self.subject,job,'model',1)
        with self.assertRaises(Denied):self.model.admit(other,self.subject,job,1)
        self.model.request_erasure(self.tenant,self.subject,1)
        self.assertEqual(self.model.state(other,self.subject),((1,'active'),[]))
        with self.connect() as conn:
            rows=conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position",(self.schema,)).fetchall()
        self.assertEqual(rows,[('jobs','tenant'),('jobs','subject'),('jobs','id'),('jobs','kind'),('jobs','state'),('subjects','tenant'),('subjects','subject'),('subjects','revision'),('subjects','state')])
        with self.assertRaises(Denied):self.model.queue(other,self.subject,uuid.uuid4(),'synthetic-secret-content',1)
