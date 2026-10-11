"""No production DB: role isolation, bounded queries and safe diagnostic output."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, AsyncMock
from test_tenant_hot_orders import load
from tenant_linear_policy import TenantLinearError

TENANT=str(uuid.uuid4())


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.mod=load('tenant_diagnostics',{'_connect':self.connect},{'tenant_platform'})
        self.session={'tenant_id':TENANT,'telegram_id':20,'role':'owner'}

    def test_current_member_not_session_role_decides_authority(self):
        for member in (None, {'role':'admin','active':True,'tenant_status':'active'},
                       {'role':'owner','active':False,'tenant_status':'active'}):
            self.cur.fetchone.return_value=member
            with self.assertRaises(TenantLinearError):self.mod.snapshot(self.session)
            self.assertNotIn('to_regclass',self.cur.execute.call_args.args[0])

    def test_missing_tables_are_not_created(self):
        self.cur.fetchone.side_effect=[{'role':'owner','active':True,'tenant_status':'active'}]+[{'table_name':None}]*len(self.mod.QUEUES)
        self.cur.fetchall.return_value=[]
        data=self.mod.snapshot(self.session)
        self.assertTrue(all(not q['available'] for q in data['queues']))
        sql=' '.join(c.args[0] for c in self.cur.execute.call_args_list)
        self.assertNotIn('CREATE',sql);self.assertNotIn('UPDATE ',sql)
        self.assertNotIn('secrets',sql);self.assertIn('statement_timeout',sql)

    def test_counts_are_bounded_scoped_and_exclude_payloads(self):
        self.mod.snapshot.__globals__['QUEUES']=(('chat_sends','saas_chat_send_receipts','state','updated_at'),)
        self.cur.fetchone.side_effect=[{'role':'owner','active':True,'tenant_status':'active'},{'table_name':'table'}]
        self.cur.fetchall.side_effect=[[{'state':'unknown','count':1001,'stale':0,'oldest':None}],[]]
        data=self.mod.snapshot(self.session);self.assertTrue(data['queues'][0]['limited'])
        query=next(c for c in self.cur.execute.call_args_list if 'GROUP BY' in c.args[0])
        self.assertIn('LIMIT 1001',query.args[0]);self.assertEqual(query.args[1],(TENANT,))
        self.assertNotIn('payload',query.args[0]);self.assertNotIn('route',query.args[0])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_errors_redacted_and_have_request_id(self):
        snapshot=Mock(side_effect=RuntimeError('PRIVATE_TOKEN'))
        mod=load('tenant_diagnostics_api',{'snapshot':snapshot,'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},
                 {'tenant_diagnostics','aiohttp'})
        session={'tenant_id':TENANT,'telegram_id':20}
        request=SimpleNamespace(query={'expected_tenant_id':TENANT,'expected_user_id':'20'})
        response=await mod.handler(lambda _:session,Mock())(request)
        self.assertEqual(response['status'],503);self.assertNotIn('PRIVATE',str(response))
        self.assertEqual(response['data']['request_id'],response['headers']['X-Request-ID'])

    async def test_wrong_company_stops_before_queries(self):
        snapshot=Mock()
        mod=load('tenant_diagnostics_api',{'snapshot':snapshot,'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},
                 {'tenant_diagnostics','aiohttp'})
        response=await mod.handler(lambda _:{'tenant_id':TENANT,'telegram_id':20},Mock())(SimpleNamespace(query={}))
        self.assertEqual(response['status'],409);snapshot.assert_not_called()
