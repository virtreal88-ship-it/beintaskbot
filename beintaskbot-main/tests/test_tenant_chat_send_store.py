from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
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
        # Profile loading has its own scoped-access tests; retain one fixture row
        # here while exercising the receipt store's current authority checks.
        profile_reader = patch('tenant_scoped_access_store.read_access_profile',
                               side_effect=lambda cur, *args, **kwargs: cur.fetchone())
        profile_reader.start(); self.addCleanup(profile_reader.stop)
        self.policy = Mock(); self.policy.allows.return_value = True; self.policy.can_access_deal.return_value = True
        self.policy.action_scope.return_value = None
        self.module = loaders.load('tenant_chat_send_store', {'_connect': self.connect,
            'ensure_schema': self.schema, '_read_tenant_workflow': self.read_workflow,
            '_fernet': lambda: self.fernet, 'TenantPolicy': lambda person: self.policy},
            {'tenant_platform', 'tenant_chat_send_schema', 'tenant_policy'})
        self.person = {**SESSION, 'active': True, 'tenant_status': 'active', 'role': 'manager'}
        self.integration = {'status': 'connected', 'secrets': b'encrypted', 'account_domain': 'client.kommo.com', 'connected_at': 'version'}
        self.authority_rows = [self.person, {'tenant_id': TENANT, 'kommo_lead_id': 7}, None, None, self.integration]

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

    def test_existing_group_send_retains_early_ai_handoff(self):
        item={'key':(TENANT,20,INPUT['request_id']),'command':INPUT,'connection':['connect','channel','123456789012345@g.us'],'quote':None}
        route={'conversation_id':'123456789012345@g.us'}
        self.cur.fetchone.return_value={'request_id':INPUT['request_id']}
        with patch.dict(self.module.mark_sending.__globals__,{'authority':Mock(return_value=(self.person,item['connection'],{'provider':'connect','route':route})),
                                                           'quote_target':Mock(return_value=None)}), \
             patch('tenant_ai_agents_schema.ensure_schema'), patch('tenant_ai_group_coordination.stop') as stop:
            self.module.mark_sending(item,{'origin':'connect'})
            stop.assert_called_once()

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


class PipelineBoundary(unittest.TestCase):
    """Even a broad dialog send grant cannot cross Kommo pipeline boundaries."""
    def setUp(self):
        Store.setUp(self)
        from tenant_policy import TenantPolicy
        self.module.authority.__globals__['TenantPolicy'] = TenantPolicy
        self.env = patch.dict('os.environ', {'TENANT_SCOPED_ACCESS_ENABLED': 'true'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.person.update(role='operator', permissions=[], modules={}, workflow={
            'pipelines': [{'pipeline_id': 10}, {'pipeline_id': 11}],
            'policies': {'members': {'20': {'pipeline_ids': ['10'],
                'access_scopes': {'dialogs': {'view': 'none', 'update': 'all'}}}}}})
        self.authority_rows[1] = {'tenant_id': TENANT, 'kommo_lead_id': 7, 'pipeline_id': 10}

    reserve = Store.reserve

    def test_assigned_pipeline_and_existing_update_grant_permit_reservation(self):
        self.assertEqual(self.reserve()['connection'], ['client.kommo.com', 'version'])

    def test_other_pipeline_is_denied_even_with_update_all(self):
        self.authority_rows[1]['pipeline_id'] = 11
        with self.assertRaises(TenantLinearError): self.reserve()
        self.assertFalse(any('INSERT INTO saas_chat_send_receipts' in c.args[0] for c in self.cur.execute.call_args_list))

    def test_read_grant_does_not_create_update_permission(self):
        self.person['workflow']['policies']['members']['20']['access_scopes']['dialogs']['update'] = 'none'
        with self.assertRaises(TenantLinearError): self.reserve()

    def test_dispatch_rechecks_revoked_pipeline_before_callback(self):
        item = self.reserve()
        self.person['workflow']['policies']['members']['20']['pipeline_ids'] = []
        self.cur.fetchone.side_effect = self.authority_rows
        callback = Mock()
        with self.assertRaises(TenantLinearError): self.module.dispatch_guarded(item, {}, callback)
        callback.assert_not_called()

    def test_upload_live_move_is_denied_before_credentials_return(self):
        item = self.reserve()
        self.cur.fetchone.side_effect = self.authority_rows
        with self.assertRaises(TenantLinearError):
            self.module.upload_credentials(item, {'id': 7, 'pipeline_id': 11})
