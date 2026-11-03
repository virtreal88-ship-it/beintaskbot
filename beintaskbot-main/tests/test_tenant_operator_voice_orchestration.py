import asyncio
import threading
import unittest
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
