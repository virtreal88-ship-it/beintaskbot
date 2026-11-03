"""Synthetic immutable-byte dispatch and counterexamples, never a provider."""
import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
import tenant_operator_voice_context as voice
import tenant_voice_test_grant as grant
import tenant_ai_spend_budget as spend
from tenant_linear_policy import TenantLinearError
import test_two_phase
from test_two_phase import TwoPhase


class BoundDispatch(TwoPhase):
    def setUp(self):
        super().setUp()
        self.conn.execute('''
          CREATE TABLE synthetic_dispatch(request_id uuid PRIMARY KEY,scope_hash text NOT NULL,
            payload bytea NOT NULL,payload_hash text NOT NULL,state text NOT NULL);
          CREATE FUNCTION protect_synthetic_dispatch() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'dispatch immutable'; END IF;
            IF NEW.request_id IS DISTINCT FROM OLD.request_id OR NEW.scope_hash IS DISTINCT FROM OLD.scope_hash
              OR NEW.payload IS DISTINCT FROM OLD.payload OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash THEN
              RAISE EXCEPTION 'dispatch immutable';
            END IF;
            IF NOT (OLD.state='ready' AND NEW.state='unknown') THEN RAISE EXCEPTION 'invalid transition'; END IF;
            RETURN NEW;
          END $$;
          CREATE TRIGGER immutable_dispatch BEFORE UPDATE OR DELETE ON synthetic_dispatch
            FOR EACH ROW EXECUTE FUNCTION protect_synthetic_dispatch();
        ''')
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('DROP TABLE IF EXISTS synthetic_dispatch')
        self.conn.execute('DROP FUNCTION IF EXISTS protect_synthetic_dispatch()')
        self.conn.commit();super().tearDown()

    def prepare_bound(self,scope,prompt):
        payload=self.prepare(scope,prompt)
        with self.scoped() as conn:
            conn.execute('INSERT INTO synthetic_dispatch VALUES(%s,%s,%s,%s,%s)',
              (scope['request_id'],grant._fingerprint(scope),payload,hashlib.sha256(payload).hexdigest(),'ready'))
        return payload

    def arm_once(self,scope,before_commit=None):
        # Mock DB boundary only; no real socket or provider code. A consumed
        # dispatch stays unknown even if the caller crashes before send.
        if not test_two_phase.FLEET_VERIFIED:raise ValueError('legacy_fleet_unverified')
        self.assert_scope(scope)
        grant._ready()
        with self.scoped() as conn:
            with conn.cursor() as cur:
                grant._lock(cur)
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+self.tenant,))
                voice.authorize(cur,{'tenant_id':self.tenant,'telegram_id':7},self.route)
                glob=cur.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s FOR UPDATE',(self.tenant,)).fetchone()['epoch']
                scoped=cur.execute('SELECT epoch FROM synthetic_scope_epoch WHERE tenant=%s AND channel=%s AND actor=7 FOR UPDATE',(self.tenant,self.channel)).fetchone()['epoch']
                prep=cur.execute('SELECT * FROM synthetic_prepared WHERE request_id=%s',(scope['request_id'],)).fetchone()
                intent=cur.execute('SELECT * FROM synthetic_intent WHERE request_id=%s FOR UPDATE',(scope['request_id'],)).fetchone()
                row=cur.execute('SELECT * FROM synthetic_dispatch WHERE request_id=%s FOR UPDATE',(scope['request_id'],)).fetchone()
                hold=cur.execute('SELECT state,fingerprint,tenant_reservation_id FROM saas_voice_test_hold WHERE request_id=%s FOR UPDATE',(scope['request_id'],)).fetchone()
                fingerprint=grant._fingerprint(scope)
                if not prep or not intent or not row or not hold or row['state']!='ready':raise ValueError('dispatch_unavailable')
                if (prep['scope_hash']!=fingerprint or intent['scope_hash']!=fingerprint or row['scope_hash']!=fingerprint
                    or hold['fingerprint']!=fingerprint or hold['state']!='claimed'):raise ValueError('scope_or_hold_mismatch')
                tenant_hold=cur.execute('''SELECT kind,model,reserved_microusd,state FROM saas_ai_spend_reservations
                    WHERE tenant_id=%s AND id=%s AND call_key=%s FOR UPDATE''',
                    (self.tenant,hold['tenant_reservation_id'],'voice-test:'+scope['request_id'])).fetchone()
                if (not tenant_hold or tenant_hold['kind']!='realtime_voice_test' or tenant_hold['model']!=grant.MODEL
                    or tenant_hold['reserved_microusd']!=grant.HOLD or tenant_hold['state']!='unknown'):
                    raise ValueError('tenant_hold_mismatch')
                cap=spend.limit_microusd(self.tenant)
                if cap:
                    used=cur.execute('''SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
                        ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used
                        FROM saas_ai_spend_reservations WHERE tenant_id=%s''',(self.tenant,)).fetchone()['used']
                    if int(used)>cap:raise ValueError('tenant_budget_exhausted')
                payload=bytes(row['payload']);digest=hashlib.sha256(payload).hexdigest()
                if (digest!=row['payload_hash'] or digest!=prep['payload_hash'] or digest!=intent['payload_hash']):
                    raise ValueError('payload_mismatch')
                if glob!=prep['global_epoch'] or scoped!=prep['scope_epoch']:raise ValueError('stale_epoch')
                q=cur.execute('''SELECT version,model,hold,expires_at,clock_timestamp()<expires_at AS valid
                  FROM synthetic_quote WHERE version=%s FOR UPDATE''',(prep['quote_version'],)).fetchone()
                if (not q or not q['valid'] or q['version']!=grant.QUOTE or q['model']!=grant.MODEL
                    or q['hold']!=grant.HOLD or q['expires_at']!=prep['quote_expires']):raise ValueError('quote_invalid')
                result=cur.execute("UPDATE synthetic_dispatch SET state='unknown' WHERE request_id=%s AND state='ready' RETURNING request_id",(scope['request_id'],)).fetchone()
                if not result:raise ValueError('dispatch_unavailable')
                held=grant.mark_unknown(cur,scope)
                if not held or held.get('state')!='unknown':raise ValueError('hold_reconciliation_failed')
                if before_commit:before_commit()
        return payload

    @staticmethod
    def fake_sink(armed_bytes,collector):
        # No socket, API or connection; represents a non-cooperating provider.
        assert type(armed_bytes) is bytes
        collector.append(armed_bytes)

    def activate_test_only(self):
        return (patch('test_two_phase.FLEET_VERIFIED',True),patch.object(grant,'ENFORCEMENT_VERIFIED',True),
                patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0))

    def test_payload_scope_immutable_one_shot_and_unknown_on_crash(self):
        scope=self.scope()
        a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload)
            with self.assertRaises(psycopg.errors.RaiseException):
                with self.scoped() as conn:conn.execute("UPDATE synthetic_dispatch SET payload=%s WHERE request_id=%s",(b'forged',scope['request_id']))
            with self.assertRaisesRegex(ValueError,'snapshot_scope_mismatch'):self.arm_once({**scope,'conversation_id':'different'})
            armed=self.arm_once(scope);self.assertEqual(armed,payload)
            # Simulate process death before send: a subsequent attempt cannot
            # distinguish it from death immediately after remote acceptance.
            with self.assertRaisesRegex(ValueError,'dispatch_unavailable'):self.arm_once(scope)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT state FROM synthetic_dispatch').fetchone()['state'],'unknown')
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'unknown')

    def assert_change_before_arm(self,action):
        scope=self.scope();a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload)
            if action=='clear':self.clear(str(uuid.uuid4()),scope)
            else:
                with self.scoped() as conn:
                    if action=='revoke':conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                    else:conn.execute("UPDATE saas_crm_messages SET body='changed' WHERE tenant_id=%s",(self.tenant,))
            with self.assertRaises((ValueError,TenantLinearError)):self.arm_once(scope)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT state FROM synthetic_dispatch WHERE request_id=%s',(scope['request_id'],)).fetchone()['state'],'ready')
            # Test-only conservative hold reconciliation after denial.
            with self.scoped() as conn,conn.cursor() as cur:grant.mark_unknown(cur,scope)

    def test_revoke_before_arm_refused(self):self.assert_change_before_arm('revoke')
    def test_clear_before_arm_refused(self):self.assert_change_before_arm('clear')
    def test_message_before_arm_refused(self):self.assert_change_before_arm('message')

    def test_disabled_grant_before_arm_refused(self):
        scope=self.scope();a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload)
            with self.scoped() as conn:conn.execute('UPDATE saas_voice_test_grant SET enabled=false')
            with self.assertRaises(TenantLinearError):self.arm_once(scope)

    def test_competing_arm_has_one_winner(self):
        scope=self.scope();barrier=threading.Barrier(2);a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload)
            def attempt():
                barrier.wait(3)
                try:self.arm_once(scope);return 'armed'
                except ValueError as exc:return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                one=pool.submit(attempt);two=pool.submit(attempt)
                self.assertCountEqual([one.result(6),two.result(6)],['armed','dispatch_unavailable'])

    def test_failed_arm_transaction_rolls_back_dispatch_and_hold_together(self):
        scope=self.scope();a,b,c,d=self.activate_test_only()
        def fail():raise RuntimeError('synthetic commit interruption')
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload)
            with self.assertRaisesRegex(RuntimeError,'synthetic commit interruption'):self.arm_once(scope,fail)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT state FROM synthetic_dispatch').fetchone()['state'],'ready')
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold').fetchone()['state'],'claimed')
            self.assertEqual(self.arm_once(scope),payload)

    def test_nonparticipating_sink_can_ignore_bound_bytes(self):
        # Proof of a real integration requirement, not a functional provider.
        scope=self.scope();received=[];a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload)
            armed=self.arm_once(scope)
            self.fake_sink(b'other bytes chosen by buggy caller',received)
            self.assertNotEqual(received,[armed])
            with self.scoped() as conn:
                recorded=conn.execute('SELECT payload_hash FROM synthetic_dispatch WHERE request_id=%s',(scope['request_id'],)).fetchone()['payload_hash']
                self.assertNotEqual(recorded,hashlib.sha256(received[0]).hexdigest())

    def test_untracked_pipeline_and_stage_do_not_bump_fixture_epoch(self):
        # Both tables are read by the real workflow loader; this test proves
        # only this synthetic trigger inventory's omission, not an auth bypass.
        before=self.snapshot()
        with self.scoped() as conn:
            conn.execute("INSERT INTO saas_tenant_pipelines VALUES(%s,101,'synthetic',true,7,'{}')",(self.tenant,))
            conn.execute("INSERT INTO saas_tenant_pipeline_stages VALUES(%s,101,202,'synthetic',1,'open','{}')",(self.tenant,))
            after=conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
        self.assertEqual(before,after)

    def test_untracked_identity_event_does_not_bump_fixture_epoch(self):
        # Illustrative table; full actual identity event semantics are not
        # recreated here, and no production effect is asserted.
        before=self.snapshot()
        self.conn.execute('CREATE TABLE saas_connect_identity_conflict_events(tenant_id uuid,conversation_id text)')
        self.conn.commit()
        try:
            with self.scoped() as conn:
                conn.execute('INSERT INTO saas_connect_identity_conflict_events VALUES(%s,%s)',(self.tenant,self.conversation))
                after=conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
            self.assertEqual(before,after)
        finally:
            self.conn.execute('DROP TABLE saas_connect_identity_conflict_events');self.conn.commit()

    def test_synthetic_extension_bumps_epoch_for_six_omitted_tables(self):
        # This isolated fixture extension covers synthetic inserts only.
        # It does not change TABLES, implement production triggers, fence
        # zero-row clear or establish an exhaustive writer inventory.
        extras=('saas_connect_owner_identity_assertions','saas_connect_identity_conflict_events',
                'saas_crm_native_contact_conversations','saas_crm_native_contact_identities')
        tracked=('saas_tenant_pipelines','saas_tenant_pipeline_stages')+extras
        before=self.snapshot()
        try:
            for table in extras:self.conn.execute(f'CREATE TABLE {table}(tenant_id uuid,id uuid)')
            for table in tracked:
                self.conn.execute(f'CREATE TRIGGER review_extension AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION bump_epoch()')
            self.conn.commit()
            with self.scoped() as conn:
                conn.execute("INSERT INTO saas_tenant_pipelines VALUES(%s,101,'synthetic',true,7,'{}')",(self.tenant,))
                conn.execute("INSERT INTO saas_tenant_pipeline_stages VALUES(%s,101,202,'synthetic',1,'open','{}')",(self.tenant,))
                for table in extras:conn.execute(f'INSERT INTO {table} VALUES(%s,%s)',(self.tenant,str(uuid.uuid4())))
                after=conn.execute('SELECT epoch FROM experiment_epoch WHERE tenant_id=%s',(self.tenant,)).fetchone()['epoch']
            self.assertEqual(after,before+6)
        finally:
            self.conn.rollback()
            for table in tracked:self.conn.execute(f'DROP TRIGGER IF EXISTS review_extension ON {table}')
            for table in extras:self.conn.execute(f'DROP TABLE IF EXISTS {table}')
            self.conn.commit()

    def test_revocation_after_arm_cannot_retract_exact_bytes(self):
        scope=self.scope();received=[];a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload);armed=self.arm_once(scope)
            with self.scoped() as conn:conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            self.fake_sink(armed,received)
            self.assertEqual(received,[payload])
            with self.assertRaises((ValueError,TenantLinearError)):self.arm_once(scope)

    def test_quote_expiry_after_arm_cannot_be_enforced_by_local_commit(self):
        scope=self.scope();received=[];expiry=datetime.now(timezone.utc)+timedelta(seconds=1)
        with self.scoped() as conn:conn.execute('UPDATE synthetic_quote SET expires_at=%s WHERE version=%s',(expiry,grant.QUOTE))
        a,b,c,d=self.activate_test_only()
        with a,b,c,d:
            payload=self.prepare_bound(scope,b'synthetic');self.admit_two_phase(scope,payload);armed=self.arm_once(scope)
            wait=(expiry-datetime.now(timezone.utc)).total_seconds()
            if wait>0:threading.Event().wait(wait+.05)
            self.fake_sink(armed,received)
            self.assertGreaterEqual(datetime.now(timezone.utc),expiry)
            self.assertEqual(received,[payload])
