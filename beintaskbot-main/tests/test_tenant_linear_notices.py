"""Current recipient scope/preferences are rechecked before external delivery."""
import unittest
from unittest.mock import Mock, MagicMock
from copy import deepcopy
import test_tenant_hot_orders as loaders
from test_tenant_linear_observer import config, row, TENANT, OTHER, STAMP
from tenant_linear_observer_policy import source_time
from tenant_linear_policy import TenantLinearError


def person():
    return {'tenant_id':TENANT,'telegram_id':20,'role':'worker','active':True,'tenant_status':'active',
        'permissions':['linear'],'modules':{'linear':True},'workflow':{'policies':{'members':{
        '20':{'notifications':{'linear_done':{'telegram':True,'push':True}}}}}}}


class Notices(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.context=Mock(return_value=(person(),config(),'private-key','v1'));self.issue=Mock(return_value=row())
        self.service=loaders.load('tenant_linear_notice_outbox',{'_connect':self.connect,'_read_tenant_workflow':Mock(),
            'ensure_observer_schema':Mock(),'context':self.context,'issue':self.issue},
            {'tenant_platform','tenant_linear_observer_schema','tenant_linear_context','tenant_linear_tasks_provider'})
        self.item={'tenant_id':TENANT,'event_id':OTHER,'issue_id':OTHER,'event':'linear_done','recipient_id':20,
            'channel':'telegram','endpoint_hash':'','source_version':source_time(STAMP),'current_version':source_time(STAMP)}

    def test_scope_preferences_and_membership_all_required(self):
        self.assertTrue(self.service.eligible(person(),config(),row(),'linear_done','telegram'))
        for change in ({'active':False},{'tenant_status':'inactive'},{'permissions':[]},{'workflow':{}},
            {'notification_rules':{'telegram':False}}):
            self.assertFalse(self.service.eligible({**person(),**change},config(),row(),'linear_done','telegram'))
        self.assertFalse(self.service.eligible(person(),config(),row(description='Account: other'),'linear_done','telegram'))
        self.assertFalse(self.service.eligible(person(),config(),row(team={'id':OTHER}),'linear_done','telegram'))
        c=config();c['workflow']['sync_enabled']=False
        self.assertFalse(self.service.eligible(person(),c,row(),'linear_done','telegram'))

    def test_claim_marks_sending_before_transport(self):
        self.cur.fetchone.side_effect=[deepcopy(self.item),{'notification_rules':{}}]
        item=self.service.claim_delivery('telegram');self.assertTrue(item['send']);self.conn.commit.assert_called_once()
        self.context.assert_called_once_with(self.cur,{'tenant_id':TENANT,'telegram_id':20})
        self.assertEqual(self.cur.execute.call_args.args[1][0],'sending')
        self.assertIn("status='pending'",str(self.cur.execute.call_args_list))
        self.assertIn("SET status='unknown'",str(self.cur.execute.call_args_list))

    def test_revoked_membership_skipped_without_provider(self):
        self.cur.fetchone.return_value=deepcopy(self.item);self.context.side_effect=TenantLinearError('Denied',403)
        result=self.service.claim_delivery('telegram');self.assertFalse(result['send']);self.issue.assert_not_called()
        self.assertEqual(self.cur.execute.call_args.args[1][0],'skipped')

    def test_live_issue_moved_to_another_account_skipped(self):
        self.cur.fetchone.side_effect=[deepcopy(self.item),{'notification_rules':{}}]
        self.issue.return_value=row(description='Account: another')
        self.assertFalse(self.service.claim_delivery('telegram')['send'])

    def test_live_source_changed_skipped(self):
        self.cur.fetchone.side_effect=[deepcopy(self.item),{'notification_rules':{}}]
        self.issue.return_value=row(updatedAt='2026-10-08T11:00:00Z')
        self.assertFalse(self.service.claim_delivery('telegram')['send'])

    def test_device_must_still_belong_to_company_and_member(self):
        item={**self.item,'channel':'push','subscription':b'encrypted','owner_telegram_id':99,'device_active':True}
        self.cur.fetchone.side_effect=[item,{'notification_rules':{}}]
        self.assertFalse(self.service.claim_delivery('push')['send'])

    def test_provider_error_not_marked_sent_or_skipped(self):
        self.cur.fetchone.side_effect=[deepcopy(self.item),{'notification_rules':{}}]
        self.issue.side_effect=TenantLinearError('Unavailable',502)
        with self.assertRaises(TenantLinearError):self.service.claim_delivery('telegram')
        self.conn.commit.assert_not_called();self.assertNotIn('sending',str(self.cur.execute.call_args.args))

    def test_receipt_updates_only_current_delivery(self):
        self.service.finish_delivery(self.item,'delivered',7)
        args=self.cur.execute.call_args.args[1]
        self.assertEqual(args,('delivered',7,TENANT,OTHER,20,'telegram',''))
        self.assertIn("status='sending'",self.cur.execute.call_args.args[0])
        with self.assertRaises(ValueError):self.service.finish_delivery(self.item,'pending')


if __name__=='__main__':unittest.main()
