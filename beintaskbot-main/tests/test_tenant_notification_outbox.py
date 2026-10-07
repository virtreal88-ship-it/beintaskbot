"""No live database and no Telegram calls: verify queue contracts in isolation."""
import copy
import unittest
from unittest.mock import MagicMock, Mock
from test_tenant_tasks import commands, load_service, person


class ApprovalEventTests(unittest.TestCase):
    def setUp(self):
        self.store = commands.TaskCommandStore.__new__(commands.TaskCommandStore)
        self.store.key = ('company-a', 20, 'request-a')
        self.store.state = {}
        self.store.conn = MagicMock()
        self.cur = self.store.conn.cursor.return_value.__enter__.return_value

    def test_creation_event_is_in_same_transaction_as_request(self):
        self.store.save({'step':'waiting_approval', 'task_input':{'text':'Task'}})
        calls = self.cur.execute.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn('UPDATE saas_task_commands', calls[0].args[0])
        self.assertIn('INSERT INTO saas_approval_notification_events', calls[1].args[0])
        self.assertIn('ON CONFLICT DO NOTHING', calls[1].args[0])
        self.assertEqual(calls[1].args[1], (*self.store.key, 'task_approval_requested'))
        self.store.conn.commit.assert_called_once()

    def test_completion_event_has_separate_type(self):
        self.store.save({'step':'waiting_approval','completion_input':{'task_id':9}})
        self.assertEqual(self.cur.execute.call_args.args[1][-1], 'task_completion_requested')

    def test_repeated_checkpoint_does_not_enqueue(self):
        self.store.state = {'step':'waiting_approval'}
        self.store.save({'step':'waiting_approval'})
        self.assertEqual(self.cur.execute.call_count, 1)
        self.store.save({'step':'done'})
        self.assertEqual(self.cur.execute.call_count, 2)

    def test_enqueue_failure_cannot_commit_request_or_in_memory_state(self):
        self.cur.execute.side_effect = [None, RuntimeError('DB error')]
        with self.assertRaises(RuntimeError): self.store.save({'step':'waiting_approval'})
        self.store.conn.commit.assert_not_called()
        self.assertEqual(self.store.state, {})


class ApprovalOutboxTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock(); self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.workflow = Mock(return_value={'policies':{'members':{'1':{'notifications':{
            'task_approval_requested':{'telegram':True}, 'task_completion_requested':{'telegram':True}}}}}})
        self.module = load_service('tenant_notification_outbox', {
            '_connect':Mock(return_value=self.conn), '_ensure_schema':Mock(), '_read_tenant_workflow':self.workflow})
        self.profile = {**person('owner', 1), 'tenant_name':'Company A', 'tenant_status':'active'}
        self.event = {'tenant_id':'company-a','actor_id':20,'request_id':'request-a',
                      'event':'task_approval_requested','state':{'step':'waiting_approval'}}

    def sqls(self):
        return [call.args[0] for call in self.cur.execute.call_args_list]

    def test_expansion_is_bounded_locked_and_tenant_scoped(self):
        self.cur.fetchall.side_effect = [[self.event], [self.profile]]
        self.assertEqual(self.module.expand_events(), 1)
        self.assertIn('LIMIT 10', self.sqls()[0]); self.assertIn('SKIP LOCKED', self.sqls()[0])
        self.assertIn("m.role IN ('owner','admin')", self.sqls()[1])
        self.assertIn('m.tenant_id=%s::uuid', self.sqls()[1])
        self.assertIn('ON CONFLICT DO NOTHING', self.sqls()[2])
        self.assertEqual(self.cur.execute.call_args_list[2].args[1], ('company-a',20,'request-a',1))
        self.assertIn('expanded=TRUE', self.sqls()[3]); self.conn.commit.assert_called_once()

    def test_disabled_or_foreign_recipient_gets_no_delivery(self):
        self.profile['tenant_id']='other-company'
        self.cur.fetchall.side_effect = [[self.event], [self.profile]]
        self.module.expand_events()
        self.assertFalse(any('INSERT INTO saas_approval_notification_deliveries' in sql for sql in self.sqls()))
        self.assertIn('expanded=TRUE', self.sqls()[-1])

    def test_resolved_request_is_discarded_without_looking_up_recipients(self):
        self.event['state']['step']='done'; self.cur.fetchall.return_value=[self.event]
        self.module.expand_events()
        self.workflow.assert_not_called()
        self.assertEqual(len(self.sqls()), 2)

    def test_current_preferences_are_rechecked_and_claim_committed(self):
        self.cur.fetchone.return_value={**copy.deepcopy(self.event), 'recipient_id':1,'channel':'telegram'}
        self.cur.fetchall.return_value=[self.profile]
        result=self.module.claim_delivery()
        self.assertTrue(result['send'])
        self.assertIn("d.status='pending'", self.sqls()[1]); self.assertIn('FOR UPDATE OF d SKIP LOCKED', self.sqls()[1])
        self.assertEqual(self.cur.execute.call_args.args[1], ('sending','company-a',20,'request-a',1,'telegram'))
        self.conn.commit.assert_called_once()

    def test_revoked_membership_skips_delivery(self):
        self.cur.fetchone.return_value={**copy.deepcopy(self.event), 'recipient_id':1,'channel':'telegram'}
        self.cur.fetchall.return_value=[]
        self.assertFalse(self.module.claim_delivery()['send'])
        self.assertEqual(self.cur.execute.call_args.args[1][0], 'skipped')

    def test_no_pending_and_crash_checkpoint_is_not_retried(self):
        self.cur.fetchone.return_value=None
        self.assertIsNone(self.module.claim_delivery())
        self.assertIn("status='unknown'", self.sqls()[0])
        self.assertIn("status='sending'", self.sqls()[0])
        self.assertIn("d.status='pending'", self.sqls()[1])

    def test_finish_only_updates_claimed_row_in_same_company(self):
        item={**self.event,'recipient_id':1,'channel':'telegram'}
        self.module.finish_delivery(item, status='delivered', message_id=123)
        self.assertIn("AND status='sending'", self.sqls()[0])
        self.assertEqual(self.cur.execute.call_args.args[1], ('delivered',123,'company-a',20,'request-a',1,'telegram'))
        with self.assertRaises(ValueError): self.module.finish_delivery(item, status='pending')

    def test_suspended_company_is_not_eligible(self):
        self.profile['workflow']=self.workflow.return_value
        self.assertTrue(self.module.eligible(self.profile,'task_approval_requested','company-a'))
        self.profile['tenant_status']='ready_for_integration'
        self.assertTrue(self.module.eligible(self.profile,'task_approval_requested','company-a'))
        self.profile['tenant_status']='suspended'
        self.assertFalse(self.module.eligible(self.profile,'task_approval_requested','company-a'))


if __name__ == '__main__':
    unittest.main()
