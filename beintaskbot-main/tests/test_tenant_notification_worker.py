import ast
import asyncio
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


def load_worker():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'tenant_notification_worker.py').read_text(encoding='utf-8'))
    tree.body=[node for node in tree.body if not isinstance(node,ast.ImportFrom) or node.module!='tenant_notification_outbox']
    module=ModuleType('worker')
    module.expand_events=Mock()
    module.claim_delivery=Mock()
    module.finish_delivery=Mock()
    exec(compile(tree,'<worker>','exec'), module.__dict__)
    return module


def notice():
    return {'tenant_id':'company-a','tenant_name':'Company A','actor_id':20,'creator_name':'Worker',
            'recipient_id':1,'request_id':'request-a','send':True,
            'event':'task_completion_requested',
            'state':{'completion_input':{'task_id':9,'lead_id':5,'text':'Task text','result_text':'Result text'}}}


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker=load_worker(); self.item=notice(); self.log=Mock()
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)))

    async def test_success_sends_full_payload_only_to_claimed_recipient(self):
        self.worker.claim_delivery.side_effect=[self.item,None]
        await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.bot.send_message.assert_awaited_once()
        params=self.bot.send_message.call_args.kwargs
        self.assertEqual(params['chat_id'],1); self.assertIsNone(params['parse_mode'])
        for value in ('Company A','Worker','Task text','Result text','Tapşırıq #9','Sövdələşmə #5','https://crm.pro.az/app'):
            self.assertIn(value,params['text'])
        self.worker.finish_delivery.assert_called_once_with(self.item,status='delivered',message_id=100)

    async def test_ambiguous_transport_failure_is_never_retried(self):
        self.worker.claim_delivery.side_effect=[self.item,None]
        self.bot.send_message.side_effect=TimeoutError('PRIVATE RESULT SHOULD NOT BE LOGGED')
        await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.bot.send_message.assert_awaited_once()
        self.worker.finish_delivery.assert_called_once_with(self.item,status='unknown')
        self.assertNotIn('PRIVATE',str(self.log.warning.call_args))
        self.assertNotIn('Task text',str(self.log.warning.call_args))

    async def test_resolved_or_revoked_request_is_not_sent(self):
        self.item['send']=False; self.worker.claim_delivery.side_effect=[self.item,None]
        await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.bot.send_message.assert_not_awaited(); self.worker.finish_delivery.assert_not_called()

    async def test_checkpoint_failure_after_send_does_not_resend(self):
        self.worker.claim_delivery.return_value=self.item
        self.worker.finish_delivery.side_effect=RuntimeError('DB unavailable')
        with self.assertRaises(RuntimeError): await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.worker.claim_delivery.call_count,1)

    async def test_batch_is_bounded(self):
        self.worker.claim_delivery.return_value=self.item
        await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.assertEqual(self.worker.claim_delivery.call_count,10)

    async def test_long_text_is_split_without_losing_content(self):
        task=self.item['state']['completion_input']; task['text']='😀'*3500;task['result_text']='Ə'*3500
        parts=self.worker.message_parts(self.worker.approval_message(self.item))
        self.assertGreater(len(parts),1)
        self.assertTrue(all(len(part.encode('utf-16-le'))//2<4096 for part in parts))
        assembled=''.join(part.split('\n',1)[1] for part in parts)
        self.assertEqual(assembled,self.worker.approval_message(self.item))
        self.worker.claim_delivery.side_effect=[self.item,None]
        await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.assertEqual(self.bot.send_message.await_count,len(parts))

    async def test_partial_send_failure_marks_unknown_and_stops_remaining_parts(self):
        self.item['state']['completion_input']['text']='X'*9000
        self.worker.claim_delivery.side_effect=[self.item,None]
        self.bot.send_message.side_effect=[SimpleNamespace(message_id=1),TimeoutError()]
        await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.assertEqual(self.bot.send_message.await_count,2)
        self.worker.finish_delivery.assert_called_once_with(self.item,status='unknown')

    async def test_cancellation_leaves_claimed_checkpoint_and_propagates(self):
        self.worker.claim_delivery.return_value=self.item;self.bot.send_message.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.worker.deliver_approval_notifications(self.bot,self.log)
        self.worker.finish_delivery.assert_not_called()

    def test_bot_scheduler_is_separate_from_legacy_notifications(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))
        job=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='check_tenant_approval_notifications')
        ns={'ContextTypes':SimpleNamespace(DEFAULT_TYPE=object),'deliver_approval_notifications':AsyncMock(),
            'deliver_push_notifications':AsyncMock(),'VAPID_PRIVATE_KEY':'key','VAPID_CLAIMS':{},'logger':Mock()}
        exec(compile(ast.Module(body=[job],type_ignores=[]),'<job>','exec'),ns)
        context=SimpleNamespace(bot=self.bot)
        asyncio.run(ns[job.name](context))
        ns['deliver_approval_notifications'].assert_awaited_once_with(self.bot,ns['logger'])
        ns['deliver_push_notifications'].assert_awaited_once_with('key',{},ns['logger'])


if __name__=='__main__':unittest.main()
