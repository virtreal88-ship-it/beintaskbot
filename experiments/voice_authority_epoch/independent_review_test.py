"""Independent exact-head synthetic counterexamples; no provider or live data."""
import asyncio
from unittest.mock import patch
import tenant_operator_voice_lease as base
import tenant_operator_voice_context as voice
from tenant_operator_voice_orchestration import ScopedAuthority
from tenant_operator_voice_session import Session
from test_epoch_pg import EpochLeasePostgres
from test_session import Ledger, Context, Provider, Client


class ReviewV2(EpochLeasePostgres):
    def seed(self):
        with self.scoped() as conn:
            conn.execute("INSERT INTO saas_crm_messages VALUES(%s,%s,%s::jsonb)",(self.tenant,'synthetic','{"marker":"before"}'))

    def marker(self):
        with self.scoped() as conn:
            return conn.execute("SELECT raw->>'marker' AS value FROM saas_crm_messages WHERE tenant_id=%s AND channel='synthetic'",(self.tenant,)).fetchone()['value']

    def mutate(self):
        with self.scoped() as conn:
            conn.execute("UPDATE saas_crm_messages SET raw=raw || '{\"marker\":\"after\"}'::jsonb WHERE tenant_id=%s AND channel='synthetic'",(self.tenant,))
        self.flip_member()

    def test_pre_claim_aba_and_source_edit_blocked_with_real_auth(self):
        self.seed()
        async def run():
            checked=asyncio.Event();proceed=asyncio.Event();uploaded=[]
            ledger=Ledger();provider=Provider(ledger);client=Client();adapter=self.adapter();context=self.context(adapter)
            original=context.revalidate
            async def barrier(scope):
                await original(scope);checked.set();await asyncio.wait_for(proceed.wait(),3)
            context.revalidate=barrier
            async def mock_open(snapshot,**kwargs):uploaded.append(snapshot)
            provider.open=mock_open
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped),patch.object(voice,'_connect',self.scoped):
                session=Session(self.scope,authority=ScopedAuthority(self.profile,self.route,self.expected),ledger=ledger,lease=adapter,
                                context=context,provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                task=asyncio.create_task(session.start())
                try:
                    await asyncio.wait_for(checked.wait(),2)
                    await asyncio.to_thread(self.mutate)
                finally:proceed.set()
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_snapshot_epoch_changed'):await task
                self.assertEqual(uploaded,[]);self.assertNotIn({'type':'ready'},client.events)
                with self.scoped() as conn:
                    self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiment_voice_session_lease').fetchone()['n'],0)
        asyncio.run(run())

    def test_post_claim_aba_still_reaches_mock_open_with_real_auth(self):
        self.seed()
        async def run():
            entered=asyncio.Event();proceed=asyncio.Event();uploaded=[];actual=ScopedAuthority(self.profile,self.route,self.expected)
            class BarrierAuthority:
                async def check(inner,scope,stage):
                    if stage=='dispatch':entered.set();await asyncio.wait_for(proceed.wait(),3)
                    await actual.check(scope,stage)
            ledger=Ledger();provider=Provider(ledger);client=Client();adapter=self.adapter()
            async def mock_open(snapshot,**kwargs):uploaded.append(snapshot)
            provider.open=mock_open
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped),patch.object(voice,'_connect',self.scoped):
                session=Session(self.scope,authority=BarrierAuthority(),ledger=ledger,lease=adapter,context=self.context(adapter),
                                provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                task=asyncio.create_task(session.start())
                try:
                    await asyncio.wait_for(entered.wait(),2)
                    await asyncio.to_thread(self.mutate)
                finally:proceed.set()
                with self.assertRaisesRegex(base.LeaseUnavailable,'synthetic_authority_epoch_changed'):await task
                self.assertEqual(uploaded[0]['synthetic_rows'][0]['marker'],'before')
                self.assertEqual(await asyncio.to_thread(self.marker),'after')
                self.assertNotIn({'type':'ready'},client.events)
        asyncio.run(run())

    def test_split_reader_current_epoch_can_bind_stale_projection(self):
        self.seed()
        async def run():
            ledger=Ledger();provider=Provider(ledger);client=Client();adapter=self.adapter();uploaded=[];outer=self
            class SplitContext(Context):
                async def read_authorized(inner,scope):
                    old=await asyncio.to_thread(outer.marker)
                    await asyncio.to_thread(outer.mutate)
                    adapter.bind_snapshot(scope,await asyncio.to_thread(outer.revision))
                    return {'existing_copilot_instructions':'synthetic','captured':old}
            async def mock_open(snapshot,**kwargs):uploaded.append(snapshot)
            provider.open=mock_open
            with patch.object(base,'LEASE_ENFORCEMENT_VERIFIED',True),patch.object(base,'_connect',self.scoped),patch.object(voice,'_connect',self.scoped):
                session=Session(self.scope,authority=ScopedAuthority(self.profile,self.route,self.expected),ledger=ledger,lease=adapter,
                                context=SplitContext(),provider=provider,client=client,max_seconds=4,idle_seconds=3,poll_seconds=.5)
                await session.start()
                try:
                    self.assertEqual(uploaded[0]['captured'],'before')
                    self.assertEqual(await asyncio.to_thread(self.marker),'after')
                    self.assertIn({'type':'ready'},client.events)
                finally:await session.end('test')
        asyncio.run(run())
