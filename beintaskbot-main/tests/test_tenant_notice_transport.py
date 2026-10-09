"""Transport contracts with fake providers; no DB, network or credentials."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from tenant_notice_transport import attempt, DeliveryResult
from tenant_notice_text import message_parts


class Transport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item={'send':True,'recipient_id':20,'tenant_id':'company','state':{'secret':'PRIVATE'}}
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)))
        self.text=Mock(return_value='Full task and result');self.parts=Mock(side_effect=lambda text:[text])
        self.push=Mock()

    async def run_attempt(self, channel='telegram', **changes):
        return await attempt(self.item,channel,self.bot,text=self.text,parts=self.parts,
            push_sender=self.push,private_key=changes.get('private_key','key'),claims={'sub':'sender'})

    async def test_telegram_arguments_and_last_id(self):
        result=await self.run_attempt()
        self.assertEqual(result,DeliveryResult('delivered',7))
        self.bot.send_message.assert_awaited_once_with(chat_id=20,text='Full task and result',parse_mode=None,disable_web_page_preview=True)
        self.push.assert_not_called()

    async def test_multiple_parts_keep_order_and_last_id(self):
        self.parts.return_value=['one','two'];self.parts.side_effect=None
        self.bot.send_message.side_effect=[SimpleNamespace(message_id=1),SimpleNamespace(message_id=2)]
        result=await self.run_attempt()
        self.assertEqual(result.message_id,2)
        self.assertEqual([c.kwargs['text'] for c in self.bot.send_message.await_args_list],['one','two'])

    async def test_partial_failure_does_not_resend_first_or_send_remaining(self):
        self.parts.return_value=['one','two','three'];self.parts.side_effect=None
        self.bot.send_message.side_effect=[SimpleNamespace(message_id=1),TimeoutError('PRIVATE')]
        result=await self.run_attempt()
        self.assertEqual(result,DeliveryResult('unknown',1));self.assertEqual(self.bot.send_message.await_count,2)
        self.assertNotIn('PRIVATE',repr(result))

    async def test_telegram_timeout_and_rejection_never_retried(self):
        for error in [TimeoutError(),RuntimeError('provider')]:
            self.bot.send_message.reset_mock();self.bot.send_message.side_effect=error
            self.assertEqual((await self.run_attempt()).status,'unknown')
            self.bot.send_message.assert_awaited_once()

    async def test_push_does_not_build_private_telegram_text(self):
        result=await self.run_attempt('push')
        self.assertEqual(result,DeliveryResult('delivered'))
        self.push.assert_called_once_with(self.item,'key',{'sub':'sender'})
        self.text.assert_not_called();self.parts.assert_not_called();self.bot.send_message.assert_not_awaited()

    async def test_only_expired_push_responses_are_expired(self):
        for code,status in [(404,'expired'),(410,'expired'),(429,'unknown'),(500,'unknown')]:
            self.push.reset_mock();error=RuntimeError();error.response=SimpleNamespace(status_code=code)
            self.push.side_effect=error
            self.assertEqual((await self.run_attempt('push')).status,status);self.push.assert_called_once()
        self.bot.send_message.side_effect=error
        self.assertEqual((await self.run_attempt('telegram')).status,'unknown')

    async def test_formatter_failure_returns_unknown_without_provider_call(self):
        self.text.side_effect=ValueError('PRIVATE')
        result=await self.run_attempt();self.assertEqual(result.status,'unknown')
        self.bot.send_message.assert_not_awaited();self.push.assert_not_called()

    async def test_cancellation_propagates_not_fabricated_failure(self):
        self.bot.send_message.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.run_attempt()

    async def test_missing_push_configuration_never_calls_sender(self):
        self.assertEqual((await self.run_attempt('push',private_key='')).status,'unknown')
        self.push.assert_not_called()

    async def test_push_requires_no_telegram_bot_and_missing_bot_is_unknown(self):
        options = dict(text=self.text, parts=self.parts, push_sender=self.push,
                       private_key='key', claims={})
        self.assertEqual((await attempt(self.item, 'push', None, **options)).status, 'delivered')
        self.assertEqual((await attempt(self.item, 'telegram', None, **options)).status, 'unknown')
        self.text.assert_not_called()

    async def test_unclaimed_or_invalid_channel_rejected_before_provider(self):
        with self.assertRaises(ValueError):await self.run_attempt('email')
        self.item['send']=False
        with self.assertRaises(ValueError):await self.run_attempt()
        self.bot.send_message.assert_not_awaited();self.push.assert_not_called()


class Text(unittest.TestCase):
    def test_empty_and_single_part_contract(self):
        self.assertEqual(message_parts(''),[]);self.assertEqual(message_parts('Salam'),['Salam'])

    def test_utf16_boundaries_and_lossless_unicode(self):
        for text in ['x'*3600,'x'*3601,'😀'*4000,'a'*3599+'😀'+'b'*5000]:
            parts=message_parts(text)
            self.assertTrue(all(len(part.encode('utf-16-le'))//2<4096 for part in parts))
            assembled=parts[0] if len(parts)==1 else ''.join(part.split('\n',1)[1] for part in parts)
            self.assertEqual(assembled,text)

    def test_approval_worker_keeps_compatibility_export(self):
        import test_tenant_notification_worker as legacy_tests
        worker=legacy_tests.load_worker()
        self.assertIs(worker.message_parts,message_parts)


if __name__=='__main__':unittest.main()
