"""Owner-only read path and tenant-scoped risk storage contracts."""
import unittest
from unittest.mock import MagicMock, Mock
from datetime import datetime, timezone
from test_tenant_hot_orders import load

TENANT='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'


class RiskStore(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.service=load('tenant_proactive_risk_store',{'_connect':self.connect,
            '_read_tenant_workflow':Mock(return_value={}), 'ensure_schema':Mock()},
            {'tenant_platform','tenant_proactive_risk_schema'})

    def test_non_owner_is_denied_before_database_access(self):
        with self.assertRaises(self.service.TenantLinearError):
            self.service.list_findings({'tenant_id':TENANT,'telegram_id':20,'role':'worker','active':True,'tenant_status':'active'})
        self.connect.assert_not_called()

    def test_stale_owner_session_is_rechecked_against_current_membership(self):
        current={'tenant_id':TENANT,'telegram_id':20,'role':'worker','active':True,'tenant_status':'active',
            'modules':{},'notification_rules':{}}
        self.cur.fetchone.return_value=current
        with self.assertRaises(self.service.TenantLinearError) as error:
            self.service.list_findings({'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,'tenant_status':'active'})
        self.assertEqual(error.exception.status,403)
        self.assertEqual(self.cur.execute.call_count,1)

    def test_owner_view_is_tenant_scoped_and_reports_alert_opt_in_state(self):
        checked=datetime(2026,11,7,12,tzinfo=timezone.utc)
        member={'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,'tenant_status':'active',
            'modules':{},'notification_rules':{}}
        self.service._read_tenant_workflow.return_value={'policies':{'members':{'20':{'notifications':{
            'proactive_risk':{'internal':True,'push':True}}}}}}
        row={'source_kind':'linear','source_id':'issue-1','rule_id':'access_price_claim','source_scope':'cfg1:team-a','source_version':'v1',
            'severity':'P1','confidence':'medium','source_title':'Price permission','source_url':'https://linear.app/a/issue/BS-1',
            'facts':['reported issue'],'assessment':'Not verified','recommended_step':'Check ACL','fingerprint':'f',
            'checked_at':checked,'source_checked_at':checked}
        self.cur.fetchone.side_effect=[member,{'updated_at':'cfg1','metadata':{'settings':{'team_id':'team-a'}}},
            {'account_domain':'crm-a','connected_at':'c1'},{'checked_at':checked},{'checked_at':checked}]
        self.cur.fetchall.return_value=[row]
        result=self.service.list_findings({'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,
            'tenant_status':'active'})
        self.assertEqual(result['tenant_id'],TENANT)
        self.assertEqual(result['mode'],'owner_review')
        self.assertEqual(result['alert_opt_in_channels'],['internal','push'])
        self.assertEqual(len(result['findings']),1)
        self.assertEqual(result['findings'][0]['source_url'],row['source_url'])
        self.assertEqual(result['findings'][0]['source_scope'],'cfg1:team-a')
        self.assertEqual(result['findings'][0]['source_checked_at'],checked.isoformat())
        sql=str(self.cur.execute.call_args_list)
        self.assertIn("m.tenant_id=%s::uuid",sql)
        self.assertIn("source_kind=ANY(%s)",sql)
        self.assertIn("source_scope=%s",sql)
        self.assertIn("saas_proactive_risk_scope_checks",sql)
        self.assertNotIn('saas_tenant_notifications',sql)
        self.assertNotIn('send_message',sql)

    def test_finding_is_stale_when_source_snapshot_has_no_verifiable_check_time(self):
        checked=datetime.now(timezone.utc)
        member={'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,'tenant_status':'active',
            'modules':{},'notification_rules':{}}
        row={'source_kind':'crm_task','source_id':'9','rule_id':'crm_task_outage','source_version':'old',
            'severity':'P2','confidence':'low','source_title':'CRM task','source_url':'','facts':[],
            'assessment':'Unverified report','recommended_step':'Check','fingerprint':'f','checked_at':checked,
            'source_checked_at':None}
        self.cur.fetchone.side_effect=[member,None,{'account_domain':'crm-a','connected_at':'c1'},
            {'checked_at':None},{'checked_at':checked}]
        self.cur.fetchall.return_value=[row]
        result=self.service.list_findings({'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,'tenant_status':'active'})
        self.assertEqual(result['findings'][0]['freshness'],'stale')

    def test_untrusted_external_url_is_removed_from_preview(self):
        checked=datetime(2026,11,7,12,tzinfo=timezone.utc)
        member={'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,'tenant_status':'active','modules':{},'notification_rules':{}}
        row={'source_kind':'linear','source_id':'issue-1','rule_id':'access_control_claim','source_version':'v1',
            'severity':'P2','confidence':'low','source_title':'Test','source_url':'https://linear.app.evil/a',
            'facts':[],'assessment':'Not verified','recommended_step':'Check','fingerprint':'f','checked_at':checked,
            'source_checked_at':checked}
        self.cur.fetchone.side_effect=[member,{'updated_at':'cfg1','metadata':{'settings':{'team_id':'team-a'}}},
            {'account_domain':'crm-a','connected_at':'c1'},{'checked_at':checked},{'checked_at':checked}]
        self.cur.fetchall.return_value=[row]
        result=self.service.list_findings({'tenant_id':TENANT,'telegram_id':20,'role':'owner','active':True,'tenant_status':'active'})
        self.assertEqual(result['findings'][0]['source_url'],'')


if __name__=='__main__':
    unittest.main()
