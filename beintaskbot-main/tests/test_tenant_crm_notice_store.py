"""Bounded scanner SQL and tenant-local, explicitly opted-in recipients."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock, patch
from test_tenant_hot_orders import load
from test_tenant_crm_notice import member,task


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.module=load('tenant_crm_notice_store',{'_connect':self.connect,'_read_tenant_workflow':Mock(),'ensure_schema':Mock()},
            {'tenant_platform','tenant_crm_notice_schema'})
    def test_round_robin_scans_one_company_with_fixed_page_sizes(self):
        baseline=datetime.fromtimestamp(100,timezone.utc)
        self.cur.fetchone.side_effect=[{'tenant_id':'company-a','positions':{},'baseline':baseline,'connection_id':'x:2026'},
            {'account_domain':'x','connected_at':'2026'}]
        self.cur.fetchall.side_effect=[[],[],[],[],[],[],[],[]]
        with patch.dict(self.module.observe.__globals__,{'profiles':Mock(return_value=[])}):
            self.assertEqual(self.module.observe(),0)
        sql=[call.args[0] for call in self.cur.execute.call_args_list]
        self.assertEqual(sum('LIMIT 50' in q for q in sql),3)
        self.assertTrue(any('LIMIT 1 FOR UPDATE OF s SKIP LOCKED' in q for q in sql))
        for call in self.cur.execute.call_args_list:
            if 'LIMIT 50' in call.args[0]:self.assertEqual(call.args[1][0],'company-a')
        self.conn.commit.assert_called_once()
    def test_plan_is_unique_and_preferences_do_not_catch_up_later(self):
        self.cur.fetchone.return_value={'id':'event'}
        opted=member();off=member(identity=21);off['workflow']['policies']={}
        row=task()
        self.module.plan(self.cur,'company-a','task','9',row,'task_assigned','version',[opted,off],'connection')
        calls=self.cur.execute.call_args_list
        self.assertIn('ON CONFLICT(tenant_id,event,source_key,version)',calls[0].args[0])
        deliveries=[c for c in calls if 'INSERT INTO saas_crm_notice_deliveries' in c.args[0]]
        self.assertEqual(len(deliveries),2)
        self.assertEqual(deliveries[0].args[1][-1],20)
        self.assertIn('p.owner_telegram_id=b.telegram_id',deliveries[1].args[0])
        self.cur.reset_mock();self.cur.fetchone.return_value=None
        self.module.plan(self.cur,'company-a','task','9',row,'task_assigned','version',[opted],'connection')
        self.assertEqual(self.cur.execute.call_count,1)
    def test_pending_deal_journal_uses_separate_table_and_tenant_key(self):
        module=load('tenant_deal_completion_store',{'_connect':self.connect,'_ensure_schema':Mock()}, {'tenant_platform'})
        self.cur.fetchall.return_value=[]
        result=module.pending(member('owner'),50,0)
        self.assertEqual(result['next_offset'],0)
        sql,params=self.cur.execute.call_args.args
        self.assertIn('saas_deal_completion_commands',sql);self.assertIn('d.tenant_id=c.tenant_id',sql)
        self.assertEqual(params,('company-a',51,0))
