"""Synthetic PostgreSQL and injected mock-provider counterexamples only.

Uses the exact Session and CopilotContext paths; does not access network
providers. The source hard-gate is bypassed only within this test process.
"""
import asyncio
from unittest.mock import patch

import tenant_operator_voice_context as voice
import tenant_operator_voice_orchestration as service
import tenant_ai_agent_run_store as knowledge
from tenant_operator_voice_session import Session
from test_session import Ledger, Lease, Provider, Client
from test_integrated_synthetic_pg import IntegratedSynthetic
from tenant_linear_policy import TenantLinearError


class ResponseRaces(IntegratedSynthetic):
    def mutate(self, action):
        with self.scoped() as conn:
            if action == 'edit':
                conn.execute("UPDATE saas_crm_messages SET body='changed' WHERE tenant_id=%s AND external_id='customer-1'", (self.tenant,))
            elif action == 'new':
                conn.execute("INSERT INTO saas_crm_messages(tenant_id,channel,raw,kommo_lead_id,external_id,direction,body,message_type) VALUES(%s,%s,'{}',41,'new-message','incoming','new','text')", (self.tenant,'connect:'+self.channel))
            elif action == 'revoke':
                conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7', (self.tenant,))
            elif action == 'aba':
                conn.execute("UPDATE saas_crm_messages SET body='transient' WHERE tenant_id=%s AND external_id='customer-1'", (self.tenant,))
        if action == 'aba':
            with self.scoped() as conn:
                conn.execute("UPDATE saas_crm_messages SET body='synthetic customer' WHERE tenant_id=%s AND external_id='customer-1'", (self.tenant,))

    def reset(self):
        with self.scoped() as conn:
            conn.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s AND external_id='new-message'", (self.tenant,))
            conn.execute("UPDATE saas_crm_messages SET body='synthetic customer' WHERE tenant_id=%s AND external_id='customer-1'", (self.tenant,))
            conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s AND telegram_id=7', (self.tenant,))

    def run_races(self, before_check):
        async def one(action):
            self.reset()
            reached=asyncio.Event(); proceed=asyncio.Event()

            class BarrierLedger(Ledger):
                async def claim(inner, item):
                    result=await super().claim(item)
                    if before_check:
                        reached.set();await asyncio.wait_for(proceed.wait(),5)
                    return result

            class BarrierProvider(Provider):
                async def bind_claim(inner, item, receipt):
                    await super().bind_claim(item,receipt)
                    if not before_check:
                        reached.set();await asyncio.wait_for(proceed.wait(),5)

            authority=await service.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
            ledger=BarrierLedger(); provider=BarrierProvider(ledger)
            session=Session(authority.session_scope,authority=authority,ledger=ledger,lease=Lease(),
                            context=service.CopilotContext(authority),provider=provider,client=Client(),poll_seconds=1)
            try:
                await session.start()
                task=asyncio.create_task(session.receive_provider({'type':'input_audio_buffer.speech_stopped'}))
                await asyncio.wait_for(reached.wait(),5)
                await asyncio.to_thread(self.mutate, action)
                proceed.set()
                should_refuse=before_check and action != 'aba'
                if should_refuse:
                    with self.assertRaises(TenantLinearError):await task
                else:
                    await task
                did_send={'type':'response.create'} in provider.events
                self.assertEqual(did_send,not should_refuse,(before_check,action,provider.events))
            finally:
                proceed.set()
                await session.end('synthetic_test')

        async def run():
            for action in ('edit','new','revoke','aba'):
                with self.subTest(before_check=before_check,action=action):await one(action)
        with patch.object(voice,'_connect',self.scoped),patch.object(service,'_connect',self.scoped), \
             patch.object(knowledge,'_connect',self.scoped),patch.object(service,'require_sealed_source_snapshot'):
            asyncio.run(run())

    def test_change_after_claim_before_final_check(self):
        self.run_races(before_check=True)

    def test_change_after_final_check_before_response_create(self):
        self.run_races(before_check=False)
