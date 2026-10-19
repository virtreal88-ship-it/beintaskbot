"""Tenant-scoped WhatsApp channel actions; all provider calls are mocked."""
from contextlib import nullcontext
from unittest import TestCase, mock

import tenant_connect_transport as transport
from tenant_platform import TenantPlatformError


TENANT = '00000000-0000-0000-0000-000000000101'
CHANNEL = '00000000-0000-0000-0000-000000000102'
OTHER_CHANNEL = '00000000-0000-0000-0000-000000000103'
PROFILE = {'tenant_id': TENANT, 'role': 'owner'}


class ChannelManagementTests(TestCase):
    def test_disconnect_rejects_foreign_channel_without_provider_call(self):
        conn = mock.MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = None
        with mock.patch.object(transport, '_tenant_connect_lock', return_value=nullcontext()), \
             mock.patch.object(transport, '_connect', return_value=conn), \
             mock.patch.object(transport, '_request') as request:
            with self.assertRaises(TenantPlatformError):
                transport.disconnect_channel(PROFILE, OTHER_CHANNEL)
        request.assert_not_called()

    def test_disconnect_is_per_channel_and_preserves_integration(self):
        lookup, update = mock.MagicMock(), mock.MagicMock()
        lookup.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = {'active': True}
        credentials = {'api_key': 'tenant-key'}
        with mock.patch.object(transport, '_tenant_connect_lock', return_value=nullcontext()), \
             mock.patch.object(transport, '_connect', side_effect=[lookup, update]), \
             mock.patch.object(transport, '_stored_connect_credentials', return_value=credentials), \
             mock.patch.object(transport, '_request', return_value={'status': 'disconnected'}) as request, \
             mock.patch.object(transport, 'status', return_value={'connected': True}):
            self.assertEqual(transport.disconnect_channel(PROFILE, CHANNEL), {'connected': True})
        request.assert_called_once_with('tenant-key', 'POST', f'/v1/channels/{CHANNEL}/disconnect')
        sql, params = update.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertIn('UPDATE saas_connect_channels', sql)
        self.assertIn('AND connect_channel_id=%s::uuid', sql)
        self.assertEqual(params, (TENANT, CHANNEL))
        self.assertFalse(any('saas_tenant_integrations' in call.args[0] for call in
                             update.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.call_args_list))

    def test_company_wide_disconnect_is_rejected(self):
        with mock.patch.object(transport, '_owner'):
            with self.assertRaises(TenantPlatformError):
                transport.disconnect(PROFILE)

