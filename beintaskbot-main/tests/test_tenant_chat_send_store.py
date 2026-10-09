from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock
import test_tenant_hot_orders as loaders
from test_tenant_chat_send import TENANT, SESSION, INPUT
from tenant_chat_send_policy import fingerprint
from tenant_linear_policy import TenantLinearError


class Store(unittest.TestCase):
    def setUp(self):
        self.connect = MagicMock(); self.conn = self.connect.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.schema = Mock(); self.read_workflow = Mock(return_value={})
        self.fernet = Mock(); self.fernet.decrypt.return_value = b'{"access_token":"tenant-key"}'
        self.policy = Mock(); self.policy.allows.return_value = True; self.policy.can_access_deal.return_value = True
        self.module = loaders.load('tenant_chat_send_store', {'_connect': self.connect,
            'ensure_schema': self.schema, '_read_tenant_workflow': self.read_workflow,
            '_fernet': lambda: self.fernet, 'TenantPolicy': lambda person: self.policy},
            {'tenant_platform', 'tenant_chat_send_schema', 'tenant_policy'})
        self.person = {**SESSION, 'active': True, 'tenant_status': 'active', 'role': 'manager'}
        self.integration = {'status': 'connected', 'secrets': b'encrypted', 'account_domain': 'client.kommo.com', 'connected_at': 'version'}
        self.authority_rows = [self.person, {'tenant_id': TENANT, 'kommo_lead_id': 7}, self.integration]

    def reserve(self, existing=None, busy=None):
        self.cur.fetchone.side_effect = [*self.authority_rows, existing, busy]
        return self.module.reserve(SESSION, INPUT)

    def test_reserve_before_post_is_tenant_and_actor_bound(self):
        item = self.reserve()
        self.assertEqual(item['key'], (TENANT, 20, INPUT['request_id']))
        self.assertEqual(item['connection'], ['client.kommo.com', 'version'])
        self.assertNotIn('secrets', item)
        statements = self.cur.execute.call_args_list
        insertion = next(call for call in statements if call.args[0].startswith('INSERT INTO saas_chat_send_receipts'))
        self.assertEqual(insertion.args[1], (TENANT, 20, INPUT['request_id'], 7, fingerprint(INPUT)))
        self.assertTrue(any('pg_advisory_xact_lock' in call.args[0] for call in statements))
        self.conn.commit.assert_called_once()

    def test_repeated_accepted_receipt_has_no_insert(self):
        item = self.reserve({'state': 'accepted', 'input_hash': fingerprint(INPUT), 'message_id': 'old'})
        self.assertEqual(item, {'cached_result': {'state': 'accepted', 'message_id': 'old'}})
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cur.execute.call_args_list))

    def test_conflicting_input_never_reuses_receipt(self):
        with self.assertRaises(TenantLinearError): self.reserve({'state': 'accepted', 'input_hash': 'different'})
        self.conn.commit.assert_not_called()

    def test_pending_unknown_and_blocked_are_not_replayed(self):
        for state in ('preparing', 'sending', 'unknown', 'blocked'):
            with self.assertRaises(TenantLinearError) as raised:
                self.reserve({'state': state, 'input_hash': fingerprint(INPUT)})
            self.assertEqual(raised.exception.keep_request, state != 'blocked')

    def test_other_request_cannot_bypass_unknown_send_for_lead(self):
        with self.assertRaises(TenantLinearError): self.reserve(busy={'request_id': 'other'})
        self.assertFalse(any(call.args[0].startswith('INSERT') for call in self.cur.execute.call_args_list))

    def test_inactive_membership_and_pipeline_denial_stop_before_reservation(self):
        self.cur.fetchone.side_effect = [{**self.person, 'active': False}]
        with self.assertRaises(TenantLinearError): self.module.reserve(SESSION, INPUT)
        self.policy.allows.return_value = False
        self.cur.fetchone.side_effect = self.authority_rows
        with self.assertRaises(TenantLinearError): self.module.reserve(SESSION, INPUT)
        self.conn.commit.assert_not_called()

    def test_connection_change_prevents_mark_sending(self):
        item = {'key': (TENANT, 20, INPUT['request_id']), 'command': INPUT, 'connection': ['other.kommo.com', 'version']}
        self.cur.fetchone.side_effect = self.authority_rows
        with self.assertRaises(TenantLinearError): self.module.mark_sending(item, {'talk_id': 3})
        self.fernet.decrypt.assert_not_called()

    def test_mark_sending_uses_current_tenant_secret_and_cas(self):
        item = {'key': (TENANT, 20, INPUT['request_id']), 'command': INPUT, 'connection': ['client.kommo.com', 'version']}
        self.cur.fetchone.side_effect = [*self.authority_rows, {'request_id': INPUT['request_id']}]
        _, credentials = self.module.mark_sending(item, {'talk_id': 3})
        self.assertEqual(credentials['access_token'], 'tenant-key')
        self.assertIn("state='preparing'", self.cur.execute.call_args.args[0])
        self.conn.commit.assert_called_once()

    def test_acceptance_and_audit_share_transaction(self):
        self.cur.fetchone.return_value = {'lead_id': 7}
        self.module.finish({'key': (TENANT, 20, INPUT['request_id'])}, 'accepted', 'message-id')
        calls = self.cur.execute.call_args_list
        self.assertIn('chat_message_accepted', calls[1].args[0])
        self.assertEqual(calls[0].args[1][-3:], (TENANT, 20, INPUT['request_id']))
        self.conn.commit.assert_called_once()

    def test_audit_failure_does_not_commit_receipt(self):
        self.cur.fetchone.return_value = {'lead_id': 7}
        self.cur.execute.side_effect = [None, RuntimeError('audit failure')]
        with self.assertRaises(RuntimeError): self.module.finish({'key': (TENANT, 20, INPUT['request_id'])}, 'accepted', 'id')
        self.conn.commit.assert_not_called()

    def test_unknown_finish_has_no_fake_history_or_audit(self):
        self.cur.fetchone.return_value = {'lead_id': 7}
        self.module.finish({'key': (TENANT, 20, INPUT['request_id'])}, 'unknown')
        self.cur.execute.assert_called_once()


if __name__ == '__main__': unittest.main()
