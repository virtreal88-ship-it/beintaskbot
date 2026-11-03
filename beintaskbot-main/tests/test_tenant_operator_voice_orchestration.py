import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tenant_linear_policy import TenantLinearError
import tenant_operator_voice_orchestration as service


class Orchestration(unittest.IsolatedAsyncioTestCase):
    async def test_http_cutover_gate_refuses_before_any_provider_or_database(self):
        with patch.object(service.voice,'check') as check,patch.object(service,'create_production_provider') as provider:
            with self.assertRaises(TenantLinearError) as error:await service.open_server_session(None,{'tenant_id':'x','telegram_id':1})
            self.assertEqual(error.exception.status,503);check.assert_not_called();provider.assert_not_called()

    async def test_writer_gate_cannot_be_bypassed_by_session_values(self):
        history=service.VoiceHistory(None)
        with self.assertRaises(TenantLinearError):history.require_writer_ready()
        with self.assertRaises(TenantLinearError):await history.append({'role':'employee','text':'synthetic'})

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
