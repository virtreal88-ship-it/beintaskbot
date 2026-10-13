import unittest
from unittest.mock import Mock

from tenant_legacy_ai_identity import member_from_request


class LegacyAiIdentityTests(unittest.TestCase):
    def test_requires_middleware_authenticated_identity(self):
        self.assertIsNone(member_from_request({}, Mock(), Mock()))

    def test_refuses_no_active_or_ambiguous_tenant(self):
        for tenants in ([], [{'id': 'pending', 'status': 'onboarding'}],
                        [{'id': 'a', 'status': 'active'}, {'id': 'b', 'status': 'active'}]):
            with self.subTest(tenants=tenants):
                lookup = Mock()
                result = member_from_request({'authenticated_chat_id': 7},
                    lambda _user: tenants, lookup)
                self.assertIsNone(result)
                lookup.assert_not_called()

    def test_maps_only_the_single_active_tenant_membership(self):
        expected = {'tenant_id': 'tenant-a', 'telegram_id': 7,
                    'tenant_status': 'active', 'role': 'owner'}
        lookup = Mock(return_value=expected)
        tenants = [{'id': 'tenant-a', 'status': 'active'},
                   {'id': 'old', 'status': 'archived'}]
        result = member_from_request({'authenticated_chat_id': '7'},
            lambda user: tenants, lookup)
        self.assertIs(result, expected)
        lookup.assert_called_once_with('tenant-a', 7)

    def test_refuses_member_that_became_inactive(self):
        lookup = Mock(return_value={'tenant_id': 'tenant-a', 'tenant_status': 'suspended'})
        result = member_from_request({'authenticated_chat_id': 7},
            lambda _user: [{'id': 'tenant-a', 'status': 'active'}], lookup)
        self.assertIsNone(result)

    def test_database_lookup_error_fails_closed(self):
        logger = Mock()
        result = member_from_request({'authenticated_chat_id': 7},
            Mock(side_effect=RuntimeError('database unavailable')), Mock(), logger)
        self.assertIsNone(result)
        logger.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
