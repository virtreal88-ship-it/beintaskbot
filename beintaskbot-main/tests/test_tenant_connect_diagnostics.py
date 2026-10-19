import json
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

import tenant_connect_api
import tenant_connect_transport as transport


class ConnectDiagnostics(unittest.TestCase):
    def test_settings_expose_diagnostics_and_explicitly_avoid_fake_http_200(self):
        html = (Path(__file__).resolve().parents[1] / 'docs' / 'tenant-app.html').read_text()
        self.assertIn('Diaqnostika Connect', html)
        self.assertIn('/api/platform/integrations/connect/diagnostics', html)
        self.assertIn('Dəqiq HTTP 200', html)
        self.assertIn('Əlçatan deyil', html)

    def test_cleanup_button_requires_explicit_confirmation_and_only_candidates(self):
        html = (Path(__file__).resolve().parents[1] / 'docs' / 'tenant-app.html').read_text()
        self.assertIn('id="deactivateStaleConnectWebhooks"', html)
        self.assertIn("typed!=='DEACTIVATE'", html)
        self.assertIn("h.needsReview&&h.active&&!h.isCurrent", html)
        self.assertIn("h.counts?.pending===0&&h.counts?.retrying===0", html)
        self.assertIn("h.url===current.url", html)
        self.assertIn("webhook_ids:ids,confirm:true", html)
        self.assertNotIn("'/v1/webhooks/' + hook.id", html)

    def test_diagnostics_are_tenant_scoped_read_only_and_redacted(self):
        conn_context = mock.MagicMock()
        cursor = conn_context.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {'status': 'connected', 'secrets': b'encrypted'}
        cursor.fetchall.return_value = [{'name': 'Support'}]
        responses = {
            '/v1/webhooks': {'data': [{'id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'active': True,
                                       'url': 'https://user:pass@example.test/hook?token=hidden'},
                                      {'id': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'active': True,
                                       'url': 'https://example.test/hook'}]},
            '/v1/webhooks/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/diagnostics': {
                'counts': {'pending': 0, 'retrying': 0, 'delivered': 0, 'failed': 5},
                'knownHttp401Failures': 5, 'http200Count': None,
            },
            '/v1/webhooks/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/deliveries': {'data': []},
            '/v1/webhooks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/diagnostics': {
                'counts': {'pending': 2, 'retrying': 1, 'delivered': 9, 'failed': 3},
                'knownHttp401Failures': 3, 'http200Count': None,
            },
            '/v1/webhooks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/deliveries': {'data': [
                {'status': 'failed', 'attempts': 1, 'createdAt': '2026-10-19T12:00:00Z',
                 'lastError': 'subscriber returned 401 (invalid_signature)'},
                {'status': 'failed', 'lastError': 'https://secret.example/private?token=hidden'},
            ]},
        }
        def request(key, method, path, payload=None):
            self.assertEqual(key, 'server-only-key')
            self.assertEqual(method, 'GET')
            self.assertIsNone(payload)
            return responses[path]

        with mock.patch.object(transport, '_owner') as owner, \
             mock.patch.object(transport, '_connect', return_value=conn_context), \
             mock.patch.object(transport, '_fernet') as fernet, \
             mock.patch.object(transport, '_request', side_effect=request):
            fernet.return_value.decrypt.return_value = json.dumps({'api_key': 'server-only-key',
                'webhook_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}).encode()
            result = transport.diagnostics({'tenant_id': 'tenant-a', 'role': 'admin'})

        owner.assert_called_once_with({'tenant_id': 'tenant-a', 'role': 'admin'})
        self.assertEqual([call.args[1] for call in cursor.execute.call_args_list],
                         [('tenant-a',), ('tenant-a',)])
        self.assertTrue(result['hasVerifiedCurrentWebhook'])
        self.assertTrue(result['subscriptions'][0]['isCurrent'])
        self.assertFalse(result['subscriptions'][0]['needsReview'])
        self.assertFalse(result['subscriptions'][1]['isCurrent'])
        self.assertTrue(result['subscriptions'][1]['needsReview'])
        self.assertEqual(result['subscriptions'][0]['url'], 'https://example.test/hook')
        self.assertEqual(result['subscriptions'][0]['channels'], ['Support'])
        self.assertEqual(result['subscriptions'][0]['knownHttp401Failures'], 3)
        self.assertIsNone(result['subscriptions'][0]['http200Count'])
        self.assertEqual(result['subscriptions'][0]['recentDeliveries'][0]['error'], 'HTTP 401: invalid_signature')
        self.assertEqual(result['subscriptions'][0]['recentDeliveries'][1]['error'], 'Çatdırılma uğursuz oldu')
        self.assertNotIn('server-only-key', json.dumps(result))
        self.assertNotIn('hidden', json.dumps(result))

    def test_non_admin_is_rejected_before_connect_access(self):
        with mock.patch.object(transport, '_connect') as connect:
            with self.assertRaises(Exception):
                transport.diagnostics({'tenant_id': 'tenant-a', 'role': 'worker'})
        connect.assert_not_called()

    def test_diagnostics_route_requires_session_and_sets_no_store(self):
        logger = mock.MagicMock()
        request = SimpleNamespace()
        handler = tenant_connect_api.diagnostics_handler(lambda _request: None, logger)
        with mock.patch.object(tenant_connect_api, 'diagnostics') as diagnostics:
            response = __import__('asyncio').run(handler(request))
        self.assertEqual(response.status, 403)
        diagnostics.assert_not_called()

        handler = tenant_connect_api.diagnostics_handler(lambda _request: {'tenant_id': 'tenant-a', 'role': 'admin'}, logger)
        safe_result = {'connected': True, 'subscriptions': [{'id': 'hook-id', 'http200Count': None}]}
        with mock.patch.object(tenant_connect_api, 'diagnostics', return_value=safe_result):
            response = __import__('asyncio').run(handler(request))
        self.assertEqual(json.loads(response.text), {'success': True, **safe_result})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_existing_connect_status_read_does_not_patch_subscription(self):
        conn_context = mock.MagicMock()
        cursor = conn_context.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {'status': 'connected', 'secrets': b'encrypted', 'updated_at': None}
        cursor.fetchall.return_value = []
        with mock.patch.object(transport, '_connect', return_value=conn_context), \
             mock.patch.object(transport, '_schema'), \
             mock.patch.object(transport, '_fernet') as fernet, \
             mock.patch.object(transport, '_request') as request:
            fernet.return_value.decrypt.return_value = json.dumps({'api_key': 'server-only-key', 'webhook_id': 'hook'}).encode()
            result = transport.status({'tenant_id': 'tenant-a', 'role': 'admin'})
        self.assertTrue(result['connected'])
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
