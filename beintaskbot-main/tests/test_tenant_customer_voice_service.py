"""Voice receipt contract and fault behaviour; no real provider traffic."""
import logging
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tenant_linear_policy import TenantLinearError
from tenant_customer_voice_service import _checked_receipt, run
from tenant_connect_transport import ConnectPreflightError, _request
from tenant_operator_dialogs import _message

TENANT = 'a9c11a16-01d5-469a-a6c1-9a726975ae42'
CHANNEL = '9644b2f2-786c-43f4-b58e-2b9b5f69d554'
REQUEST = '79d6467b-3ca6-4557-9e12-b9da059c0611'
MESSAGE = '6b9f1ac1-d693-4f9f-a400-d9b8372306f8'
OPERATION = '7d92af34-d4db-4983-a1a7-56e8249c6e6e'
JID = '994500000000@s.whatsapp.net'
SESSION = {'tenant_id': TENANT, 'telegram_id': 20}
COMMAND = {'request_id': REQUEST, 'lead_id': 7, 'text': '', 'file': {'mime': 'audio/ogg'}}
ROUTE = {'channel_id': CHANNEL, 'conversation_id': JID, 'api_key': 'synthetic','author_id':20,'crm_conversation_id':7}
RECEIPT = {'id': MESSAGE, 'operationId': OPERATION, 'channelId': CHANNEL, 'conversationId': JID, 'clientMessageId': REQUEST,
           'authorId':'20','crmConversationId':'7',
           'retrySafe': False, 'status': 'sent', 'providerAccepted': True, 'delivered': False,
           'externalMessageId': 'PROVIDER123'}


class ReceiptContract(unittest.TestCase):
    def test_history_marks_only_verified_customer_voice_for_status_refresh(self):
        row={'direction':'outgoing','channel':'connect:'+CHANNEL,'message_type':'audio','body':'',
             'raw':{'voice_receipt_request_id':REQUEST,'verified_employee_id':20,'status':'sent','externalMessageId':'PROVIDER123'}}
        result=_message(row)
        self.assertTrue(result['customer_voice_status']); self.assertEqual(result['delivery_state'],'sent')
        self.assertNotIn('raw',result); self.assertNotIn('voice_receipt_request_id',result)
        for raw in ({},{'voice_receipt_request_id':'invalid','verified_employee_id':20},
                    {'voice_receipt_request_id':REQUEST}):
            self.assertNotIn('customer_voice_status',_message({**row,'raw':raw}))
    def test_acceptance_requires_exact_scope_and_provider_evidence(self):
        result = _checked_receipt(RECEIPT, ROUTE, REQUEST)
        self.assertTrue(result['provider_accepted']); self.assertFalse(result['delivered'])
        self.assertEqual(result['provider_message_id'], 'PROVIDER123')
        self.assertEqual(result['operation_id'],OPERATION)
        for change in ({'channelId': 'other'}, {'conversationId': 'other'}, {'clientMessageId': MESSAGE},
                       {'retrySafe': True}, {'status': 'invented'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                _checked_receipt({**RECEIPT, **change}, ROUTE, REQUEST)

    def test_missing_evidence_stays_unknown_and_unproved_delivery_is_downgraded(self):
        self.assertEqual(_checked_receipt({**RECEIPT, 'providerAccepted': False}, ROUTE, REQUEST)['state'], 'unknown')
        self.assertEqual(_checked_receipt({**RECEIPT, 'status': 'read', 'delivered': False}, ROUTE, REQUEST)['delivery_state'], 'sent')


class VoiceService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item = {'key': (TENANT, 20, REQUEST), 'command': COMMAND, 'connection': ['connect', CHANNEL, JID]}
        self.receipt = _checked_receipt(RECEIPT, ROUTE, REQUEST)
        patch('tenant_customer_voice_service.dispatch_guarded',side_effect=lambda item,credentials,callback,*args,**kwargs:callback(credentials,*args,**kwargs)).start()
        self.reserve = patch('tenant_customer_voice_service.reserve', return_value=self.item).start()
        self.mark = patch('tenant_customer_voice_service.mark_sending', return_value=(SESSION, ROUTE)).start()
        self.send = patch('tenant_customer_voice_service._submit', return_value=self.receipt).start()
        self.record = patch('tenant_customer_voice_service.record_acceptance').start()
        self.finish = patch('tenant_customer_voice_service.finish').start()
        self.addCleanup(patch.stopall)

    async def test_confirmed_send_persists_scoped_receipt_once(self):
        result = await run(SESSION, COMMAND, b'synthetic', logging.getLogger('synthetic'))
        self.assertTrue(result['provider_accepted'])
        self.send.assert_called_once(); self.record.assert_called_once(); self.finish.assert_not_called()

    async def test_timeout_marks_unknown_and_keeps_request(self):
        self.send.side_effect = TimeoutError()
        with self.assertRaises(TenantLinearError) as raised:
            await run(SESSION, COMMAND, b'synthetic', logging.getLogger('synthetic'))
        self.assertTrue(raised.exception.keep_request)
        self.send.assert_called_once(); self.finish.assert_called_once_with(self.item, 'unknown')

    async def test_unknown_receipt_never_counts_as_acceptance(self):
        self.send.return_value = {'state':'unknown','provider_accepted':False}
        result = await run(SESSION, COMMAND, b'synthetic', logging.getLogger('synthetic'))
        self.assertEqual(result['state'], 'unknown'); self.record.assert_not_called()
        self.finish.assert_called_once_with(self.item, 'unknown')

    async def test_known_connect_preflight_rejection_returns_draft_without_unknown_outcome(self):
        self.send.side_effect = ConnectPreflightError('voice_manual_mode_unavailable')
        with self.assertRaises(TenantLinearError) as raised:
            await run(SESSION, COMMAND, b'synthetic', logging.getLogger('synthetic'))
        self.assertEqual(raised.exception.status,409)
        self.assertFalse(raised.exception.keep_request)
        self.assertEqual(raised.exception.error_code,'voice_manual_mode_unavailable')
        self.finish.assert_called_once_with(self.item,'blocked')
        self.record.assert_not_called()

    def test_request_classifies_only_explicit_no_send_codes(self):
        response=Mock(status_code=409,json=Mock(return_value={'error':'voice_manual_mode_unavailable'}))
        with patch('tenant_connect_transport.requests.request',return_value=response):
            with self.assertRaises(ConnectPreflightError) as raised:
                _request('synthetic','POST','/v1/channels/test/customer-voice',{},
                         safe_preflight_codes=('voice_manual_mode_unavailable',))
        self.assertEqual(raised.exception.code,'voice_manual_mode_unavailable')
        response=Mock(status_code=409,json=Mock(return_value={'error':'unknown'}))
        with patch('tenant_connect_transport.requests.request',return_value=response):
            with self.assertRaises(Exception) as raised:
                _request('synthetic','POST','/v1/channels/test/customer-voice',{},
                         safe_preflight_codes=('voice_manual_mode_unavailable',))
        self.assertNotIsInstance(raised.exception,ConnectPreflightError)

    async def test_other_provider_is_blocked_before_submission(self):
        self.item['connection'] = ['kommo', 'version']
        with self.assertRaises(TenantLinearError):
            await run(SESSION, COMMAND, b'synthetic', logging.getLogger('synthetic'))
        self.send.assert_not_called(); self.mark.assert_not_called()
        self.finish.assert_called_once_with(self.item, 'blocked')

    async def test_group_voice_fails_closed_before_ai_handoff_or_submission(self):
        self.item['connection']=['connect',CHANNEL,'123456789012345@g.us']
        with self.assertRaises(TenantLinearError):
            await run(SESSION,COMMAND,b'synthetic',logging.getLogger('synthetic'))
        self.mark.assert_not_called();self.send.assert_not_called();self.record.assert_not_called()
        self.finish.assert_called_once_with(self.item,'blocked')

    async def test_uppercase_or_unverified_routes_fail_closed(self):
        for route in ('123456789012345@G.US','not-a-jid','123456789012345@broadcast','994500000000@c.us'):
            with self.subTest(route=route):
                self.item['connection']=['connect',CHANNEL,route]
                with self.assertRaises(TenantLinearError):
                    await run(SESSION,COMMAND,b'synthetic',logging.getLogger('synthetic'))
        self.mark.assert_not_called();self.send.assert_not_called();self.record.assert_not_called()

    async def test_accepted_replay_checks_receipt_without_sending(self):
        self.reserve.return_value = {'cached_result': {'state':'accepted'}}
        with patch('tenant_customer_voice_service.check', new=AsyncMock(return_value=self.receipt)) as check:
            self.assertTrue((await run(SESSION, COMMAND, b'synthetic', logging.getLogger('synthetic')))['provider_accepted'])
        check.assert_awaited_once_with(SESSION, 7, REQUEST); self.send.assert_not_called()
