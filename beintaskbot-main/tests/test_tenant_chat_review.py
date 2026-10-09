from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, AsyncMock
import test_tenant_hot_orders as loaders
from test_tenant_chat_send import SESSION, TENANT, INPUT
from tenant_linear_policy import TenantLinearError


class Review(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock(); self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.policy=Mock(privileged=True); self.policy.allows.return_value=True; self.policy.can_access_deal.return_value=True
        self.module=loaders.load('tenant_chat_review_store', {'_connect':self.connect, 'ensure_schema':Mock(),
            '_read_tenant_workflow':Mock(return_value={}), 'TenantPolicy':lambda _:self.policy},
            {'tenant_platform','tenant_chat_review_schema','tenant_policy'})
        self.version=datetime(2026,10,9,tzinfo=timezone.utc)
        self.data={'lead_id':7,'actor_id':20,'request_id':INPUT['request_id'],
            'review_id':'d271709d-1140-49a3-87f1-d554a9336a68', 'expected_updated_at':self.version.isoformat(),
            'decision':'found','reason':'Checked Kommo history'}
        self.member={**SESSION,'active':True,'tenant_status':'active'}
        self.row={'state':'unknown','updated_at':self.version,'resolution':{}}

    def invoke(self, row=None, updated=True):
        self.cur.fetchone.side_effect=[self.member,{'tenant_id':TENANT},row if row is not None else self.row,
            {'request_id':INPUT['request_id']} if updated else None]
        return self.module.resolve(SESSION,self.data)

    def test_resolution_and_audit_commit_together_without_message_id(self):
        result=self.invoke()
        self.assertEqual(result['state'],'accepted'); self.assertTrue(result['resolution']['manual'])
        calls=self.cur.execute.call_args_list
        update=next(c for c in calls if c.args[0].startswith('UPDATE'))
        self.assertEqual(update.args[1][2:6],(TENANT,20,INPUT['request_id'],7))
        self.assertIn("state='unknown' AND updated_at=%s",update.args[0])
        self.assertNotIn('message_id=',update.args[0])
        self.assertIn('chat_send_manually_reviewed',calls[-1].args[0]);self.conn.commit.assert_called_once()

    def test_not_sent_unblocks_without_resending(self):
        self.data['decision']='not_sent';self.assertEqual(self.invoke()['state'],'blocked')

    def test_active_terminal_or_changed_version_is_rejected(self):
        for state in ['preparing','sending','accepted','blocked']:
            with self.assertRaises(TenantLinearError):self.invoke({**self.row,'state':state})
        with self.assertRaises(TenantLinearError):self.invoke({**self.row,'updated_at':datetime.now(timezone.utc)})
        self.conn.commit.assert_not_called()

    def test_employee_or_scope_denied(self):
        for prop in ['privileged','allows','can_access_deal']:
            self.policy.privileged=True;self.policy.allows.return_value=True;self.policy.can_access_deal.return_value=True
            if prop=='privileged':self.policy.privileged=False
            else:getattr(self.policy,prop).return_value=False
            with self.assertRaises(TenantLinearError):self.invoke()
        self.conn.commit.assert_not_called()

    def test_idempotent_review_and_conflicting_retry(self):
        result=self.invoke();self.conn.commit.reset_mock()
        row={**self.row,'state':'accepted','resolution':result['resolution']}
        self.assertEqual(self.invoke(row),result);self.conn.commit.assert_not_called()
        self.data['reason']='different review'
        with self.assertRaises(TenantLinearError):self.invoke(row)

    def test_invalid_reason_decision_and_version(self):
        for key,value in [('reason',' '),('decision','sent'),('expected_updated_at','2026-10-09')]:
            original=self.data[key];self.data[key]=value
            with self.assertRaises((TenantLinearError,ValueError)):self.module.resolve(SESSION,self.data)
            self.data[key]=original
        self.connect.assert_not_called()

    def test_cas_conflict_and_audit_failure_no_commit(self):
        with self.assertRaises(TenantLinearError):self.invoke(updated=False)
        self.cur.execute.side_effect=[None,None,None,None,None,RuntimeError('audit')]
        with self.assertRaises(RuntimeError):self.invoke()
        self.conn.commit.assert_not_called()

    def test_bounded_list_omits_draft_hash_and_secrets(self):
        self.cur.fetchone.side_effect=[self.member,{'tenant_id':TENANT}];self.cur.fetchall.return_value=[]
        self.assertEqual(self.module.list_receipts(SESSION,7),{'receipts':[]})
        sql,params=self.cur.execute.call_args.args
        self.assertIn('LIMIT 50',sql);self.assertEqual(params,(TENANT,7));self.assertNotIn('secrets',sql)


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolve=Mock(return_value={'state':'blocked'});self.list=Mock(return_value={'receipts':[]})
        self.module=loaders.load('tenant_chat_review_api',{'resolve':self.resolve,'list_receipts':self.list,
            'web':SimpleNamespace(json_response=lambda data,**kw:{'data':data,**kw})}, {'aiohttp','tenant_chat_review_store'})
        self.handler=self.module.handler(lambda _:SESSION,'https://crm.pro.az',Mock())

    async def test_identity_and_origin_required_before_mutation(self):
        for origin,tenant,status in [('https://evil.invalid',TENANT,403),('https://crm.pro.az','other',409)]:
            request=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':origin},
                json=AsyncMock(return_value={'expected_tenant_id':tenant,'expected_user_id':20}))
            self.assertEqual((await self.handler(request))['status'],status)
        self.resolve.assert_not_called()

    async def test_read_no_store_and_write_success(self):
        result=await self.handler(SimpleNamespace(method='GET',query={'lead_id':'7'}))
        self.list.assert_called_once_with(SESSION,'7');self.assertEqual(result['headers']['Cache-Control'],'no-store')
        request=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':'https://crm.pro.az'},
            json=AsyncMock(return_value={'expected_tenant_id':TENANT,'expected_user_id':20}))
        self.assertTrue((await self.handler(request))['data']['success'])


if __name__=='__main__':unittest.main()
