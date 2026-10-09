"""Push provider contracts without database, credentials or network."""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock

from tenant_push_payload import payload_for
from tenant_push_provider import send


class PayloadTests(unittest.TestCase):
    def test_event_routes_and_kinds_keep_existing_contract(self):
        expected = {
            'task_approval_decided': ('tasks', 'crm-task-decision', 'task_decided'),
            'linear_done': ('linear', 'crm-linear', 'linear'),
            'linear_status_changed': ('linear', 'crm-linear', 'linear'),
            'hot_order_available': ('hot_orders', 'crm-hot-order', 'hot_order'),
            'hot_order_completion_requested': ('approvals', 'crm-hot-order-review', 'hot_order_review'),
            'hot_order_completion_decided': ('hot_orders', 'crm-hot-order-review', 'hot_order_decided'),
        }
        for event, (view, tag, kind) in expected.items():
            with self.subTest(event=event):
                value = payload_for(event)
                self.assertEqual(value['url'], '/app?view=' + view)
                self.assertEqual(value['tag'], tag)
                self.assertEqual(value['kind'], kind)
                self.assertEqual(set(value), {'title', 'body', 'url', 'tag', 'kind'})
                self.assertEqual(value['title'], 'CRM Smart Assistant')

    def test_unknown_and_approval_events_keep_default(self):
        expected = {'title': 'CRM Smart Assistant',
                    'body': 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
                    'url': '/app', 'tag': 'crm-task-approval'}
        for event in [None, '', 'unknown', 'task_completion_requested', 'task_assignment_requested']:
            self.assertEqual(payload_for(event), expected)

    def test_each_call_returns_independent_copy_including_alias(self):
        first = payload_for('linear_done')
        first['body'] = 'PRIVATE'
        self.assertNotEqual(payload_for('linear_done')['body'], 'PRIVATE')
        self.assertNotEqual(payload_for('linear_status_changed')['body'], 'PRIVATE')
        first = payload_for(None)
        first['title'] = 'PRIVATE'
        self.assertEqual(payload_for(None)['title'], 'CRM Smart Assistant')

    def test_modules_have_no_db_queue_or_platform_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        for name in ['tenant_push_payload', 'tenant_push_provider']:
            tree = ast.parse((root / (name + '.py')).read_text(encoding='utf-8'))
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertFalse({'tenant_platform', 'tenant_push', 'tenant_push_outbox'} & set(imports))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        endpoint = 'https://fcm.googleapis.com/fcm/send/private-token'
        self.subscription = {'endpoint': endpoint, 'keys': {'p256dh': 'key', 'auth': 'secret'}}
        self.item = {'subscription': b'ENCRYPTED',
                     'endpoint_hash': hashlib.sha256(endpoint.encode()).hexdigest(),
                     'tenant_id': 'PRIVATE_COMPANY', 'request_id': 'PRIVATE_REQUEST',
                     'state': {'text': 'PRIVATE_TEXT'}, 'event': 'linear_done'}
        self.decrypt = Mock(return_value=json.dumps(self.subscription).encode())
        self.normalize = Mock(return_value=self.subscription)
        self.session = MagicMock()
        self.factory = Mock(return_value=self.session)
        self.webpush = Mock(return_value=SimpleNamespace(status_code=201))
        self.error = RuntimeError('Push rejected')
        self.rejection = Mock(return_value=self.error)
        self.claims = {'sub': 'sender'}

    def send(self):
        send(self.item, 'PRIVATE_KEY', self.claims, decrypt=self.decrypt,
             normalize=self.normalize, session_factory=self.factory,
             webpush=self.webpush, rejection=self.rejection)

    def test_single_call_keeps_timeout_ttl_and_neutral_payload(self):
        self.send()
        self.decrypt.assert_called_once_with(b'ENCRYPTED')
        self.normalize.assert_called_once_with(self.subscription)
        self.factory.assert_called_once_with()
        self.webpush.assert_called_once()
        args = self.webpush.call_args.kwargs
        self.assertEqual(args['subscription_info'], self.subscription)
        self.assertEqual(args['timeout'], 15)
        self.assertEqual(args['ttl'], 300)
        self.assertEqual(args['vapid_private_key'], 'PRIVATE_KEY')
        self.assertEqual(args['vapid_claims'], self.claims)
        self.assertIsNot(args['vapid_claims'], self.claims)
        self.assertIs(args['requests_session'], self.session.__enter__.return_value)
        self.assertEqual(json.loads(args['data']), payload_for('linear_done'))
        self.assertNotIn('PRIVATE', args['data'])
        self.session.__exit__.assert_called_once()

    def test_identity_mismatch_never_opens_session_or_calls_provider(self):
        self.item['endpoint_hash'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.send()
        self.factory.assert_not_called()
        self.webpush.assert_not_called()

    def test_decrypt_json_and_normalization_errors_stop_before_network(self):
        self.decrypt.side_effect = ValueError('decrypt')
        with self.assertRaises(ValueError): self.send()
        self.normalize.assert_not_called()
        self.decrypt.side_effect = None
        self.decrypt.return_value = b'not-json'
        with self.assertRaises(ValueError): self.send()
        self.decrypt.return_value = json.dumps(self.subscription).encode()
        self.normalize.side_effect = ValueError('unsafe endpoint')
        with self.assertRaises(ValueError): self.send()
        self.factory.assert_not_called()
        self.webpush.assert_not_called()

    def test_all_2xx_accepted_without_rejection(self):
        for code in [200, 201, 202, 204, 299]:
            self.webpush.return_value.status_code = code
            self.send()
        self.rejection.assert_not_called()

    def test_non_2xx_rejected_once_with_original_response(self):
        for code in [199, 301, 404, 410, 429, 500]:
            self.webpush.reset_mock()
            self.webpush.return_value.status_code = code
            with self.assertRaises(RuntimeError) as caught: self.send()
            self.assertIs(caught.exception, self.error)
            self.webpush.assert_called_once()
            self.rejection.assert_called_with('Push rejected', response=self.webpush.return_value)

    def test_network_exception_propagates_and_session_closes_no_retry(self):
        error = TimeoutError('PRIVATE_PROVIDER_BODY')
        self.webpush.side_effect = error
        with self.assertRaises(TimeoutError) as caught: self.send()
        self.assertIs(caught.exception, error)
        self.webpush.assert_called_once()
        self.session.__exit__.assert_called_once()
        self.rejection.assert_not_called()


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_leaves_claimed_checkpoint_unfinished(self):
        from test_tenant_push_delivery import load, delivery
        finish = Mock()
        worker = load('tenant_push_worker', {'claim_push': Mock(return_value=delivery()),
                                            'finish_push': finish})
        worker.send_push = Mock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await worker.deliver_push_notifications('key', {}, Mock())
        finish.assert_not_called()
        worker.send_push.assert_called_once()


if __name__ == '__main__': unittest.main()
