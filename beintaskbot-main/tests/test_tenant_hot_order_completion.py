"""Completion state machine; no database, finance or external sends."""
import hashlib
import json
import unittest
from unittest.mock import Mock,MagicMock
import test_tenant_hot_orders as loaders
import test_tenant_hot_order_policy as fixtures


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        base=loaders.load('tenant_hot_orders',{'_connect':self.connect,'ensure_hot_order_schema':Mock()},
                          {'tenant_platform','tenant_hot_order_schema'})
        self.module=loaders.load('tenant_hot_order_completion',{'_connect':self.connect,'ensure_hot_order_schema':Mock(),
            **{key:getattr(base,key) for key in ('authorize','identity','public_row','TenantHotOrderError')}},
            {'tenant_platform','tenant_hot_order_schema','tenant_hot_orders'})
        self.profile=fixtures.person()
        self.row=fixtures.order(id='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',status='claimed',claimed_by=20,
             updated_at='2026-10-08T12:00:00+00:00',result_text='')
        self.data={'action':'complete','order_id':self.row['id'],'request_id':'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb',
                   'expected_updated_at':self.row['updated_at'],'result_text':'Done'}

    def command(self,status='submitted'):
        self.cur.fetchone.side_effect=[self.row,None,{**self.row,'status':status,'result_text':'Done'}]
        return self.module.completion_command(self.profile,self.data)

    def test_default_submits_and_commits_result_audit_and_notice_atomically(self):
        result=self.command();self.assertEqual(result['status'],'submitted')
        sql=' '.join(call.args[0] for call in self.cur.execute.call_args_list)
        for fragment in ('FOR UPDATE','pg_advisory_xact_lock','saas_hot_order_commands','saas_tenant_audit_events','saas_hot_order_events'):
            self.assertIn(fragment,sql)
        self.assertNotIn('balance',sql);self.conn.commit.assert_called_once()
        self.assertEqual(self.cur.execute.call_args.args[1][-2:],('company-a',self.row['id']))

    def test_company_policy_can_complete_without_review(self):
        self.profile['workflow']['policies']['hot_orders']['completion_requires_admin']=False
        self.command('completed');self.assertEqual(self.cur.execute.call_args_list[3].args[1][0],'completed')

    def test_member_override_wins(self):
        self.profile['workflow']['policies']['hot_orders']['completion_requires_admin']=False
        self.profile['workflow']['policies']['members']['20']['hot_order_completion_requires_admin']=True
        self.command();self.assertEqual(self.cur.execute.call_args_list[3].args[1][0],'submitted')

    def test_empty_result_and_rejection_reason_fail_before_database(self):
        self.profile=fixtures.person('owner')
        for action in ('complete','reject'):
            with self.assertRaises(ValueError):self.module.completion_command(self.profile,{**self.data,'action':action,'result_text':' ','reason':' '})
        self.connect.assert_not_called()

    def test_stale_version_and_other_claimant_cannot_mutate(self):
        for change in ({'updated_at':'2026-10-08T13:00:00+00:00'},{'claimed_by':21}):
            self.cur.fetchone.side_effect=[{**self.row,**change},None]
            with self.assertRaises(ValueError):self.module.completion_command(self.profile,self.data)
        self.conn.commit.assert_not_called()

    def test_foreign_company_and_employee_review_denied(self):
        self.cur.fetchone.side_effect=[{**self.row,'tenant_id':'foreign'}]
        with self.assertRaises(ValueError):self.module.completion_command(self.profile,self.data)
        with self.assertRaises(ValueError):self.module.completion_command(self.profile,{**self.data,'action':'approve'})
        self.conn.commit.assert_not_called()

    def test_approve_and_return_preserve_claimant_and_result(self):
        self.profile=fixtures.person('owner');self.row['status']='submitted';self.row['result_text']='Result'
        for action,status in [('approve','completed'),('reject','claimed')]:
            self.data.update(action=action,reason='Rework')
            self.cur.fetchone.side_effect=[self.row,None,{**self.row,'status':status}]
            self.module.completion_command(self.profile,self.data)
            update=self.cur.execute.call_args_list[-4]
            self.assertEqual(update.args[1][:3],(status,'Result','Rework' if action=='reject' else ''))
            self.assertNotIn('claimed_by=',update.args[0])

    def test_identical_retry_returns_receipt_without_update(self):
        payload={'order_id':self.row['id'],'action':'complete','text':'Done','version':self.row['updated_at']}
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        self.cur.fetchone.side_effect=[{**self.row,'status':'completed'},{'fingerprint':fingerprint,'result':{'status':'submitted'}}]
        result=self.module.completion_command(self.profile,self.data)
        self.assertEqual(result,{'status':'submitted'});self.assertEqual(self.cur.execute.call_count,3)

    def test_reusing_receipt_for_changed_text_is_conflict(self):
        self.cur.fetchone.side_effect=[self.row,{'fingerprint':'different','result':{}}]
        with self.assertRaises(self.module.TenantHotOrderError) as error:self.module.completion_command(self.profile,self.data)
        self.assertEqual(error.exception.status,409);self.conn.commit.assert_not_called()

    def test_audit_failure_does_not_commit(self):
        self.cur.fetchone.side_effect=[self.row,None,{**self.row,'status':'submitted'}]
        self.cur.execute.side_effect=[None,None,None,None,None,RuntimeError('audit unavailable')]
        with self.assertRaises(RuntimeError):self.module.completion_command(self.profile,self.data)
        self.conn.commit.assert_not_called()

    def test_approval_listing_is_admin_only_and_scoped_before_pagination(self):
        base=loaders.load('tenant_hot_orders',{'_connect':self.connect,'ensure_hot_order_schema':Mock()},
                          {'tenant_platform','tenant_hot_order_schema'})
        with self.assertRaises(ValueError):base.list_orders(self.profile,approvals_only=True)
        self.cur.fetchone.return_value={'total':0};self.cur.fetchall.return_value=[]
        base.list_orders(fixtures.person('owner'),approvals_only=True)
        for call in self.cur.execute.call_args_list:self.assertIn("AND status='submitted'",call.args[0])
