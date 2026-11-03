"""Synthetic two-phase proof. No deployment, provider or audio storage."""
import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
import tenant_operator_voice_context as voice
import tenant_voice_test_grant as grant
import tenant_ai_spend_budget as spend
from test_linearization import Linearization


# A test override is not verification of any real fleet.
FLEET_VERIFIED = False


class TwoPhase(Linearization):
    def setUp(self):
        super().setUp()
        self.conn.execute('''
          ALTER TABLE saas_instruction_lessons ADD COLUMN id uuid, ADD COLUMN state text, ADD COLUMN published_doc jsonb;
          CREATE TABLE synthetic_scope_epoch(tenant uuid,channel uuid,actor bigint,epoch bigint NOT NULL,
            PRIMARY KEY(tenant,channel,actor));
          CREATE TABLE synthetic_clear(request_id uuid PRIMARY KEY,tenant uuid,channel uuid,actor bigint,conversation text);
          CREATE TABLE synthetic_quote(version text PRIMARY KEY,model text,hold bigint,expires_at timestamptz NOT NULL);
          CREATE TABLE synthetic_prepared(request_id uuid PRIMARY KEY,tenant uuid,channel uuid,actor bigint,conversation text,
            scope_hash text NOT NULL,payload_hash text NOT NULL,global_epoch bigint NOT NULL,scope_epoch bigint NOT NULL,
            quote_version text NOT NULL,model text NOT NULL,hold bigint NOT NULL,quote_expires timestamptz NOT NULL);
          CREATE TABLE synthetic_intent(request_id uuid PRIMARY KEY,scope_hash text NOT NULL,payload_hash text NOT NULL,
            state text NOT NULL,validated_at timestamptz NOT NULL,quote_version text NOT NULL);
          CREATE FUNCTION synthetic_immutable_prepared() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'prepared immutable'; END $$;
          CREATE TRIGGER synthetic_immutable BEFORE UPDATE OR DELETE ON synthetic_prepared
            FOR EACH ROW EXECUTE FUNCTION synthetic_immutable_prepared();
        ''')
        self.conn.execute('INSERT INTO synthetic_scope_epoch VALUES(%s,%s,7,1)',(self.tenant,self.channel))
        self.conn.execute('INSERT INTO synthetic_quote VALUES(%s,%s,%s,%s)',(grant.QUOTE,grant.MODEL,grant.HOLD,grant.EXPIRES))
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('DROP TABLE IF EXISTS synthetic_intent,synthetic_prepared,synthetic_quote,synthetic_clear,synthetic_scope_epoch')
        self.conn.execute('DROP FUNCTION IF EXISTS synthetic_immutable_prepared()')
        self.conn.commit()
        super().tearDown()

    def assert_scope(self,scope):
        if (scope.get('tenant_id')!=self.tenant or scope.get('actor_id')!=7 or
            scope.get('channel_id')!=self.channel or scope.get('conversation_id')!=self.conversation):
            raise ValueError('snapshot_scope_mismatch')

    def prepare(self,scope,payload):
        if not FLEET_VERIFIED:raise ValueError('legacy_fleet_unverified')
        self.assert_scope(scope)
        if not isinstance(payload,(bytes,bytearray,memoryview)):raise ValueError('invalid_payload')
        prompt=bytes(payload)  # immutable before any async or DB boundary
        if not prompt or len(prompt)>4096:raise ValueError('invalid_payload')
        try:prompt=prompt.decode('utf-8')
        except UnicodeDecodeError:raise ValueError('invalid_payload') from None
        with self.scoped() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            with conn.cursor() as cur:
                voice.authorize(cur,{'tenant_id':self.tenant,'telegram_id':7},self.route)
                # Synthetic stand-in: real prompt projection + knowledge are
                # NOT yet read on this same connection/snapshot.
                source=cur.execute('SELECT body FROM saas_crm_messages WHERE tenant_id=%s ORDER BY external_id',(self.tenant,)).fetchall()
                if not source:raise ValueError('no_source')
                payload=json.dumps({'instruction':'synthetic only','source':[row['body'] for row in source],
                                    'employee_prompt':prompt},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
                digest=hashlib.sha256(payload).hexdigest()
                glob=cur.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
                scoped=cur.execute('SELECT epoch FROM synthetic_scope_epoch WHERE tenant=%s AND channel=%s AND actor=7',(self.tenant,self.channel)).fetchone()['epoch']
                q=cur.execute('SELECT * FROM synthetic_quote WHERE version=%s',(grant.QUOTE,)).fetchone()
                cur.execute('INSERT INTO synthetic_prepared VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (scope['request_id'],self.tenant,self.channel,7,self.conversation,grant._fingerprint(scope),digest,glob,scoped,q['version'],q['model'],q['hold'],q['expires_at']))
        return payload

    def clear(self,clear_id,scope,before_epoch=None):
        if not FLEET_VERIFIED:raise ValueError('legacy_fleet_unverified')
        self.assert_scope(scope)
        with self.scoped() as conn:
            with conn.cursor() as cur:
                voice.authorize(cur,{'tenant_id':self.tenant,'telegram_id':7},self.route)
                row=cur.execute('INSERT INTO synthetic_clear VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING request_id',
                    (clear_id,self.tenant,self.channel,7,self.conversation)).fetchone()
                if not row:
                    stored=cur.execute('SELECT tenant::text,channel::text,actor,conversation FROM synthetic_clear WHERE request_id=%s',(clear_id,)).fetchone()
                    if stored!={'tenant':self.tenant,'channel':self.channel,'actor':7,'conversation':self.conversation}:
                        raise ValueError('clear_scope_conflict')
                    return False
                cur.execute('DELETE FROM saas_operator_copilot_threads WHERE tenant_id=%s AND requested_by=7 AND channel_id=%s AND conversation_id=%s',
                            (self.tenant,self.channel,self.conversation))
                if before_epoch:before_epoch(cur.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'])
                cur.execute('UPDATE synthetic_scope_epoch SET epoch=epoch+1 WHERE tenant=%s AND channel=%s AND actor=7',
                            (self.tenant,self.channel))
                if cur.rowcount!=1:raise ValueError('scope_epoch_missing')
        return True

    def admit_two_phase(self,scope,payload,before_epoch=None,after_check=None):
        if not FLEET_VERIFIED:raise ValueError('legacy_fleet_unverified')
        self.assert_scope(scope)
        if not isinstance(payload,(bytes,bytearray,memoryview)):raise ValueError('invalid_payload')
        payload=bytes(payload)
        digest=hashlib.sha256(payload).hexdigest()
        with self.scoped() as conn:
            with conn.cursor() as cur:
                grant.reserve(cur,scope)
                if not grant.claim(cur,scope):raise ValueError('not_claimable')
                prep=cur.execute('SELECT * FROM synthetic_prepared WHERE request_id=%s FOR UPDATE',(scope['request_id'],)).fetchone()
                if not prep or prep['scope_hash']!=grant._fingerprint(scope) or prep['payload_hash']!=digest:
                    raise ValueError('prepared_mismatch')
                if before_epoch:before_epoch()
                glob=cur.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s FOR UPDATE',(self.tenant,)).fetchone()['epoch']
                scoped=cur.execute('SELECT epoch FROM synthetic_scope_epoch WHERE tenant=%s AND channel=%s AND actor=7 FOR UPDATE',(self.tenant,self.channel)).fetchone()['epoch']
                if glob!=prep['global_epoch'] or scoped!=prep['scope_epoch']:raise ValueError('stale_epoch')
                quote=cur.execute('''SELECT version,model,hold,expires_at,clock_timestamp() AS checked_at,
                  clock_timestamp()<expires_at AS valid FROM synthetic_quote WHERE version=%s FOR UPDATE''',(prep['quote_version'],)).fetchone()
                if (not quote or not quote['valid'] or quote['version']!=grant.QUOTE or quote['model']!=grant.MODEL
                    or quote['hold']!=grant.HOLD or prep['model']!=quote['model'] or prep['hold']!=quote['hold']
                    or prep['quote_expires']!=quote['expires_at']):
                    raise ValueError('quote_invalid')
                cur.execute('INSERT INTO synthetic_intent VALUES(%s,%s,%s,%s,%s,%s)',
                    (scope['request_id'],grant._fingerprint(scope),digest,'ready',quote['checked_at'],quote['version']))
                if after_check:after_check(cur.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'])
        # In any real transport only these exact immutable bytes may be sent;
        # neither this test nor this method performs an external operation.
        return payload

    def test_gate_denies_before_any_reservation(self):
        self.assertFalse(FLEET_VERIFIED)
        with self.assertRaisesRegex(ValueError,'legacy_fleet_unverified'):self.prepare(self.scope(),b'test')
        with self.assertRaisesRegex(ValueError,'legacy_fleet_unverified'):self.admit_two_phase(self.scope(),b'test')

    def test_scoped_clear_zero_rows_invalidates_and_is_idempotent(self):
        scope=self.scope();clear_id=str(uuid.uuid4())
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic payload')
            self.assertTrue(self.clear(clear_id,scope));self.assertFalse(self.clear(clear_id,scope))
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT epoch FROM synthetic_scope_epoch WHERE tenant=%s',(self.tenant,)).fetchone()['epoch'],2)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_operator_copilot_threads').fetchone()['n'],0)
            with self.assertRaisesRegex(ValueError,'stale_epoch'):self.admit_two_phase(scope,payload)
            with self.scoped() as conn:self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],0)

    def test_exact_immutable_payload_and_scope_are_bound(self):
        scope=self.scope();original=bytearray(b'original')
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            frozen=self.prepare(scope,original);original[:]=b'modified'
            with self.assertRaises(psycopg.errors.RaiseException):
                with self.scoped() as conn:conn.execute("UPDATE synthetic_prepared SET payload_hash='forged' WHERE request_id=%s",(scope['request_id'],))
            with self.assertRaisesRegex(ValueError,'prepared_mismatch'):self.admit_two_phase(scope,original)
            with self.assertRaisesRegex(ValueError,'snapshot_scope_mismatch'):self.admit_two_phase({**scope,'actor_id':8},frozen)
            output=self.admit_two_phase(scope,frozen);self.assertIsInstance(output,bytes);self.assertEqual(output,frozen)
            self.assertEqual(json.loads(output)['employee_prompt'],'original')
            with self.scoped() as conn:
                row=conn.execute('SELECT payload_hash,state FROM synthetic_intent WHERE request_id=%s',(scope['request_id'],)).fetchone()
                self.assertEqual(row['payload_hash'],hashlib.sha256(output).hexdigest());self.assertEqual(row['state'],'ready')

    def test_same_quote_version_cannot_silently_change_terms(self):
        scope=self.scope()
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic')
            with self.scoped() as conn:conn.execute("UPDATE synthetic_quote SET expires_at=expires_at+interval '1 second' WHERE version=%s",(grant.QUOTE,))
            with self.assertRaisesRegex(ValueError,'quote_invalid'):self.admit_two_phase(scope,payload)

    def test_duplicate_admission_commits_once(self):
        scope=self.scope();barrier=threading.Barrier(2)
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic')
            def attempt():
                barrier.wait(3)
                try:self.admit_two_phase(scope,payload);return 'committed'
                except ValueError as exc:return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(attempt);b=pool.submit(attempt)
                self.assertCountEqual([a.result(6),b.result(6)],['committed','not_claimable'])
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM synthetic_intent').fetchone()['n'],1)

    def test_message_knowledge_and_permission_change_invalidates(self):
        actions=(("UPDATE saas_crm_messages SET body='changed' WHERE tenant_id=%s",(self.tenant,)),
                 ("INSERT INTO saas_ai_knowledge_sources(tenant_id,id,title,active,agent_id,provenance) VALUES(%s,%s,'changed',true,NULL,'{}')",(self.tenant,str(uuid.uuid4()))),
                 ('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,)))
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            for statement,params in actions:
                self.conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s',(self.tenant,));self.conn.commit()
                scope=self.scope();payload=self.prepare(scope,b'synthetic')
                with self.scoped() as conn:conn.execute(statement,params)
                with self.assertRaisesRegex(ValueError,'stale_epoch'):self.admit_two_phase(scope,payload)

    def test_fake_fleet_verification_cannot_fence_old_clear(self):
        scope=self.scope()
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic')
            with self.scoped() as conn:
                result=conn.execute('DELETE FROM saas_operator_copilot_threads WHERE tenant_id=%s AND requested_by=7 AND channel_id=%s AND conversation_id=%s',(self.tenant,self.channel,self.conversation))
                self.assertEqual(result.rowcount,0)
            # Counterexample: a boolean pretending the fleet is migrated does
            # not make an old uninstrumented clear participate.
            self.assertEqual(self.admit_two_phase(scope,payload),payload)

    def test_two_phase_detects_message_aba(self):
        scope=self.scope()
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic')
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET body='transient' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET body='synthetic customer' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with self.assertRaisesRegex(ValueError,'stale_epoch'):self.admit_two_phase(scope,payload)

    def test_expired_quote_after_epoch_wait_rolls_back(self):
        with self.scoped() as conn:
            conn.execute("UPDATE synthetic_quote SET expires_at=clock_timestamp()+interval '1 second' WHERE version=%s",(grant.QUOTE,))
        scope=self.scope();reached=threading.Event()
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic')
            with self.scoped() as locker, ThreadPoolExecutor(max_workers=1) as pool:
                locker.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s FOR UPDATE',(self.tenant,))
                task=pool.submit(self.admit_two_phase,scope,payload,lambda:reached.set())
                self.assertTrue(reached.wait(2))
                threading.Event().wait(1.1)
                locker.commit()
                with self.assertRaisesRegex(ValueError,'quote_invalid'):task.result(3)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],0)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM synthetic_intent').fetchone()['n'],0)

    def test_quote_can_expire_after_final_check_before_commit(self):
        expiry=datetime.now(timezone.utc)+timedelta(seconds=1)
        with self.scoped() as conn:conn.execute('UPDATE synthetic_quote SET expires_at=%s WHERE version=%s',(expiry,grant.QUOTE))
        scope=self.scope()
        def delay(_pid):
            remaining=(expiry-datetime.now(timezone.utc)).total_seconds()
            if remaining>0:threading.Event().wait(remaining+.05)
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic');self.assertEqual(self.admit_two_phase(scope,payload,after_check=delay),payload)
        self.assertGreaterEqual(datetime.now(timezone.utc),expiry)

    def test_clear_after_locked_check_commits_after_intent(self):
        scope=self.scope();checked=threading.Event();release=threading.Event();clear_start=threading.Event();pids={}
        def after_check(pid):
            pids['admit']=pid;checked.set()
            if not release.wait(5):raise RuntimeError('admit release timeout')
        def before_epoch(pid):pids['clear']=pid;clear_start.set()
        with patch(__name__+'.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            payload=self.prepare(scope,b'synthetic')
            with ThreadPoolExecutor(max_workers=2) as pool:
                admission=pool.submit(self.admit_two_phase,scope,payload,None,after_check)
                self.assertTrue(checked.wait(3))
                clearing=pool.submit(self.clear,str(uuid.uuid4()),scope,before_epoch)
                try:
                    self.assertTrue(clear_start.wait(3));deadline=time.monotonic()+3;blocked=False
                    while time.monotonic()<deadline:
                        with self.scoped() as conn:
                            blockers=conn.execute('SELECT pg_blocking_pids(%s) AS ids',(pids['clear'],)).fetchone()['ids']
                        if pids['admit'] in blockers:blocked=True;break
                        time.sleep(.01)
                    self.assertTrue(blocked,'clear did not wait for scope epoch lock')
                finally:release.set()
                self.assertEqual(admission.result(6),payload);self.assertTrue(clearing.result(6))
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT epoch FROM synthetic_scope_epoch WHERE tenant=%s',(self.tenant,)).fetchone()['epoch'],2)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM synthetic_intent').fetchone()['n'],1)
