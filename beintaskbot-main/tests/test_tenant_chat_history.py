import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock
import test_tenant_hot_orders as loaders
from test_tenant_chat_send import SESSION, TENANT
from tenant_chat_history_cursor import encode, decode
from tenant_linear_policy import TenantLinearError

AT=datetime(2026,10,9,12,30,tzinfo=timezone.utc)


class Cursor(unittest.TestCase):
    def test_round_trip_timezone_and_quotes(self):
        value=encode(SESSION,7,{'position_at':AT,'external_id':"'; SELECT secrets"})
        self.assertEqual(decode(value,SESSION,7),(AT,"'; SELECT secrets"))

    def test_company_actor_and_deal_bound(self):
        value=encode(SESSION,7,{'position_at':AT,'external_id':'1'})
        for session,lead in [({**SESSION,'tenant_id':'other'},7),({**SESSION,'telegram_id':21},7),(SESSION,8)]:
            with self.assertRaises(ValueError):decode(value,session,lead)

    def test_malformed_and_unbounded_cursor(self):
        for value in ['?'*4,'x'*2049,base64.urlsafe_b64encode(b'[]').decode(),base64.urlsafe_b64encode(b'{}').decode()]:
            with self.assertRaises(ValueError):decode(value,SESSION,7)
        self.assertIsNone(decode('',SESSION,7))

    def test_naive_time_and_empty_id_rejected(self):
        for timestamp,external in [('2026-10-09','1'),(AT.isoformat(),''),(AT.isoformat(),'x'*513)]:
            blob={'tenant':TENANT,'actor':'20','lead':7,'at':timestamp,'id':external}
            with self.assertRaises(ValueError):decode(base64.urlsafe_b64encode(json.dumps(blob).encode()).decode(),SESSION,7)


class Store(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.policy=Mock();self.policy.can_access_deal.return_value=True
        self.module=loaders.load('tenant_chat_history_store',{'_connect':self.connect,'ensure_schema':Mock(),
            '_read_tenant_workflow':Mock(return_value={}), 'TenantPolicy':lambda _:self.policy},
            {'tenant_platform','tenant_chat_history_schema','tenant_policy'})
        self.cur.fetchone.side_effect=[{**SESSION,'active':True,'tenant_status':'active'}, {'tenant_id':TENANT}]
        self.cur.fetchall.return_value=[]

    def row(self, external):
        return {'external_id':external,'position_at':AT,'happened_at':AT,'created_at':AT,'body':'text'}

    def test_bounded_first_page_without_offset_or_count(self):
        result=self.module.page(SESSION,7)
        sql,parameters=self.cur.execute.call_args.args
        self.assertEqual(parameters,[TENANT,7,121]);self.assertNotIn('OFFSET',sql);self.assertNotIn('count(',sql)
        self.assertEqual(result,{'messages':[],'has_more':False,'next_cursor':None,'source':'cache'})

    def test_tie_breaker_oldest_cursor_and_chronological_output(self):
        self.cur.fetchall.return_value=[self.row('c'),self.row('b'),self.row('a')]
        result=self.module.page(SESSION,7,limit=2)
        self.assertEqual([r['external_id'] for r in result['messages']],['b','c'])
        self.assertEqual(decode(result['next_cursor'],SESSION,7),(AT,'b'))
        self.assertTrue(result['has_more']);self.assertNotIn('position_at',result['messages'][0])

    def test_cursor_remains_sql_parameter_not_interpolation(self):
        external="x'; DROP TABLE data; --"
        token=encode(SESSION,7,self.row(external));self.module.page(SESSION,7,token,2)
        sql,values=self.cur.execute.call_args.args
        self.assertIn('external_id)<(%s::timestamptz,%s)',sql)
        self.assertNotIn(external,sql);self.assertEqual(values,[TENANT,7,AT,external,3])

    def test_last_page_no_more(self):
        self.cur.fetchall.return_value=[self.row('b'),self.row('a')]
        result=self.module.page(SESSION,7,limit=2)
        self.assertFalse(result['has_more']);self.assertIsNone(result['next_cursor'])

    def test_inactive_membership_blocks_message_query(self):
        for profile in [None,{**SESSION,'active':False,'tenant_status':'active'},{**SESSION,'active':True,'tenant_status':'disabled'}]:
            self.cur.fetchone.side_effect=[profile]
            with self.assertRaises(TenantLinearError):self.module.page(SESSION,7)
        self.cur.fetchall.assert_not_called()

    def test_deal_policy_denied_before_message_read(self):
        self.policy.can_access_deal.return_value=False
        with self.assertRaises(TenantLinearError):self.module.page(SESSION,7)
        self.cur.fetchall.assert_not_called()

    def test_invalid_cursor_limit_or_lead_before_database(self):
        for lead,cursor,size in [(0,'',120),(2**63,'',120),(7,'bad',120),(7,'',121),(7,'',0)]:
            with self.assertRaises(ValueError):self.module.page(SESSION,lead,cursor,size)
        self.connect.assert_not_called()

    def test_database_failure_not_reported_as_empty_success(self):
        self.cur.fetchall.side_effect=RuntimeError('db')
        with self.assertRaises(RuntimeError):self.module.page(SESSION,7)
        self.conn.commit.assert_not_called()


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page=Mock(return_value={'messages':[],'has_more':False,'next_cursor':None})
        self.module=loaders.load('tenant_chat_history_api',{'page':self.page,
            'web':SimpleNamespace(json_response=lambda data,**kw:{'data':data,**kw})}, {'aiohttp','tenant_chat_history_store'})
        self.handle=self.module.handler(lambda _:SESSION,Mock())
        self.query={'lead_id':'7','expected_tenant_id':TENANT,'expected_user_id':'20'}

    async def test_switched_company_or_user_rejected_before_read(self):
        for change in [{'expected_tenant_id':'other'},{'expected_user_id':'21'}, {'expected_user_id':None}]:
            result=await self.handle(SimpleNamespace(query={**self.query,**change}))
            self.assertEqual(result['status'],409)
        self.page.assert_not_called()

    async def test_success_no_store_and_no_external_calls(self):
        result=await self.handle(SimpleNamespace(query=self.query))
        self.page.assert_called_once_with(SESSION,'7','','120');self.assertTrue(result['data']['success'])
        self.assertEqual(result['headers']['Cache-Control'],'no-store')

    async def test_validation_denial_and_database_failure_statuses(self):
        for error,status in [(ValueError('cursor'),400),(TenantLinearError('denied',403),403),(RuntimeError('db'),503)]:
            self.page.side_effect=error
            self.assertEqual((await self.handle(SimpleNamespace(query=self.query)))['status'],status)


if __name__=='__main__':unittest.main()
