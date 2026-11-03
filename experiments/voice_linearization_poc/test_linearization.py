"""Isolated PostgreSQL model; not a migration, not a live provider client."""
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
import tenant_operator_voice_context as voice
import tenant_voice_test_grant as grant
import tenant_ai_spend_budget as spend
from test_integrated_synthetic_pg import IntegratedSynthetic


# Tables in the disposable fixture, NOT a complete production inventory.
TABLES = (
    'saas_tenants', 'saas_tenant_members', 'saas_tenant_workflow_policies',
    'saas_connect_conversations', 'saas_connect_identity_conflicts',
    'saas_connect_direct_identities', 'saas_connect_dialog_links',
    'saas_crm_messages', 'saas_operator_copilot_threads',
    'saas_tenant_integrations', 'saas_ai_agent_settings', 'saas_ai_agents',
    'saas_ai_conversation_state', 'saas_ai_knowledge_sources',
    'saas_ai_knowledge_chunks', 'saas_knowledge_candidates',
    'saas_knowledge_candidate_sources', 'saas_media_evidence_receipts',
    'saas_instruction_lessons',
)


class Linearization(IntegratedSynthetic):
    def setUp(self):
        super().setUp()
        self.conn.execute('''
          CREATE TABLE saas_media_evidence_receipts(tenant_id uuid, value text);
          CREATE TABLE saas_instruction_lessons(tenant_id uuid, value text);
          CREATE TABLE experiment_epoch(tenant_id uuid PRIMARY KEY, epoch bigint NOT NULL);
          CREATE TABLE experiment_intent(request_id uuid PRIMARY KEY,tenant_id uuid NOT NULL,
              epoch bigint NOT NULL,scope_fingerprint text NOT NULL,state text NOT NULL);
          CREATE FUNCTION bump_epoch() RETURNS trigger LANGUAGE plpgsql AS $$
          DECLARE old_tenant uuid; new_tenant uuid;
          BEGIN
            IF TG_TABLE_NAME='saas_tenants' THEN
              IF TG_OP <> 'INSERT' THEN old_tenant := OLD.id; END IF;
              IF TG_OP <> 'DELETE' THEN new_tenant := NEW.id; END IF;
            ELSE
              IF TG_OP <> 'INSERT' THEN old_tenant := OLD.tenant_id; END IF;
              IF TG_OP <> 'DELETE' THEN new_tenant := NEW.tenant_id; END IF;
            END IF;
            IF old_tenant IS NOT NULL AND new_tenant IS NOT NULL AND old_tenant <> new_tenant THEN
              RAISE EXCEPTION 'tenant move unsupported in synthetic model';
            END IF;
            IF coalesce(new_tenant,old_tenant) IS NULL THEN RAISE EXCEPTION 'tenant missing'; END IF;
            INSERT INTO experiment_epoch VALUES(coalesce(new_tenant,old_tenant),1)
              ON CONFLICT(tenant_id) DO UPDATE SET epoch=experiment_epoch.epoch+1;
            RETURN COALESCE(NEW, OLD);
          END $$;
        ''')
        for table in TABLES:
            self.conn.execute(f'CREATE TRIGGER revision AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION bump_epoch()')
        self.conn.execute('INSERT INTO experiment_epoch VALUES(%s,1)', (self.tenant,))
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        for table in TABLES:
            self.conn.execute(f'DROP TRIGGER IF EXISTS revision ON {table}')
        self.conn.execute('DROP FUNCTION IF EXISTS bump_epoch()')
        self.conn.execute('DROP TABLE IF EXISTS experiment_intent,experiment_epoch,saas_media_evidence_receipts,saas_instruction_lessons')
        self.conn.commit()
        super().tearDown()

    def scope(self):
        return {'request_id':str(uuid.uuid4()), 'tenant_id':self.tenant,'session_id':str(uuid.uuid4()),
                'actor_id':7,'channel_id':self.channel,'conversation_id':self.conversation,
                'response_number':1,'executor':'synthetic'}

    def snapshot(self):
        # A single PG snapshot in this model, with the real authorization
        # function. This intentionally does not pretend that the current
        # production context/knowledge readers share this transaction.
        with self.scoped() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            epoch=conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
            voice.authorize(conn.cursor(),{'tenant_id':self.tenant,'telegram_id':7},self.route)
            rows=conn.execute('SELECT id,body,raw FROM saas_crm_messages WHERE tenant_id=%s ORDER BY id',(self.tenant,)).fetchall()
            self.assertTrue(rows)
            return epoch

    def dispatch(self, scope, expected, before_commit=None):
        # Uses the real, still-gated grant implementation with gate overrides
        # confined to this test. Budget locks are acquired before epoch.
        if (scope.get('tenant_id') != self.tenant or scope.get('actor_id') != 7
            or scope.get('channel_id') != self.channel or scope.get('conversation_id') != self.conversation):
            raise ValueError('snapshot_scope_mismatch')
        with self.scoped() as conn:
            with conn.cursor() as cur:
                grant.reserve(cur,scope)
                if not grant.claim(cur,scope):
                    raise ValueError('not_claimable')
                current=cur.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s FOR UPDATE',(self.tenant,)).fetchone()
                if not current or current['epoch'] != expected:
                    raise ValueError('stale_epoch')
                cur.execute('INSERT INTO experiment_intent VALUES(%s,%s,%s,%s,%s)',
                            (scope['request_id'],self.tenant,expected,grant._fingerprint(scope),'ready'))
                if before_commit is not None:
                    before_commit(cur.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'])
            # Successful return occurs only after context manager COMMIT.
        return scope['request_id']

    def test_mutations_invalidate_including_aba_and_roll_back_budget(self):
        actions=(
          ("UPDATE saas_crm_messages SET body='edited' WHERE tenant_id=%s",(self.tenant,)),
          ("INSERT INTO saas_crm_messages(tenant_id,channel,raw,kommo_lead_id,external_id,direction,body,message_type) VALUES(%s,%s,'{}',41,'new','incoming','test','text')",(self.tenant,'connect:'+self.channel)),
          ('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,)),
          ("UPDATE saas_ai_agents SET instructions='edited' WHERE tenant_id=%s",(self.tenant,)),
          ("INSERT INTO saas_operator_copilot_threads(tenant_id,requested_by,channel_id,conversation_id,turns) VALUES(%s,7,%s,%s,'[]') ON CONFLICT(tenant_id,requested_by,channel_id,conversation_id) DO UPDATE SET clear_revision=saas_operator_copilot_threads.clear_revision+1",(self.tenant,self.channel,self.conversation)),
          ("INSERT INTO saas_ai_knowledge_sources(tenant_id,id,title,active,agent_id,provenance) VALUES(%s,%s,'new',true,NULL,'{}')",(self.tenant,str(uuid.uuid4()))),
        )
        with patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            for statement,params in actions:
                self.conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s',(self.tenant,));self.conn.commit()
                expected=self.snapshot();scope=self.scope()
                with self.scoped() as conn:conn.execute(statement,params)
                with self.assertRaisesRegex(ValueError,'stale_epoch'):self.dispatch(scope,expected)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_intent WHERE request_id=%s',(scope['request_id'],)).fetchone()['n'],0)
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold WHERE request_id=%s',(scope['request_id'],)).fetchone()['n'],0)
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE call_key=%s',('voice-test:'+scope['request_id'],)).fetchone()['n'],0)
            self.conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s',(self.tenant,));self.conn.commit()
            before=self.snapshot()
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET body='temporary' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET body='synthetic customer' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with self.assertRaisesRegex(ValueError,'stale_epoch'):self.dispatch(self.scope(),before)

    def test_committed_one_shot_intent_and_budget(self):
        with patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            scope=self.scope();expected=self.snapshot();self.dispatch(scope,expected)
            with self.scoped() as conn:
                hold=conn.execute('SELECT state FROM saas_voice_test_hold WHERE request_id=%s',(scope['request_id'],)).fetchone()
                intent=conn.execute('SELECT state,epoch FROM experiment_intent WHERE request_id=%s',(scope['request_id'],)).fetchone()
                self.assertEqual(hold['state'],'claimed');self.assertEqual(intent,{'state':'ready','epoch':expected})
            with self.assertRaisesRegex(ValueError,'not_claimable'):self.dispatch(scope,expected)
            # Atomic local one-shot consumption is not exactly-once external I/O.
            with self.scoped() as conn:
                wrong={**scope,'conversation_id':'other-conversation'}
                self.assertIsNone(conn.execute("UPDATE experiment_intent SET state='consumed' WHERE request_id=%s AND tenant_id=%s AND epoch=%s AND scope_fingerprint=%s AND state='ready' RETURNING state",(scope['request_id'],scope['tenant_id'],expected,grant._fingerprint(wrong))).fetchone())
                row=conn.execute("UPDATE experiment_intent SET state='consumed' WHERE request_id=%s AND tenant_id=%s AND epoch=%s AND scope_fingerprint=%s AND state='ready' RETURNING state",(scope['request_id'],scope['tenant_id'],expected,grant._fingerprint(scope))).fetchone()
                self.assertEqual(row['state'],'consumed')
            with self.scoped() as conn:
                self.assertIsNone(conn.execute("UPDATE experiment_intent SET state='consumed' WHERE request_id=%s AND tenant_id=%s AND epoch=%s AND scope_fingerprint=%s AND state='ready' RETURNING state",(scope['request_id'],scope['tenant_id'],expected,grant._fingerprint(scope))).fetchone())

    def test_competing_duplicate_dispatch_has_one_committed_intent(self):
        with patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            scope=self.scope();expected=self.snapshot();start=threading.Barrier(2)
            def attempt():
                start.wait(3)
                try:self.dispatch(scope,expected);return 'committed'
                except ValueError as exc:return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(attempt);second=pool.submit(attempt)
                self.assertCountEqual([first.result(6),second.result(6)],['committed','not_claimable'])
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_intent WHERE request_id=%s',(scope['request_id'],)).fetchone()['n'],1)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold WHERE request_id=%s',(scope['request_id'],)).fetchone()['n'],1)

    def test_wrong_snapshot_scope_refused_before_budget(self):
        with patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            original=self.scope();expected=self.snapshot()
            for wrong in ({'tenant_id':self.other},{'actor_id':8},{'channel_id':str(uuid.uuid4())},{'conversation_id':'other'}):
                with self.assertRaisesRegex(ValueError,'snapshot_scope_mismatch'):
                    self.dispatch({**original,**wrong},expected)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],0)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_intent').fetchone()['n'],0)

    def test_writer_started_before_L_cannot_commit_until_after_L(self):
        locked=threading.Event();release=threading.Event();writer_started=threading.Event();pids={}
        with patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            scope=self.scope();expected=self.snapshot()
            def barrier(pid):
                pids['dispatch']=pid;locked.set()
                if not release.wait(5):raise RuntimeError('test release timeout')
            def writer():
                if not locked.wait(3):raise RuntimeError('test lock timeout')
                with self.scoped() as conn:
                    conn.execute("SET LOCAL statement_timeout='5s'")
                    pids['writer']=conn.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
                    writer_started.set()
                    conn.execute("UPDATE saas_crm_messages SET body='after' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with ThreadPoolExecutor(max_workers=2) as pool:
                dispatch=pool.submit(self.dispatch,scope,expected,barrier);write=pool.submit(writer)
                try:
                    self.assertTrue(writer_started.wait(3))
                    deadline=time.monotonic()+3;blocked=False
                    while time.monotonic()<deadline:
                        with self.scoped() as conn:
                            blockers=conn.execute('SELECT pg_blocking_pids(%s) AS ids',(pids['writer'],)).fetchone()['ids']
                        if pids['dispatch'] in blockers:blocked=True;break
                        time.sleep(.01)
                    self.assertTrue(blocked,'writer did not wait for epoch holder')
                finally:release.set()
                self.assertEqual(dispatch.result(6),scope['request_id']);write.result(6)
            with self.scoped() as conn:
                self.assertGreater(conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch'],expected)

    def test_legacy_source_then_budget_vs_dispatch_budget_then_epoch_deadlocks(self):
        # Deliberate counterexample in synthetic transactions. It demonstrates
        # a possible ordering conflict, not evidence a production path does it.
        a_ready=threading.Event();b_ready=threading.Event()
        def writer():
            try:
                with self.scoped() as conn:
                    conn.execute("SET LOCAL deadlock_timeout='100ms'")
                    conn.execute("SET LOCAL statement_timeout='5s'")
                    conn.execute("UPDATE saas_crm_messages SET body='writer' WHERE tenant_id=%s",(self.tenant,))
                    a_ready.set();self.assertTrue(b_ready.wait(3))
                    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+self.tenant,))
                return 'commit'
            except psycopg.errors.DeadlockDetected:return 'deadlock'
        def dispatcher():
            try:
                self.assertTrue(a_ready.wait(3))
                with self.scoped() as conn:
                    conn.execute("SET LOCAL deadlock_timeout='100ms'")
                    conn.execute("SET LOCAL statement_timeout='5s'")
                    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+self.tenant,))
                    b_ready.set()
                    conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s FOR UPDATE',(self.tenant,))
                return 'commit'
            except psycopg.errors.DeadlockDetected:return 'deadlock'
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(writer);b=pool.submit(dispatcher)
            self.assertCountEqual([a.result(7),b.result(7)],['commit','deadlock'])

    def test_truncate_bypasses_row_triggers(self):
        # Demonstrates the actual trigger set's gap, not that a production
        # writer uses TRUNCATE. Do not deploy this trigger design.
        before=self.snapshot()
        with self.scoped() as conn:
            conn.execute('TRUNCATE saas_crm_messages')
            after=conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
        self.assertEqual(before,after)

    def test_uninstrumented_identity_source_does_not_increment_epoch(self):
        # Existing route-conflict code can read owner identity assertions;
        # the fixture's illustrative trigger inventory omits this table.
        before=self.snapshot()
        self.conn.execute('CREATE TABLE saas_connect_owner_identity_assertions(tenant_id uuid, value text)')
        self.conn.commit()
        try:
            with self.scoped() as conn:
                conn.execute("INSERT INTO saas_connect_owner_identity_assertions VALUES(%s,'synthetic')",(self.tenant,))
                after=conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
            self.assertEqual(before,after)
        finally:
            self.conn.execute('DROP TABLE saas_connect_owner_identity_assertions');self.conn.commit()
