"""Tenant SQL/API contracts without a production database or messages."""
import ast
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

from tenant_policy import TenantPolicy
import test_tenant_hot_order_policy as fixtures

ROOT = Path(__file__).resolve().parents[1]


def load(name, namespace, exclude):
    tree=ast.parse((ROOT/(name+'.py')).read_text(encoding='utf-8-sig'))
    tree.body=[n for n in tree.body if not isinstance(n,ast.ImportFrom) or n.module not in exclude]
    exec(compile(tree,name,'exec'),namespace)
    return SimpleNamespace(**namespace)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.service=load('tenant_hot_orders',{'_connect':self.connect,'ensure_hot_order_schema':Mock()},
                          {'tenant_platform','tenant_hot_order_schema'})
        self.profile=fixtures.person('owner')
        self.order=fixtures.order(id='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',claimed_by=None)
        self.data={'request_id':'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb','service_id':'repair',
                   'client_name':'Client','description':'Repair printer'}

    def test_create_uses_session_identity_and_audits_in_same_commit(self):
        self.cur.fetchone.return_value=self.order
        result=self.service.create_order(self.profile,{**self.data,'tenant_id':'foreign','created_by':999})
        insert=self.cur.execute.call_args_list[0]
        self.assertEqual(insert.args[1][0],'company-a');self.assertEqual(insert.args[1][-1],20)
        self.assertIn('ON CONFLICT (tenant_id,created_by,request_id) DO NOTHING',insert.args[0])
        self.assertIn('saas_tenant_audit_events',self.cur.execute.call_args_list[1].args[0])
        self.assertIn('saas_hot_order_events',self.cur.execute.call_args.args[0])
        self.conn.commit.assert_called_once();self.assertEqual(result['id'],self.order['id'])

    def test_duplicate_create_returns_same_order_without_second_audit(self):
        payload={**{k:self.data[k] for k in ('service_id','client_name','description')},'phone':'','address':'','priority':'normal'}
        stamp=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        self.cur.fetchone.side_effect=[None,{**self.order,'fingerprint':stamp,'request_id':self.data['request_id']}]
        result=self.service.create_order(self.profile,self.data)
        self.assertNotIn('fingerprint',result);self.assertNotIn('request_id',result)
        self.assertEqual(len(self.cur.execute.call_args_list),2)

    def test_same_request_with_different_payload_is_conflict(self):
        self.cur.fetchone.side_effect=[None,{**self.order,'fingerprint':'other'}]
        with self.assertRaises(self.service.TenantHotOrderError) as error:
            self.service.create_order(self.profile,self.data)
        self.assertEqual(error.exception.status,409);self.conn.commit.assert_not_called()

    def test_audit_failure_cannot_commit_creation(self):
        self.cur.fetchone.return_value=self.order
        self.cur.execute.side_effect=[None,RuntimeError('audit unavailable')]
        with self.assertRaises(RuntimeError):self.service.create_order(self.profile,self.data)
        self.conn.commit.assert_not_called()

    def test_invalid_creation_does_not_open_database(self):
        for data in ({**self.data,'description':''},{**self.data,'service_id':'old'},
                     {**self.data,'priority':'unknown'},{**self.data,'request_id':'bad'}):
            with self.assertRaises(ValueError):self.service.create_order(self.profile,data)
        self.connect.assert_not_called()

    def test_list_scope_is_applied_before_limit_and_count(self):
        self.cur.fetchone.return_value={'total':0};self.cur.fetchall.return_value=[]
        self.service.list_orders(fixtures.person(),limit=9999,offset=-1)
        calls=self.cur.execute.call_args_list
        for call in calls:
            self.assertIn('tenant_id=%s::uuid',call.args[0]);self.assertIn('service_id=ANY(%s)',call.args[0])
            self.assertEqual(call.args[1][:4],['company-a',20,20,['repair']])
        self.assertEqual(calls[-1].args[1][-2:],[100,0])

    def test_claim_locks_and_updates_tenant_and_prior_status(self):
        claimed={**self.order,'status':'claimed','claimed_by':20}
        self.cur.fetchone.side_effect=[self.order,claimed]
        result=self.service.change_order(fixtures.person(),order_id=self.order['id'],action='claim')
        self.assertEqual(result['claimed_by'],20)
        read,write,audit=self.cur.execute.call_args_list
        self.assertIn('FOR UPDATE',read.args[0]);self.assertEqual(read.args[1],('company-a',self.order['id']))
        self.assertIn('AND status=%s',write.args[0]);self.assertEqual(write.args[1][-3:],('company-a',self.order['id'],'open'))
        self.assertIn('saas_tenant_audit_events',audit.args[0]);self.conn.commit.assert_called_once()

    def test_second_employee_cannot_claim_accepted_order(self):
        self.cur.fetchone.return_value={**self.order,'status':'claimed','claimed_by':21}
        with self.assertRaises(ValueError):self.service.change_order(fixtures.person(),order_id=self.order['id'],action='claim')
        self.assertEqual(len(self.cur.execute.call_args_list),1);self.conn.commit.assert_not_called()

    def test_repeat_claim_by_same_worker_is_read_only(self):
        self.cur.fetchone.return_value={**self.order,'status':'claimed','claimed_by':20}
        self.service.change_order(fixtures.person(),order_id=self.order['id'],action='claim')
        self.assertEqual(len(self.cur.execute.call_args_list),1)

    def test_server_actions_follow_status_and_rights(self):
        policy=self.service.authorize(fixtures.person())
        result=self.service.public_row(self.order,policy)
        self.assertEqual(result['actions'],{'claim':True,'cancel':False,'release':False,'complete':False,'approve':False,'reject':False})
        self.assertEqual(result['service_name'],'Təmir')
        result=self.service.public_row({**self.order,'status':'claimed','claimed_by':20},policy)
        self.assertEqual(result['actions'],{'claim':False,'cancel':False,'release':True,'complete':True,'approve':False,'reject':False})

    def test_creator_cannot_cancel_or_release_order_accepted_by_another_employee(self):
        self.cur.fetchone.return_value={**self.order,'created_by':20,'status':'claimed','claimed_by':21}
        for action in ('cancel','release'):
            with self.assertRaises(ValueError):self.service.change_order(self.profile,order_id=self.order['id'],action=action)
        self.assertFalse(any('UPDATE saas_hot_orders' in c.args[0] for c in self.cur.execute.call_args_list))

    def test_accepting_worker_can_release(self):
        self.cur.fetchone.side_effect=[{**self.order,'status':'claimed','claimed_by':20},self.order]
        self.service.change_order(fixtures.person(),order_id=self.order['id'],action='release')
        update=self.cur.execute.call_args_list[1]
        self.assertEqual(update.args[1][:3],('open',None,'open'))

    def test_cross_tenant_row_is_not_mutated_even_for_owner(self):
        self.cur.fetchone.return_value={**self.order,'tenant_id':'foreign'}
        with self.assertRaises(ValueError):self.service.change_order(self.profile,order_id=self.order['id'],action='cancel')
        self.assertEqual(len(self.cur.execute.call_args_list),1)

    def test_schema_is_additive_and_has_composite_identity_and_request_deduplication(self):
        schema=load('tenant_hot_order_schema',{'_ensure_schema':Mock(),'ensure_finance_schema':Mock()},
                    {'tenant_platform','tenant_finance_schema'})
        schema.ensure_hot_order_schema(self.conn)
        sql=' '.join(c.args[0] for c in self.cur.execute.call_args_list)
        self.assertIn('PRIMARY KEY (tenant_id,id)',sql);self.assertIn('UNIQUE (tenant_id,created_by,request_id)',sql)
        self.assertNotIn('ALTER TABLE hot_orders',sql);self.assertNotIn('DROP TABLE',sql)
        self.assertIn("pg_get_constraintdef(oid) NOT LIKE '%submitted%'",sql)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile=fixtures.person('owner');self.list=Mock(return_value={'orders':[],'total':0})
        self.create=Mock(return_value={'id':'order'});self.change=Mock(return_value={})
        service=load('tenant_hot_orders',{'_connect':Mock(),'ensure_hot_order_schema':Mock()},
                     {'tenant_platform','tenant_hot_order_schema'})
        tree=ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        tree.body=[n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='handle_platform_hot_orders']
        self.ns={'asyncio':asyncio,'TenantPolicy':TenantPolicy,'TenantHotOrderError':service.TenantHotOrderError,
                 '_tenant_member_from_request':lambda request:self.profile,'CANONICAL_WEB_ORIGIN':'https://crm.pro.az',
                 'list_tenant_hot_orders':self.list,'create_tenant_hot_order':self.create,'change_tenant_hot_order':self.change,
                 'logger':Mock(),'web':SimpleNamespace(Request=dict,Response=dict,json_response=lambda data,**kw:{'data':data,**kw})}
        exec(compile(tree,'<hot-api>','exec'),self.ns)

    async def request(self, data=None, method='POST', origin='https://crm.pro.az', query=None):
        async def read():return data
        return await self.ns['handle_platform_hot_orders'](SimpleNamespace(method=method,content_type='application/json',
            headers={'Origin':origin},json=read,rel_url=SimpleNamespace(query=query or {})))

    async def test_get_uses_only_current_membership(self):
        result=await self.request(method='GET');self.assertTrue(result['data']['success'])
        self.list.assert_called_once_with(self.profile,limit=50,offset=0)

    async def test_get_review_queue_passes_filter_to_tenant_service(self):
        await self.request(method='GET',query={'approvals':'1'})
        self.list.assert_called_once_with(self.profile,limit=50,offset=0,approvals_only=True)

    async def test_completion_routes_through_same_session_and_origin_checks(self):
        command=Mock(return_value={'status':'submitted'})
        with patch.dict(sys.modules,{'tenant_hot_order_completion':SimpleNamespace(completion_command=command)}):
            for action in ('complete','approve','reject'):
                data={'action':action,'expected_tenant_id':'company-a','expected_user_id':20}
                response=await self.request(data)
                self.assertTrue(response['data']['success']);command.assert_called_with(self.profile,data)
        self.create.assert_not_called();self.change.assert_not_called()

    async def test_cross_origin_and_switched_company_are_rejected(self):
        self.assertEqual((await self.request({},origin='https://evil.invalid'))['status'],403)
        self.assertEqual((await self.request({'expected_tenant_id':'foreign','expected_user_id':20}))['status'],409)
        self.create.assert_not_called();self.change.assert_not_called()

    async def test_post_uses_current_identity_not_payload_identity(self):
        data={'action':'create','expected_tenant_id':'company-a','expected_user_id':20,'tenant_id':'foreign'}
        result=await self.request(data);self.assertTrue(result['data']['success'])
        self.create.assert_called_once_with(self.profile,data)

    async def test_closed_page_rejects_even_get(self):
        self.profile=fixtures.person();self.profile['permissions']=[]
        self.assertEqual((await self.request(method='GET'))['status'],403);self.list.assert_not_called()

    async def test_storage_failure_is_generic_and_no_customer_details_are_logged(self):
        self.list.side_effect=RuntimeError('password and private client')
        result=await self.request(method='GET');self.assertEqual(result['status'],503)
        self.assertNotIn('password',str(result));self.ns['logger'].error.assert_called_once_with('Tenant hot-order API failed')
