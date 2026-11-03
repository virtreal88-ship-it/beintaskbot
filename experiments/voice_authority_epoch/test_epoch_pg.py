"""Synthetic authority epoch + real lease and Session; never uses a provider."""
import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
from test_lease_pg import LeasePostgres
from epoch_lease import SyntheticEpochLease,SyntheticSnapshotContext
from test_session import Authority, Ledger, Context, Provider, Client
from tenant_operator_voice_session import Session
import tenant_operator_voice_lease as base


# Only tables present in this disposable direct-dialog fixture. Deliberately
# incomplete: real voice authorize also reads group and owner identity tables.
AUTH_FIXTURE_TABLES=(
    'saas_tenants','saas_tenant_members','saas_tenant_pipelines','saas_tenant_pipeline_stages',
    'saas_tenant_workflow_policies','saas_connect_conversations','saas_connect_identity_conflicts',
    'saas_connect_direct_identities','saas_connect_dialog_links','saas_crm_messages',
    'saas_tenant_integrations','saas_ai_agent_settings','saas_ai_agents','saas_ai_conversation_state',
)


class EpochLeasePostgres(LeasePostgres):
    def setUp(self):
        super().setUp()
        self.conn.execute('''ALTER TABLE experiment_voice_session_lease ADD COLUMN authority_epoch bigint;
          CREATE TABLE experiment_authority_epoch(tenant_id uuid PRIMARY KEY,revision bigint NOT NULL);
          CREATE FUNCTION experiment_bump_authority_epoch() RETURNS trigger LANGUAGE plpgsql AS $$
          DECLARE old_tenant uuid;new_tenant uuid;
          BEGIN
            IF TG_TABLE_NAME='saas_tenants' THEN
              IF TG_OP<>'INSERT' THEN old_tenant:=OLD.id; END IF;
              IF TG_OP<>'DELETE' THEN new_tenant:=NEW.id; END IF;
            ELSE
              IF TG_OP<>'INSERT' THEN old_tenant:=OLD.tenant_id; END IF;
              IF TG_OP<>'DELETE' THEN new_tenant:=NEW.tenant_id; END IF;
            END IF;
            IF old_tenant IS NOT NULL AND new_tenant IS NOT NULL AND old_tenant<>new_tenant THEN
              RAISE EXCEPTION 'synthetic tenant move is unsupported';
            END IF;
            INSERT INTO experiment_authority_epoch VALUES(coalesce(new_tenant,old_tenant),1)
              ON CONFLICT(tenant_id) DO UPDATE SET revision=experiment_authority_epoch.revision+1;
            RETURN COALESCE(NEW,OLD);
          END $$;''')
        self.conn.execute('INSERT INTO experiment_authority_epoch VALUES(%s,1),(%s,1)',(self.tenant,self.other))
        for table in AUTH_FIXTURE_TABLES:
            self.conn.execute(f'''CREATE TRIGGER epoch_bump AFTER INSERT OR UPDATE OR DELETE ON {table}
              FOR EACH ROW EXECUTE FUNCTION experiment_bump_authority_epoch()''')
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        for table in AUTH_FIXTURE_TABLES:self.conn.execute('DROP TRIGGER IF EXISTS epoch_bump ON '+table)
        self.conn.execute('DROP FUNCTION IF EXISTS experiment_bump_authority_epoch()')
        self.conn.execute('DROP TABLE IF EXISTS experiment_authority_epoch')
        self.conn.commit();super().tearDown()

    def adapter(self):
        return SyntheticEpochLease(self.profile,self.route,self.expected,str(uuid.uuid4()))

    def context(self,adapter):
        return SyntheticSnapshotContext(self.profile,self.route,self.expected,adapter)

    def revision(self,tenant=None):
        with self.scoped() as conn:
            return conn.execute('SELECT revision FROM experiment_authority_epoch WHERE tenant_id=%s',(tenant or self.tenant,)).fetchone()['revision']

    def flip_member(self):
        for active in (False,True):
            with self.scoped() as conn:
                conn.execute('UPDATE saas_tenant_members SET active=%s WHERE tenant_id=%s AND telegram_id=7',(active,self.tenant))

    def test_actual_member_revoke_regrant_invalidates_epoch_and_fence(self):
        async def run():
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter();await self.context(adapter).read_authorized(self.scope)
                fence=await adapter.claim(self.scope);before=self.revision()
                self.flip_member();self.assertEqual(self.revision(),before+2)
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):
                    await adapter.assert_current(self.scope,fence)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT state FROM experiment_voice_session_lease').fetchone()['state'],'quarantined')
                with self.assertRaisesRegex(base.LeaseUnavailable,'session_terminal'):
                    await adapter.assert_current(self.scope,fence)
                fresh=self.adapter()
                await self.context(fresh).read_authorized(self.scope)
                with self.assertRaisesRegex(base.LeaseUnavailable,'already_active_or_unverified'):
                    await fresh.claim(self.scope)
        asyncio.run(run())

    def test_revoke_regrant_after_snapshot_before_claim_refuses_provider_open(self):
        async def run():
            ledger=Ledger();provider=Provider(ledger);client=Client()
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter();context=self.context(adapter);original=context.read_authorized
                async def capture_then_aba(scope):
                    projection=await original(scope);self.flip_member();return projection
                context.read_authorized=capture_then_aba
                session=Session(self.scope,authority=Authority(),ledger=ledger,lease=adapter,context=context,
                                provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_snapshot_epoch_changed'):
                    await session.start()
                self.assertIsNone(provider.config)
                self.assertNotIn('ready',[event.get('type') for event in client.events])
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],0)
        asyncio.run(run())

    def test_revoke_regrant_after_claim_can_reach_mock_open(self):
        """Expected counterexample in the e585e23 Session start ordering."""
        async def run():
            from tenant_operator_voice_orchestration import ScopedAuthority
            import tenant_operator_voice_context as voice
            actual=ScopedAuthority(self.profile,self.route,self.expected)
            class RevokeAfterClaim:
                async def check(inner,scope,stage):
                    if stage=='dispatch':self.flip_member()
                    await actual.check(scope,stage)
            ledger=Ledger();provider=Provider(ledger);client=Client();seen=[]
            async def record_open(snapshot,**config):seen.append(snapshot)
            provider.open=record_open
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped),patch.object(voice,'_connect',self.scoped):
                adapter=self.adapter()
                session=Session(self.scope,authority=RevokeAfterClaim(),ledger=ledger,lease=adapter,context=self.context(adapter),
                                provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):
                    await session.start()
                # Epoch is rechecked after open, so the mock has already seen
                # the snapshot before this old Session reports the failure.
                self.assertEqual(len(seen),1)
                self.assertNotIn('ready',[event.get('type') for event in client.events])
                self.assertTrue(provider.closed)
        asyncio.run(run())

    def test_rolled_back_change_does_not_invalidate_captured_epoch(self):
        async def run():
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter();await self.context(adapter).read_authorized(self.scope);before=self.revision()
                with self.scoped() as conn:
                    conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                    conn.rollback()
                self.assertEqual(self.revision(),before)
                fence=await adapter.claim(self.scope);self.assertIsNotNone(fence)
        asyncio.run(run())

    def test_unknown_committed_change_still_invalidates_captured_epoch(self):
        async def run():
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter();await self.context(adapter).read_authorized(self.scope)
                conn=self.scoped()
                try:
                    try:
                        conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                        conn.commit()
                        raise OSError('synthetic_unknown_commit_after_real_commit')
                    except OSError:pass
                finally:conn.close()
                with self.scoped() as conn:
                    conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_snapshot_epoch_changed'):
                    await adapter.claim(self.scope)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],0)
        asyncio.run(run())

    def test_split_connections_can_bind_old_content_to_new_epoch(self):
        """Expected counterexample: separately collected epoch is unsafe."""
        with self.scoped() as conn:
            conn.execute('INSERT INTO saas_crm_messages VALUES(%s,%s,%s::jsonb)',(self.tenant,'synthetic','{"text":"before"}'))
        class SplitContext(Context):
            async def read_authorized(inner,scope):
                with self.scoped() as conn:
                    raw=conn.execute("SELECT raw FROM saas_crm_messages WHERE tenant_id=%s AND channel='synthetic'",(self.tenant,)).fetchone()['raw']
                with self.scoped() as conn:
                    conn.execute("UPDATE saas_crm_messages SET raw='{\"text\":\"after\"}' WHERE tenant_id=%s AND channel='synthetic'",(self.tenant,))
                # Unsafe: the epoch is now newer than the already-read content.
                adapter.bind_snapshot(scope,self.revision())
                return {'existing_copilot_instructions':'synthetic','synthetic_rows':[raw]}
        async def run():
            ledger=Ledger();provider=Provider(ledger);seen=[]
            async def record_open(snapshot,**config):seen.append(snapshot)
            provider.open=record_open
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                nonlocal adapter
                adapter=self.adapter()
                session=Session(self.scope,authority=Authority(),ledger=ledger,lease=adapter,context=SplitContext(),
                                provider=provider,client=Client(),max_seconds=4,idle_seconds=3,poll_seconds=.5)
                await session.start()
                try:
                    self.assertEqual(seen[0]['synthetic_rows'][0]['text'],'before')
                    with self.scoped() as conn:
                        self.assertEqual(conn.execute("SELECT raw FROM saas_crm_messages WHERE tenant_id=%s AND channel='synthetic'",(self.tenant,)).fetchone()['raw']['text'],'after')
                finally:await session.end('test')
        adapter=None
        asyncio.run(run())

    def test_fixture_workflow_and_identity_changes_increment_same_tenant_epoch(self):
        before=self.revision();other_before=self.revision(self.other)
        operations=[
          ('INSERT INTO saas_tenant_pipelines VALUES(%s,1,\'synthetic\',true,NULL,\'{}\')',(self.tenant,)),
          ('INSERT INTO saas_tenant_pipeline_stages VALUES(%s,1,1,\'synthetic\',1,\'open\',\'{}\')',(self.tenant,)),
          ('UPDATE saas_tenant_workflow_policies SET value=\'{}\' WHERE tenant_id=%s',(self.tenant,)),
          ('INSERT INTO saas_connect_identity_conflicts VALUES(%s,%s,%s)',(self.tenant,self.channel,self.conversation)),
          ('UPDATE saas_ai_agents SET version=version+1 WHERE tenant_id=%s',(self.tenant,)),
        ]
        for sql,params in operations:
            with self.scoped() as conn:conn.execute(sql,params)
        self.assertEqual(self.revision(),before+len(operations))
        self.assertEqual(self.revision(self.other),other_before)

    def test_uninstrumented_group_coordination_writes_do_not_advance_epoch(self):
        """Missing writer is real; this reduced table is a synthetic model."""
        with self.scoped() as conn:
            conn.execute('''CREATE TABLE saas_ai_group_coordination
              (tenant_id uuid,group_jid text,revision bigint)''')
        try:
            before=self.revision()
            with self.scoped() as conn:
                conn.execute('INSERT INTO saas_ai_group_coordination VALUES(%s,%s,1)',(self.tenant,'12345@g.us'))
                conn.execute('UPDATE saas_ai_group_coordination SET revision=revision+1 WHERE tenant_id=%s',(self.tenant,))
            self.assertEqual(self.revision(),before)
        finally:
            with self.scoped() as conn:conn.execute('DROP TABLE saas_ai_group_coordination')

    def test_session_blocks_new_audio_after_participating_revoke_regrant(self):
        async def run():
            ledger=Ledger();provider=Provider(ledger);client=Client()
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter()
                session=Session(self.scope,authority=Authority(),ledger=ledger,lease=adapter,context=self.context(adapter),
                                provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                await session.start();self.flip_member()
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):
                    await session.receive_client({'type':'audio','pcm':bytes(100)})
                self.assertNotIn('input_audio_buffer.append',[e.get('type') for e in provider.events])
                self.assertTrue(provider.closed)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT state FROM experiment_voice_session_lease').fetchone()['state'],'quarantined')
        asyncio.run(run())

    def test_started_mock_send_can_finish_after_revoke_and_remains_unconfirmed(self):
        async def run():
            ledger=Ledger();provider=Provider(ledger);client=Client();entered=asyncio.Event();proceed=asyncio.Event()
            original=provider.send
            async def blocked(event):
                if event.get('type')=='input_audio_buffer.append':entered.set();await proceed.wait()
                await original(event)
            provider.send=blocked
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter()
                session=Session(self.scope,authority=Authority(),ledger=ledger,lease=adapter,context=self.context(adapter),
                                provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                session.shutdown_timeout=.05
                await session.start()
                sending=asyncio.create_task(session.receive_client({'type':'audio','pcm':bytes(100)}))
                try:
                    await asyncio.wait_for(entered.wait(),2);self.flip_member()
                    with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):
                        await adapter.assert_current(self.scope,session.fence)
                    self.assertFalse(await session.end('revoked'))
                    with self.scoped() as conn:
                        self.assertEqual(conn.execute('SELECT state FROM experiment_voice_session_lease').fetchone()['state'],'quarantined')
                finally:
                    proceed.set();await sending
                # Expected counterexample: committed revoke cannot retract bytes
                # from a mock send that had already crossed the send boundary.
                self.assertIn('input_audio_buffer.append',[e.get('type') for e in provider.events])
                with self.assertRaisesRegex(base.LeaseUnavailable,'reconciliation_unverified'):
                    await adapter.release(self.scope,session.fence)
        asyncio.run(run())

    def test_started_mock_context_upload_cannot_be_retracted_by_revoke(self):
        async def run():
            ledger=Ledger();provider=Provider(ledger);client=Client();entered=asyncio.Event();proceed=asyncio.Event();uploaded=[]
            async def blocked_open(snapshot,**_config):
                uploaded.append(snapshot)  # boundary already crossed before revoke
                entered.set();await proceed.wait()
            provider.open=blocked_open
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter()
                session=Session(self.scope,authority=Authority(),ledger=ledger,lease=adapter,context=self.context(adapter),
                                provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                startup=asyncio.create_task(session.start())
                try:
                    await asyncio.wait_for(entered.wait(),2);self.flip_member()
                finally:proceed.set()
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):
                    await startup
                self.assertTrue(uploaded)
                self.assertNotIn('ready',[event.get('type') for event in client.events])
                self.assertTrue(provider.closed)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT state FROM experiment_voice_session_lease').fetchone()['state'],'quarantined')
        asyncio.run(run())

    def test_cancel_suppressed_mock_send_remains_inflight_and_lease_held(self):
        async def run():
            ledger=Ledger();provider=Provider(ledger);entered=asyncio.Event();cancelled=asyncio.Event();proceed=asyncio.Event()
            original=provider.send
            async def suppress_cancellation(event):
                if event.get('type')=='input_audio_buffer.append':
                    entered.set()
                    try:await proceed.wait()
                    except asyncio.CancelledError:
                        cancelled.set();await proceed.wait()
                await original(event)
            provider.send=suppress_cancellation
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped):
                adapter=self.adapter()
                session=Session(self.scope,authority=Authority(),ledger=ledger,lease=adapter,context=self.context(adapter),
                                provider=provider,client=Client(),max_seconds=4,idle_seconds=3,poll_seconds=.5)
                session.shutdown_timeout=.05;await session.start()
                sending=asyncio.create_task(session.receive_client({'type':'audio','pcm':bytes(100)}))
                try:
                    await asyncio.wait_for(entered.wait(),2);self.flip_member();sending.cancel()
                    await asyncio.wait_for(cancelled.wait(),2)
                    with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):
                        await adapter.assert_current(self.scope,session.fence)
                    self.assertFalse(await session.end('revoked'))
                    with self.scoped() as conn:
                        self.assertEqual(conn.execute('SELECT state FROM experiment_voice_session_lease').fetchone()['state'],'quarantined')
                finally:proceed.set();await sending
                # Cancel request is not proof a dependency or provider stopped.
                self.assertIn('input_audio_buffer.append',[e.get('type') for e in provider.events])
                with self.assertRaisesRegex(base.LeaseUnavailable,'reconciliation_unverified'):
                    await adapter.release(self.scope,session.fence)
        asyncio.run(run())

    def test_epoch_before_real_authorize_can_deadlock_with_member_writer(self):
        """Expected counterexample; do not install tenant-wide triggers live."""
        started=threading.Event();writer_pid={}
        def writer():
            try:
                with self.scoped() as conn:
                    conn.execute("SET LOCAL deadlock_timeout='100ms'")
                    conn.execute("SET LOCAL statement_timeout='4s'")
                    writer_pid['value']=conn.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
                    started.set()
                    conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                return 'commit'
            except psycopg.errors.DeadlockDetected:return 'deadlock'
        with self.scoped() as holder:
            holder.execute("SET LOCAL deadlock_timeout='100ms'")
            holder.execute("SET LOCAL statement_timeout='4s'")
            own_pid=holder.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
            holder.execute('SELECT revision FROM experiment_authority_epoch WHERE tenant_id=%s FOR UPDATE',(self.tenant,))
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(writer);self.assertTrue(started.wait(3))
                deadline=time.monotonic()+3;blocked=False
                while time.monotonic()<deadline:
                    with self.scoped() as observer:
                        ids=observer.execute('SELECT pg_blocking_pids(%s) AS ids',(writer_pid['value'],)).fetchone()['ids']
                    if own_pid in ids:blocked=True;break
                    time.sleep(.01)
                self.assertTrue(blocked,'member writer did not reach epoch lock')
                try:
                    from tenant_operator_voice_context import authorize
                    authorize(holder.cursor(),self.profile,self.route)
                    holder.commit();holder_state='commit'
                except psycopg.errors.DeadlockDetected:
                    holder.rollback();holder_state='deadlock'
                self.assertCountEqual([holder_state,pending.result(5)],['commit','deadlock'])
