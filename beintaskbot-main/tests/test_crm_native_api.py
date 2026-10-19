"""Focused tests for native contact API without database or external calls."""
import asyncio
import unittest
from unittest import mock
from types import SimpleNamespace

import tenant_crm_native_api as api


class ContactApiTests(unittest.TestCase):
    def request(self, method='GET', contact_id='', *, query=None):
        return SimpleNamespace(method=method, match_info={'id': contact_id} if contact_id else {},
                               query=query or {})

    def test_unauthenticated_request_is_rejected(self):
        h = api.handler(lambda _: None, 'https://crm.example', mock.MagicMock())
        response = asyncio.run(h(self.request()))
        self.assertEqual(response.status, 403)

    def test_tenant_scoped_list(self):
        h = api.handler(lambda _: {'tenant_id': 'tenant-a', 'role': 'admin'},
                        'https://crm.example', mock.MagicMock())
        with mock.patch.object(api, 'list_contacts', return_value=[{'display_name': 'A'}]) as listing:
            response = asyncio.run(h(self.request(query={'search': 'A'})))
        self.assertEqual(response.status, 200)
        listing.assert_called_once_with('tenant-a', 'A', 50)

    def test_invalid_contact_id_does_not_reach_database(self):
        h = api.handler(lambda _: {'tenant_id': 'tenant-a', 'role': 'owner'},
                        'https://crm.example', mock.MagicMock())
        with mock.patch.object(api, 'get_contact') as getter:
            response = asyncio.run(h(self.request(contact_id='123')))
        self.assertEqual(response.status, 400)
        getter.assert_not_called()


if __name__ == '__main__':
    unittest.main()
