"""Identity guard regression tests: no DB, provider or credentials."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session


class ReadSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = {'tenant_id': 'company-a', 'telegram_id': 20}
        self.member = Mock(return_value=self.session)

    def request(self, **query):
        return SimpleNamespace(query=query)

    def test_returns_same_session_and_reads_member_once(self):
        request = self.request(expected_tenant_id='company-a', expected_user_id='20')
        self.assertIs(require_read_session(request, self.member), self.session)
        self.member.assert_called_once_with(request)

    def test_missing_session_is_forbidden_before_identity(self):
        self.member.return_value = None
        with self.assertRaises(TenantLinearError) as caught:
            require_read_session(self.request(), self.member)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(str(caught.exception), 'İcazə yoxdur.')

    def test_missing_or_changed_or_noncanonical_identity_conflicts(self):
        cases = ({}, {'expected_tenant_id': 'company-a'},
                 {'expected_tenant_id': 'company-b', 'expected_user_id': '20'},
                 {'expected_tenant_id': 'company-a', 'expected_user_id': '21'},
                 {'expected_tenant_id': 'company-a', 'expected_user_id': '020'},
                 {'expected_tenant_id': 'company-a ', 'expected_user_id': '20'})
        for query in cases:
            with self.subTest(query=query):
                with self.assertRaises(TenantLinearError) as caught:
                    require_read_session(self.request(**query), self.member)
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(str(caught.exception), 'Kabinet dəyişib. Yeniləyin.')

    def test_resource_fields_not_interpreted_by_identity_guard(self):
        request = self.request(expected_tenant_id='company-a', expected_user_id='20',
                               lead_id='not-a-lead', message_id='other-company')
        self.assertIs(require_read_session(request, self.member), self.session)

    def test_resolver_failure_not_disguised_as_authorized_session(self):
        self.member.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            require_read_session(self.request(), self.member)
