"""Fixed tariffs and transactional accrual contracts; no real DB or payments."""
import hashlib
import json
import unittest
import uuid
from unittest.mock import MagicMock

import test_tenant_hot_order_completion as completion
import test_tenant_hot_orders as queue
from tenant_hot_order_reward_policy import reward_amount, service_reward
from tenant_hot_order_reward_schema import migrate_hot_order_rewards
from tenant_hot_order_rewards import accrue_reward
from tenant_policy import validate_workflow_patch


class RewardPolicyTests(unittest.TestCase):
    def test_zero_default_and_decimal_precision(self):
        self.assertEqual(reward_amount(), 0)
        self.assertEqual(reward_amount('25.50'), 2550)
        self.assertEqual(reward_amount('999999999.99'), 99999999999)
        self.assertEqual(service_reward({}, 'repair'), 0)

    def test_bad_tariffs_fail_validation(self):
        for value in (True, 25, 25.5, '-1', 'NaN', 'Infinity', '1.001', '1000000000', ''):
            with self.subTest(value=value), self.assertRaises(ValueError): reward_amount(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_workflow_patch([], [], policies={'hot_orders':{'services':[{'id':'repair','name':'Təmir','reward_amount':value}]}})

    def test_tariff_is_tenant_policy_not_employee_or_global_constant(self):
        self.assertEqual(service_reward({'services':[{'id':'repair','reward_amount':'15.00'}]}, 'repair'), 1500)
        self.assertEqual(service_reward({'services':[{'id':'repair','reward_amount':'25.00'}]}, 'repair'), 2500)

    def test_public_row_uses_snapshot_not_current_tariff(self):
        fixture = queue.QueueTests(); fixture.setUp()
        fixture.profile['workflow']['policies']['hot_orders']['services'][0]['reward_amount'] = '99.00'
        row = {**fixture.order, 'reward_minor':2550}
        result = fixture.service.public_row(row, fixture.service.authorize(fixture.profile))
        self.assertEqual(result['reward_amount'], '25.50')
        self.assertNotIn('reward_minor', result)
        self.assertEqual(row['reward_minor'], 2550)

    def test_finance_history_serializes_order_uuid(self):
        module = queue.load('tenant_finance', {}, {'tenant_platform','tenant_finance_schema'})
        row = {'id':uuid.uuid4(),'tenant_id':uuid.uuid4(),'hot_order_id':uuid.uuid4(),'amount_minor':2550}
        result = module.public_entry(row)
        self.assertEqual(result['hot_order_id'],str(row['hot_order_id']))
        self.assertEqual(json.loads(json.dumps(result))['amount'],'25.50')

    def test_creation_snapshots_server_tariff_and_ignores_forged_amount(self):
        fixture = queue.QueueTests(); fixture.setUp()
        fixture.profile['workflow']['policies']['hot_orders']['services'][0]['reward_amount'] = '25.50'
        fixture.cur.fetchone.return_value = {**fixture.order, 'reward_minor':2550}
        result = fixture.service.create_order(fixture.profile, {**fixture.data,'reward_minor':90000,'reward_amount':'900'})
        self.assertEqual(fixture.cur.execute.call_args_list[0].args[1][-2:], (2550,20))
        self.assertEqual(result['reward_amount'], '25.50')

    def test_creation_retry_after_tariff_change_keeps_original_order(self):
        fixture = queue.QueueTests(); fixture.setUp()
        payload = {**{k:fixture.data[k] for k in ('service_id','client_name','description')},'phone':'','address':'','priority':'normal'}
        stamp = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        fixture.profile['workflow']['policies']['hot_orders']['services'][0]['reward_amount'] = '99.00'
        fixture.cur.fetchone.side_effect = [None,{**fixture.order,'fingerprint':stamp,'reward_minor':2550}]
        result = fixture.service.create_order(fixture.profile, fixture.data)
        self.assertEqual(result['reward_amount'],'25.50')
        self.assertEqual(fixture.cur.execute.call_count,2)


class AccrualTests(unittest.TestCase):
    def setUp(self):
        self.cur = MagicMock()
        self.profile = {'tenant_id':'company-a','telegram_id':10}
        self.row = {'tenant_id':'company-a','id':'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',
                    'status':'completed','claimed_by':20,'reward_minor':2550}

    def test_only_final_completed_order_with_positive_snapshot_accrues(self):
        for patch in ({'status':'submitted'}, {'status':'claimed'}, {'status':'cancelled'}, {'reward_minor':0}):
            self.assertIsNone(accrue_reward(self.cur,self.profile,{**self.row,**patch}))
        self.cur.execute.assert_not_called()

    def test_credit_targets_claimant_and_locks_same_account_as_manual_finance(self):
        self.cur.fetchone.side_effect = [{'active':True},None]
        entry = accrue_reward(self.cur,self.profile,self.row)
        calls = self.cur.execute.call_args_list
        self.assertIn('FOR UPDATE',calls[0].args[0])
        self.assertEqual(calls[0].args[1], ('company-a',20))
        self.assertIn('hot_order_id=%s::uuid',calls[1].args[0])
        self.assertEqual(calls[2].args[1][0:4], ('company-a',entry,20,10))
        self.assertEqual(calls[2].args[1][6],2550)
        self.assertEqual(calls[3].args[1],(entry,'company-a',self.row['id']))
        self.assertIn('finance_hot_order_reward',calls[4].args[0])

    def test_missing_member_and_duplicate_credit_fail(self):
        for answers in ([None], [{'active':True},{'id':'existing'}]):
            self.cur.reset_mock(); self.cur.fetchone.side_effect = answers
            with self.assertRaises(ValueError): accrue_reward(self.cur,self.profile,self.row)
            self.assertFalse(any('INSERT' in call.args[0] for call in self.cur.execute.call_args_list))

    def test_inactive_worker_keeps_earned_reward_without_reactivating_access(self):
        self.cur.fetchone.side_effect = [{'active':False},None]
        self.assertTrue(accrue_reward(self.cur,self.profile,self.row))
        self.assertFalse(any('UPDATE saas_tenant_members' in c.args[0] for c in self.cur.execute.call_args_list))

    def test_cross_tenant_and_invalid_recipient_fail_before_sql(self):
        for patch in ({'tenant_id':'foreign'},{'claimed_by':None},{'reward_minor':-1}):
            with self.assertRaises(ValueError): accrue_reward(self.cur,self.profile,{**self.row,**patch})
        self.cur.execute.assert_not_called()

    def test_migration_has_unique_order_source_and_composite_foreign_keys(self):
        migrate_hot_order_rewards(self.cur)
        sql = ' '.join(call.args[0] for call in self.cur.execute.call_args_list)
        for fragment in ('DEFAULT 0','ON DELETE RESTRICT','FOREIGN KEY (tenant_id,hot_order_id)',
                         'FOREIGN KEY (tenant_id,reward_entry_id)','CREATE UNIQUE INDEX','(tenant_id,hot_order_id)'):
            self.assertIn(fragment,sql)
        for fragment in ('UPDATE saas_hot_orders','UPDATE hot_orders','DROP TABLE'):
            self.assertNotIn(fragment,sql)


class RewardCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = completion.CompletionTests(); self.fixture.setUp()
        self.fixture.row['reward_minor'] = 2550

    def test_approve_settles_snapshot_in_same_commit_before_command_receipt(self):
        f = self.fixture; f.profile = queue.fixtures.person('owner'); f.row['status']='submitted'
        f.profile['workflow']['policies']['hot_orders']['services'][0]['reward_amount']='99.00'
        f.data['action']='approve'
        f.cur.fetchone.side_effect=[f.row,None,{**f.row,'status':'completed'},{'active':True},None]
        result=f.module.completion_command(f.profile,f.data)
        self.assertEqual(result['reward_amount'],'25.50');self.assertTrue(result['reward_entry_id'])
        sql=[call.args[0] for call in f.cur.execute.call_args_list]
        credit=next(i for i,text in enumerate(sql) if 'INSERT INTO saas_finance_entries' in text)
        receipt=next(i for i,text in enumerate(sql) if 'INSERT INTO saas_hot_order_commands' in text)
        self.assertLess(credit,receipt);f.conn.commit.assert_called_once()

    def test_no_review_policy_settles_on_completion(self):
        f=self.fixture;f.profile['workflow']['policies']['hot_orders']['completion_requires_admin']=False
        f.cur.fetchone.side_effect=[f.row,None,{**f.row,'status':'completed'},{'active':True},None]
        self.assertTrue(f.module.completion_command(f.profile,f.data)['reward_entry_id'])
        f.conn.commit.assert_called_once()

    def test_submission_and_rejection_never_credit(self):
        f=self.fixture
        for action,status in [('complete','submitted'),('reject','claimed')]:
            f.cur.reset_mock();f.profile=queue.fixtures.person('master' if action=='complete' else 'owner')
            row={**f.row,'status':'claimed' if action=='complete' else 'submitted'}
            f.cur.fetchone.side_effect=[row,None,{**row,'status':status}]
            f.module.completion_command(f.profile,{**f.data,'action':action,'reason':'Rework'})
            self.assertFalse(any('saas_finance_entries' in call.args[0] for call in f.cur.execute.call_args_list))

    def test_accrual_failure_does_not_commit_completion_or_receipt(self):
        f=self.fixture;f.profile['workflow']['policies']['hot_orders']['completion_requires_admin']=False
        f.cur.fetchone.side_effect=[f.row,None,{**f.row,'status':'completed'},None]
        with self.assertRaises(ValueError): f.module.completion_command(f.profile,f.data)
        f.conn.commit.assert_not_called()
        self.assertFalse(any('INSERT INTO saas_hot_order_commands' in call.args[0] for call in f.cur.execute.call_args_list))

    def test_repeat_receipt_never_reaccrues_or_recomputes_tariff(self):
        f=self.fixture
        payload={'order_id':f.row['id'],'action':'complete','text':'Done','version':f.row['updated_at']}
        stamp=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        receipt={'status':'submitted','reward_amount':'25.50'}
        f.cur.fetchone.side_effect=[{**f.row,'status':'completed'},{'fingerprint':stamp,'result':receipt}]
        self.assertEqual(f.module.completion_command(f.profile,f.data),receipt)
        self.assertEqual(f.cur.execute.call_count,3)


if __name__ == '__main__': unittest.main()
