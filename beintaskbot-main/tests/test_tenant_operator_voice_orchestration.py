import asyncio
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

from tenant_linear_policy import TenantLinearError
import tenant_operator_voice_orchestration as service


class FakeCursor:
    def __enter__(self):return self
    def __exit__(self,*_args):return False


class FakeConnection:
    def cursor(self):return FakeCursor()
    def __enter__(self):return self
    def __exit__(self,*_args):return False


class Orchestration(unittest.IsolatedAsyncioTestCase):
    def test_pilot_is_exactly_one_server_configured_user_and_tenant(self):
        tenant=str(uuid.uuid4());scope=SimpleNamespace(tenant=tenant,actor=7)
        with patch.dict('os.environ',{'COPILOT_VOICE_PILOT_TENANT_ID':tenant,'COPILOT_VOICE_PILOT_ACTOR_ID':'7'},clear=True):
            service.require_pilot(scope)
            for rejected in (SimpleNamespace(tenant=str(uuid.uuid4()),actor=7),SimpleNamespace(tenant=tenant,actor=8)):
                with self.assertRaises(TenantLinearError):service.require_pilot(rejected)
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(TenantLinearError):service.require_pilot(scope)
        for bad_actor in ('0','07','-7','7.0','7 '):
            with patch.dict('os.environ',{'COPILOT_VOICE_PILOT_TENANT_ID':tenant,'COPILOT_VOICE_PILOT_ACTOR_ID':bad_actor},clear=True):
                with self.assertRaises(TenantLinearError):service.require_pilot(scope)

    async def test_revoked_pilot_is_denied_before_live_authority_check(self):
        tenant=str(uuid.uuid4());scope=SimpleNamespace(tenant=tenant,actor=7,channel_id='c',conversation_id='d')
        authority=service.ScopedAuthority({}, {}, scope)
        with patch.dict('os.environ',{},clear=True),patch.object(service.voice,'check') as check:
            with self.assertRaises(TenantLinearError):await authority.check(authority.session_scope,'ongoing')
            check.assert_not_called()

    async def test_real_session_reservation_is_fail_closed(self):
        scope=('t',7,'c','d')
        authority=SimpleNamespace(session_scope=scope,recheck=Mock())
        budget=service.ScopedBudget(authority,'unused')
        with patch.object(service,'_connect',return_value=FakeConnection()):
            with self.assertRaises(TenantLinearError):await budget.reserve_session(scope,'unused')

    async def test_session_receipt_is_returned_only_after_transaction_commit(self):
        scope=('t',7,'c','d');events=[]
        class CommittingConnection(FakeConnection):
            def __exit__(self,*_args):events.append('commit');return False
        authority=SimpleNamespace(session_scope=scope,recheck=lambda _cur:events.append('authorized'))
        budget=service.ScopedBudget(authority,'session')
        def reserve(_cur,actual_scope,session_id):
            events.append('reserved');return {'scope':actual_scope,'session_id':session_id}
        with patch.object(service,'_connect',return_value=CommittingConnection()), \
             patch.object(service.grant,'reserve_session',side_effect=reserve):
            result=await budget.reserve_session(scope,'session')
        self.assertEqual(events,['authorized','reserved','commit'])
        self.assertEqual(result,{'scope':scope,'session_id':'session'})

    async def test_failed_commit_never_returns_a_session_receipt(self):
        scope=('t',7,'c','d')
        class FailedCommit(FakeConnection):
            def __exit__(self,*_args):raise RuntimeError('commit_unconfirmed')
        authority=SimpleNamespace(session_scope=scope,recheck=Mock())
        budget=service.ScopedBudget(authority,'session')
        with patch.object(service,'_connect',return_value=FailedCommit()), \
             patch.object(service.grant,'reserve_session',return_value={'state':'reserved'}):
            with self.assertRaisesRegex(RuntimeError,'commit_unconfirmed'):
                await budget.reserve_session(scope,'session')

    def test_voice_context_uses_only_live_assigned_instructions(self):
        scope=SimpleNamespace(tenant='t',actor=7,channel_id='c',conversation_id='d',lead_id=1,
                              agent_binding='binding',config_version='v1')
        authority=SimpleNamespace(scope=scope,session_scope=('t',7,'c','d'),recheck=Mock())
        agent={'agent_id':'agent','instructions':'assigned instructions','company_instructions':'company rules','style':'precise'}
        context=service.CopilotContext(authority)
        with patch.object(service,'_connect',return_value=FakeConnection()), \
             patch.object(service.copilot,'_assigned_context',return_value=agent), \
             patch.object(service.copilot,'_agent_binding',return_value='binding'), \
             patch.object(service.copilot,'_read_history') as read_history, \
             patch.object(service.copilot,'_draft_context') as draft_context:
            snapshot,fingerprint=context._capture()
        self.assertEqual(snapshot['lines'],[])
        self.assertEqual(snapshot['extra_context'],{})
        self.assertIn('assigned instructions',snapshot['existing_copilot_instructions'])
        self.assertIn('company rules',snapshot['existing_copilot_instructions'])
        self.assertIn('No CRM tools',snapshot['existing_copilot_instructions'])
        self.assertTrue(fingerprint)
        read_history.assert_not_called();draft_context.assert_not_called()
        authority.recheck.assert_called_once()

    async def test_http_open_fails_closed_on_unverified_budget_before_lease_or_provider(self):
        authority=SimpleNamespace(session_scope=('t',7,'c','d'))
        request=SimpleNamespace(query={'lead_id':'1','channel_id':'c','conversation_id':'d'})
        with patch.object(service.ScopedAuthority,'create',new=AsyncMock(return_value=authority)) as create, \
             patch.object(service,'_create_server_lease') as lease, \
             patch.object(service,'create_production_provider') as provider:
            with self.assertRaises(TenantLinearError) as error:
                await service.open_server_session(request,{'tenant_id':'t','telegram_id':7},'https://app.example')
        self.assertEqual(error.exception.status,503)
        create.assert_awaited_once();lease.assert_not_called();provider.assert_not_called()

    async def test_db_work_does_not_block_loop_and_cancel_never_opens_provider(self):
        entered=threading.Event();release=threading.Event();completed=threading.Event()
        def slow(*_args,**_kwargs):
            entered.set();release.wait(3);completed.set();return SimpleNamespace(tenant='t',actor=1,channel_id='c',conversation_id='d')
        with patch.object(service.voice,'check',slow),patch.object(service,'create_production_provider') as provider:
            task=asyncio.create_task(service.ScopedAuthority.create({'tenant_id':'t','telegram_id':1},{}))
            self.assertTrue(await asyncio.to_thread(entered.wait,1))
            ticks=0
            for _ in range(3):await asyncio.sleep(.01);ticks+=1
            self.assertEqual(ticks,3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            release.set();self.assertTrue(await asyncio.to_thread(completed.wait,1));provider.assert_not_called()

    async def test_changed_session_scope_refused_before_database(self):
        scope=SimpleNamespace(tenant='t',actor=1,channel_id='c',conversation_id='d')
        authority=service.ScopedAuthority({}, {}, scope)
        with patch.object(service.voice,'check') as check:
            with self.assertRaises(TenantLinearError):await authority.check(('t',1,'other','d'),'input')
            check.assert_not_called()


if __name__=='__main__':unittest.main()
