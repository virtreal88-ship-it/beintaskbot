"""Distributed lease on isolated localhost PostgreSQL only; provider absent."""
import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_real_authority_pg import RealVoiceAuthority
import tenant_operator_voice_lease as lease
from tenant_linear_policy import TenantLinearError

ROOT=Path(__file__).resolve().parents[2]


class LeasePostgres(RealVoiceAuthority):
    def setUp(self):
        super().setUp()
        self.conn.execute((ROOT/'experiments/voice_session_lease/synthetic_schema.sql').read_text());self.conn.commit()
        self.profile={'tenant_id':self.tenant,'telegram_id':7}
        self.expected=self.admit();self.conn.commit()
        self.scope=(self.tenant,7,self.channel,self.conversation)

    def tearDown(self):
        self.conn.rollback();self.conn.execute('DROP TABLE IF EXISTS experiment_voice_session_lease');self.conn.commit()
        super().tearDown()

    def scoped(self):
        conn=self.connect();conn.execute('SET search_path TO '+self.schema);return conn

    def claim(self,session=None,profile=None,route=None,expected=None,scope=None):
        with self.scoped() as conn,conn.cursor() as cur:
            return lease._claim(cur,profile or self.profile,route or self.route,expected or self.expected,
                                scope or self.scope,session or str(uuid.uuid4()))

    def beat(self,fence,profile=None,route=None,expected=None,scope=None):
        with self.scoped() as conn,conn.cursor() as cur:
            error=lease._heartbeat(cur,profile or self.profile,route or self.route,expected or self.expected,scope or self.scope,fence)
        if error:raise lease.LeaseUnavailable(error)

    def test_gate_and_recovery_are_unconditionally_closed(self):
        with self.assertRaisesRegex(lease.LeaseUnavailable,'lease_unavailable'):self.claim()
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            fence=self.claim()
            with self.assertRaisesRegex(lease.LeaseUnavailable,'reconciliation_unverified'):
                lease._release(self.scope,fence)
            with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):self.claim()

    def test_concurrent_tabs_have_one_winner_and_no_automatic_steal(self):
        barrier=threading.Barrier(8)
        def worker():
            barrier.wait(4)
            try:self.claim();return 'won'
            except lease.LeaseUnavailable as e:return str(e)
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True),ThreadPoolExecutor(max_workers=8) as pool:
            outcomes=list(pool.map(lambda _:worker(),range(8)))
        self.assertEqual(outcomes.count('won'),1)
        self.assertEqual(outcomes.count('voice_session_already_active_or_unverified'),7)
        with self.scoped() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],1)

    def test_heartbeat_updates_only_matching_live_fence(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            fence=self.claim()
            with self.scoped() as conn:
                before=conn.execute('SELECT heartbeat_until FROM experiment_voice_session_lease').fetchone()['heartbeat_until']
                conn.execute("UPDATE experiment_voice_session_lease SET heartbeat_until=clock_timestamp()+interval '1 second'")
            self.beat(fence)
            with self.scoped() as conn:
                after=conn.execute('SELECT heartbeat_until FROM experiment_voice_session_lease').fetchone()['heartbeat_until']
            self.assertGreater(after,before.replace(microsecond=0))
            bad=lease.Fence(fence.session_id,b'x'*32)
            with self.assertRaisesRegex(lease.LeaseUnavailable,'expired_or_unverified'):self.beat(bad)
            with self.assertRaisesRegex(lease.LeaseUnavailable,'scope_invalid'):self.beat(fence,scope=(self.tenant,7,self.channel,'other'))

    def test_expiry_stops_heartbeat_but_never_reopens_session(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            fence=self.claim()
            with self.scoped() as conn:conn.execute("UPDATE experiment_voice_session_lease SET heartbeat_until=clock_timestamp()-interval '1 second'")
            with self.assertRaisesRegex(lease.LeaseUnavailable,'expired_or_unverified'):self.beat(fence)
            with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):self.claim()
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],1)

    def test_observed_expiry_stays_quarantined_even_if_wall_clock_moves_back(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            fence=self.claim()
            with self.scoped() as conn:conn.execute("UPDATE experiment_voice_session_lease SET heartbeat_until=clock_timestamp()-interval '1 second'")
            with self.assertRaisesRegex(lease.LeaseUnavailable,'expired_or_unverified'):self.beat(fence)
            # Equivalent ordering relation to a backwards clock step. This
            # changes only a synthetic row, never host or database time.
            with self.scoped() as conn:
                state=conn.execute("UPDATE experiment_voice_session_lease SET heartbeat_until=clock_timestamp()+interval '1 hour' RETURNING state").fetchone()['state']
            self.assertEqual(state,'quarantined')
            with self.assertRaisesRegex(lease.LeaseUnavailable,'expired_or_unverified'):self.beat(fence)

    def test_heartbeat_waiting_on_lock_cannot_extend_expired_lease(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            fence=self.claim();started=threading.Event()
            def beat():
                started.set()
                try:self.beat(fence);return 'extended'
                except lease.LeaseUnavailable as error:return str(error)
            with self.scoped() as holding:
                holding.execute("UPDATE experiment_voice_session_lease SET heartbeat_until=clock_timestamp()+interval '0.1 seconds'")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending=pool.submit(beat);self.assertTrue(started.wait(2))
                    holding.execute('SELECT pg_sleep(0.25)')
                    holding.commit()
                    self.assertEqual(pending.result(3),'voice_session_expired_or_unverified')
            with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):self.claim()

    def test_lost_claim_result_and_process_death_are_not_retried(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            session=str(uuid.uuid4());self.claim(session=session)  # commit, lose fence/result
            for _ in range(2):
                with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):self.claim(session=session)
            with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):self.claim()

    def test_revocation_and_assignment_change_stop_heartbeat_keep_scope_locked(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            fence=self.claim()
            with self.scoped() as conn:conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            with self.assertRaisesRegex(lease.LeaseUnavailable,'authority_changed'):self.beat(fence)
            with self.scoped() as conn:conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            # Revocation observed once is terminal even if permissions return.
            with self.assertRaisesRegex(lease.LeaseUnavailable,'expired_or_unverified'):self.beat(fence)
            with self.scoped() as conn:conn.execute('UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s',(self.tenant,))
            with self.assertRaisesRegex(lease.LeaseUnavailable,'authority_changed'):self.beat(fence)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],1)

    def test_other_staff_and_tenant_not_blocked(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            self.claim()
            manager=self.admit(actor=9);self.conn.commit()
            manager_profile={'tenant_id':self.tenant,'telegram_id':9}
            self.claim(profile=manager_profile,expected=manager,scope=(self.tenant,9,self.channel,self.conversation))
            other_channel=str(uuid.uuid4());other_agent=str(uuid.uuid4())
            with self.scoped() as conn:
                conn.execute('INSERT INTO saas_connect_conversations VALUES(%s,%s,%s,41)',(self.other,other_channel,self.conversation))
                conn.execute("INSERT INTO saas_tenant_integrations VALUES(%s,'chat_ai',%s::jsonb,now(),%s)",
                             (self.other,'{"enabled":true,"consent":true,"audio_enabled":true}',b'synthetic'))
                conn.execute("INSERT INTO saas_ai_agent_settings VALUES(%s,false,'synthetic')",(self.other,))
                conn.execute("INSERT INTO saas_ai_agents VALUES(%s,%s,1,'synthetic','valid-model','synthetic','auto','plain',NULL)",(self.other,other_agent))
                conn.execute("INSERT INTO saas_ai_conversation_state VALUES(%s,'connect',%s,%s,%s,1)",(self.other,other_channel,self.conversation,other_agent))
            other_profile={'tenant_id':self.other,'telegram_id':7};other_route={**self.route,'channel_id':other_channel}
            with self.scoped() as conn:
                from tenant_operator_voice_context import authorize
                other_expected=authorize(conn.cursor(),other_profile,other_route)
            self.claim(profile=other_profile,route=other_route,expected=other_expected,
                       scope=(self.other,7,other_channel,self.conversation))
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],3)

    def test_same_actor_cannot_claim_another_dialog(self):
        new_dialog='different@s.whatsapp.net'
        with self.scoped() as conn:
            conn.execute('INSERT INTO saas_connect_conversations VALUES(%s,%s,%s,41)',(self.tenant,self.channel,new_dialog))
            conn.execute("INSERT INTO saas_ai_conversation_state VALUES(%s,'connect',%s,%s,%s,1)",
                         (self.tenant,self.channel,new_dialog,self.agent))
        other_route={**self.route,'conversation_id':new_dialog}
        with self.scoped() as conn:
            from tenant_operator_voice_context import authorize
            other_expected=authorize(conn.cursor(),self.profile,other_route)
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            self.claim()
            with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):
                self.claim(route=other_route,expected=other_expected,scope=(self.tenant,7,self.channel,new_dialog))

    def test_lost_claim_transaction_does_not_leave_lease(self):
        with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True):
            with self.scoped() as conn:
                lease._claim(conn.cursor(),self.profile,self.route,self.expected,self.scope,str(uuid.uuid4()))
                conn.rollback() # provider has not been opened or exposed
            self.claim()

    def test_session_async_interface_only_succeeds_after_commit(self):
        async def run():
            with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(lease,'_connect',self.scoped):
                adapter=lease.ServerLease(self.profile,self.route,self.expected,str(uuid.uuid4()))
                fence=await adapter.claim(self.scope)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],1)
                await adapter.assert_current(self.scope,fence)
                with self.assertRaisesRegex(lease.LeaseUnavailable,'reconciliation_unverified'):
                    await adapter.release(self.scope,fence)
        asyncio.run(run())

    def test_cancelled_claim_may_commit_and_must_remain_fenced(self):
        entered=threading.Event();proceed=threading.Event();original=lease._claim
        def delayed(*args):
            fence=original(*args);entered.set()
            if not proceed.wait(3):raise RuntimeError('synthetic timeout')
            return fence
        async def run():
            with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(lease,'_connect',self.scoped),patch.object(lease,'_claim',delayed):
                adapter=lease.ServerLease(self.profile,self.route,self.expected,str(uuid.uuid4()))
                task=asyncio.create_task(adapter.claim(self.scope))
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                proceed.set()
                for _ in range(100):
                    with self.scoped() as conn:
                        count=conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n']
                    if count:break
                    await asyncio.sleep(.01)
                self.assertEqual(count,1)
                with self.assertRaisesRegex(lease.LeaseUnavailable,'session_terminal'):
                    await adapter.claim(self.scope)
                fresh=lease.ServerLease(self.profile,self.route,self.expected,str(uuid.uuid4()))
                with self.assertRaisesRegex(lease.LeaseUnavailable,'already_active_or_unverified'):
                    await fresh.claim(self.scope)
        try:asyncio.run(run())
        finally:proceed.set()

    def test_revocation_rollback_fences_adapter_but_not_a_recreated_adapter(self):
        """The last assertion demonstrates an outstanding distributed gap."""
        class RollbackConnection:
            def __init__(self,conn):self.conn=conn
            def __enter__(self):return self.conn
            def __exit__(self,*_):
                self.conn.rollback();self.conn.close()
                raise RuntimeError('synthetic_commit_failure')
        async def run():
            with patch.object(lease,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(lease,'_connect',self.scoped):
                sid=str(uuid.uuid4());adapter=lease.ServerLease(self.profile,self.route,self.expected,sid)
                fence=await adapter.claim(self.scope)
                with self.scoped() as conn:conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                with patch.object(lease,'_connect',lambda:RollbackConnection(self.scoped())):
                    with self.assertRaisesRegex(RuntimeError,'synthetic_commit_failure'):
                        await adapter.assert_current(self.scope,fence)
                with self.scoped() as conn:conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                with self.assertRaisesRegex(lease.LeaseUnavailable,'session_terminal'):
                    await adapter.assert_current(self.scope,fence)
                # No durable quarantine was committed. If trusted code
                # improperly transfers the fence to a recreated adapter, it
                # can be reused. This is a blocker, not a supported recovery.
                recreated=lease.ServerLease(self.profile,self.route,self.expected,sid)
                await recreated.assert_current(self.scope,fence)
        asyncio.run(run())
