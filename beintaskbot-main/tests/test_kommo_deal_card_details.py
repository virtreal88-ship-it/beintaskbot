import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from tenant_crm_relation_contract import ProviderBinding
from tenant_kommo_deal_details import DealDetails, snapshot_fields


class Snapshot(unittest.TestCase):
    def test_only_explicit_company_and_assignee(self):
        raw = {'responsible_user_id': 9, 'price': 0, '_embedded': {
            'contacts': [{'id': 35, 'name': 'Client', 'custom_fields_values': [
                {'field_code': 'EMAIL', 'values': [{'value': 'contact@example.test'}]}]}],
            'companies': [{'id': 36}]}}
        snapshot = snapshot_fields({'phone': 'stale phone', 'responsible_name': 'Pipeline owner', 'raw': raw})
        self.assertEqual(snapshot['responsible_id'], 9)
        self.assertEqual(snapshot['responsible_name'], '')
        self.assertTrue(snapshot['company_linked'])
        self.assertEqual(snapshot['company_name'], '')
        self.assertEqual(snapshot['contact_email'], 'contact@example.test')
        self.assertEqual(snapshot['contact_phone'], '')
        self.assertEqual(snapshot['price'], 0)
        raw['_embedded']['companies'] = [{'name': 'Unlinked'}]
        self.assertFalse(snapshot_fields({'raw': raw})['company_linked'])
        self.assertEqual(snapshot_fields({'raw': {}})['responsible_id'], 0)


class Directory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant = '11111111-1111-4111-8111-111111111111'
        self.profile = {'tenant_id': self.tenant}
        self.binding = ProviderBinding(self.tenant, 'kommo', 'demo.kommo.com', 'generation-1')
        self.deal = {'raw': {'responsible_user_id': 9}}

    async def test_one_directory_lookup_and_connection_isolation(self):
        request = AsyncMock(side_effect=[
            {'_embedded': {'users': [{'id': 9, 'name': 'Assignee A', 'email': 'private@example.test'}]}},
            {'_embedded': {'users': [{'id': 9, 'name': 'Assignee B'}]}}])
        details = DealDetails(request)
        with patch('tenant_kommo_deal_details._authorized_binding', return_value=self.binding) as binding:
            expected = {'responsible_name': 'Assignee A'}
            self.assertEqual(await asyncio.gather(*(details.read(self.profile, self.deal) for _ in range(4))), [expected] * 4)
            self.assertEqual(await details.read(self.profile, self.deal), expected)
            request.assert_awaited_once_with(self.binding, 'GET', 'users', params={'page': 1, 'limit': 250})
            binding.return_value = ProviderBinding(self.tenant, 'kommo', 'demo.kommo.com', 'generation-2')
            self.assertEqual(await details.read(self.profile, self.deal), {'responsible_name': 'Assignee B'})
            binding.return_value = None
            self.assertEqual(await details.read(self.profile, self.deal), {})
        self.assertEqual(request.await_count, 2)

    async def test_late_permission_loss_and_throttled_provider_denial(self):
        request = AsyncMock(return_value={'_embedded': {'users': [{'id': 9, 'name': 'Assignee'}]}})
        with patch('tenant_kommo_deal_details._authorized_binding', side_effect=[self.binding, None]):
            self.assertEqual(await DealDetails(request).read(self.profile, self.deal), {})
        denied = AsyncMock(side_effect=PermissionError)
        details = DealDetails(denied)
        with patch('tenant_kommo_deal_details._authorized_binding', return_value=self.binding):
            self.assertEqual(await details.read(self.profile, self.deal), {})
            self.assertEqual(await details.read(self.profile, self.deal), {})
        self.assertEqual(denied.await_count, 1)
