"""Safety checks for explicit Connect webhook deactivation (no live requests)."""
import json
import unittest
from unittest import mock

import tenant_connect_transport as transport


class DeactivateWebhookSafetyTests(unittest.TestCase):
    CURRENT = '92338d69-2a93-4574-a012-8ce149b77f1b'
    OLD = '4e51483c-fd2a-4838-8d5d-23eb4935190c'

    def test_confirmation_required_without_network(self):
        with mock.patch.object(transport, '_connect') as conn:
            with self.assertRaises(Exception):
                transport.deactivate_stale_webhooks({'tenant_id': 'tenant', 'role': 'admin'}, [self.OLD])
            conn.assert_not_called()

    def test_company_admin_cannot_deactivate_stale_webhooks(self):
        with mock.patch.object(transport, '_connect') as conn:
            with self.assertRaises(Exception):
                transport.deactivate_stale_webhooks({'tenant_id': 'tenant', 'role': 'admin'},
                                                    [self.OLD], confirmed=True)
            conn.assert_not_called()

    def test_current_id_is_never_disabled(self):
        context = mock.MagicMock()
        cur = context.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {'status': 'connected', 'secrets': b'cipher'}
        with mock.patch.object(transport, '_connect', return_value=context), \
             mock.patch.object(transport, '_fernet') as fernet, \
             mock.patch.object(transport, '_request') as request:
            fernet.return_value.decrypt.return_value = json.dumps({
                'api_key': 'private-key', 'webhook_id': self.CURRENT}).encode()
            with self.assertRaises(Exception):
                transport.deactivate_stale_webhooks({'tenant_id': 'tenant', 'role': 'admin'},
                                                    [self.CURRENT], confirmed=True, platform_admin=True)
            request.assert_not_called()

    def test_only_selected_old_hook_is_deactivated(self):
        context = mock.MagicMock()
        cur = context.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {'status': 'connected', 'secrets': b'cipher'}
        hooks = {'data': [{'id': self.CURRENT, 'active': True, 'url': 'https://example.test/hook'},
                          {'id': self.OLD, 'active': True, 'url': 'https://example.test/hook'}]}
        def api(key, method, path, payload=None):
            self.assertEqual(key, 'private-key')
            if method == 'GET' and path == '/v1/webhooks':
                return hooks
            if method == 'GET' and path.endswith('/diagnostics'):
                return {'counts': {'pending': 0, 'retrying': 0}}
            if method == 'PATCH' and path == '/v1/webhooks/' + self.OLD:
                self.assertEqual(payload, {'active': False})
                return {'active': False}
            self.fail(f'unexpected API operation {method} {path}')
        with mock.patch.object(transport, '_connect', return_value=context), \
             mock.patch.object(transport, '_fernet') as fernet, \
             mock.patch.object(transport, '_request', side_effect=api):
            fernet.return_value.decrypt.return_value = json.dumps({
                'api_key': 'private-key', 'webhook_id': self.CURRENT}).encode()
            result = transport.deactivate_stale_webhooks({'tenant_id': 'tenant', 'role': 'admin'},
                                                         [self.OLD], confirmed=True, platform_admin=True)
        self.assertEqual(result['disabledWebhookIds'], [self.OLD])
        self.assertEqual(result['preservedWebhookId'], self.CURRENT)


if __name__ == '__main__':
    unittest.main()
