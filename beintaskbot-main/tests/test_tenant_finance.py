"""SaaS ledger contracts, with no real database or monetary operations."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,MagicMock
import test_tenant_hot_orders as loaders
from tenant_finance_policy import minor_amount,money_text,finance_policy,TenantFinanceError
from tenant_policy import validate_workflow_patch


def person(role='owner'):
    return {'tenant_id':'company-a','telegram_id':20,'role':role,'active':True,'permissions':['finance'],
            'modules':{'finance':True},'workflow':{'policies':{}}}


class MoneyTests(unittest.TestCase):
    def test_decimal_amounts_remain_exact(self):
        for value,amount in [('0.01',1),('100.00',10000),('999999999.99',99999999999),(1,100)]:
            self.assertEqual(minor_amount(value),amount)
        self.assertEqual(money_text(-10001),'-100.01')

    def test_invalid_or_float_amounts_are_rejected(self):
        for value in (True,1.23,'NaN','Infinity','0','-1','1.001','1000000000','1e999','1,23',None):
            with self.assertRaises(ValueError):minor_amount(value)

    def test_settings_are_validated(self):
        for value in ([],None,{'currency':'USD'},{'allow_negative_balances':'yes'}):
            with self.assertRaises(ValueError):validate_workflow_patch([],[],{'finance':value})
        validate_workflow_patch([],[],{'finance':{'currency':'AZN','allow_negative_balances':False}})

    def test_denied_inactive_master_and_worker_write(self):
        for changes in ({'active':False},{'permissions':[],'role':'worker'},{'role':'master'},{'tenant_status':'disabled'}):
            with self.assertRaises(TenantFinanceError):finance_policy({**person(),**changes})
        finance_policy(person('worker'))
        with self.assertRaises(TenantFinanceError):finance_policy(person('worker'),write=True)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.conn=MagicMock();self.conn.__enter__.return_value=self.conn
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.connect=Mock(return_value=self.conn)
        self.service=loaders.load('tenant_finance',{'_connect':self.connect,'ensure_finance_schema':Mock()},
                                  {'tenant_platform','tenant_finance_schema'})
        self.profile=person();self.data={'action':'credit','member_id':30,'amount':'100.00','note':'Payment',
             'request_id':'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'}
        self.row={'tenant_id':'company-a','id':'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','member_id':30,
                  'actor_id':20,'amount_minor':10000,'kind':'credit','currency':'AZN','note':'Payment','reverses_id':None}

    def test_credit_locks_company_membership_and_audits_in_same_transaction(self):
        self.cur.fetchone.side_effect=[None,{'active':True},{'balance':0},self.row]
        result=self.service.record(self.profile,self.data)
        self.assertEqual(result['amount'],'100.00');self.assertNotIn('amount_minor',result)
        calls=self.cur.execute.call_args_list
        self.assertIn('pg_advisory_xact_lock',calls[0].args[0]);self.assertIn('FOR UPDATE',calls[2].args[0])
        self.assertEqual(calls[2].args[1],('company-a',30));self.assertEqual(calls[4].args[1][0],'company-a')
        self.assertIn('saas_tenant_audit_events',calls[-1].args[0]);self.conn.commit.assert_called_once()

    def test_debit_is_negative_and_insufficient_balance_is_not_committed(self):
        self.cur.fetchone.side_effect=[None,{'active':True},{'balance':9999}]
        with self.assertRaises(TenantFinanceError) as error:self.service.record(self.profile,{**self.data,'action':'debit'})
        self.assertEqual(error.exception.status,409);self.conn.commit.assert_not_called()
        self.cur.fetchone.side_effect=[None,{'active':True},{'balance':10000},{**self.row,'kind':'debit','amount_minor':-10000}]
        self.service.record(self.profile,{**self.data,'action':'debit'})
        self.assertEqual(self.cur.execute.call_args_list[-2].args[1][7],-10000)

    def test_negative_balance_requires_company_opt_in(self):
        self.profile['workflow']['policies']['finance']={'allow_negative_balances':True}
        self.cur.fetchone.side_effect=[None,{'active':True},{'balance':0},{**self.row,'kind':'debit','amount_minor':-10000}]
        self.service.record(self.profile,{**self.data,'action':'debit'});self.conn.commit.assert_called_once()

    def test_same_request_returns_prior_entry_without_second_write(self):
        payload={'member':30,'action':'credit','amount':10000,'source':None,'note':'Payment'}
        stamp=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        self.cur.fetchone.return_value={**self.row,'fingerprint':stamp}
        self.assertEqual(self.service.record(self.profile,self.data)['id'],self.row['id'])
        self.assertEqual(self.cur.execute.call_count,2)

    def test_changed_payload_with_same_request_is_conflict(self):
        self.cur.fetchone.return_value={**self.row,'fingerprint':'old'}
        with self.assertRaises(TenantFinanceError) as error:self.service.record(self.profile,self.data)
        self.assertEqual(error.exception.status,409);self.conn.commit.assert_not_called()

    def test_worker_cannot_record_and_cannot_read_other_employee(self):
        with self.assertRaises(ValueError):self.service.record(person('worker'),self.data)
        with self.assertRaises(ValueError):self.service.history(person('worker'),member_id=30)
        self.connect.assert_not_called()

    def test_missing_or_inactive_target_is_rejected(self):
        for recipient in (None,{'active':False}):
            self.cur.fetchone.side_effect=[None,recipient]
            with self.assertRaises(ValueError):self.service.record(self.profile,self.data)
        self.conn.commit.assert_not_called()

    def test_reverse_adds_opposite_entry_never_edits_original(self):
        self.cur.fetchone.side_effect=[None,{'active':False},self.row,None,{'balance':10000},
            {**self.row,'kind':'reverse','amount_minor':-10000,'reverses_id':self.row['id']}]
        result=self.service.record(self.profile,{**self.data,'action':'reverse','entry_id':self.row['id']})
        self.assertEqual(result['amount'],'-100.00')
        sql=' '.join(call.args[0] for call in self.cur.execute.call_args_list)
        self.assertNotIn('UPDATE saas_finance_entries',sql);self.assertNotIn('DELETE',sql)
        self.assertIn('tenant_id=%s::uuid AND member_id=%s AND id=%s::uuid',sql)

    def test_reverse_of_reverse_or_already_reversed_is_rejected(self):
        for original,previous in (({**self.row,'kind':'reverse'},None),(self.row,{'id':'old'})):
            self.cur.fetchone.side_effect=[None,{'active':True},original,previous]
            with self.assertRaises(ValueError):self.service.record(self.profile,{**self.data,'action':'reverse','entry_id':self.row['id']})
        self.conn.commit.assert_not_called()

    def test_audit_failure_cannot_commit_money_entry(self):
        self.cur.fetchone.side_effect=[None,{'active':True},{'balance':0},self.row]
        self.cur.execute.side_effect=[None,None,None,None,None,RuntimeError('Audit unavailable')]
        with self.assertRaises(RuntimeError):self.service.record(self.profile,self.data)
        self.conn.commit.assert_not_called()

    def test_history_is_paged_tenant_scoped_and_snapshot_consistent(self):
        self.cur.fetchone.side_effect=[{'display_name':'Person','active':True},{'balance':10000,'total':1}]
        self.cur.fetchall.return_value=[self.row]
        result=self.service.history(person('worker'),limit=999,offset=-5)
        self.assertEqual(result['balance'],'100.00');self.assertFalse(result['can_write'])
        self.assertIn('REPEATABLE READ READ ONLY',self.cur.execute.call_args_list[0].args[0])
        self.assertEqual(self.cur.execute.call_args.args[1],('company-a',20,100,0))

    def test_schema_is_separate_preserves_history_and_deduplicates_reversals(self):
        schema=loaders.load('tenant_finance_schema',{'_ensure_schema':Mock()},{'tenant_platform'})
        schema.ensure_finance_schema(self.conn)
        sql=' '.join(call.args[0] for call in self.cur.execute.call_args_list)
        self.assertIn('UNIQUE (tenant_id,actor_id,request_id)',sql);self.assertIn('UNIQUE (tenant_id,reverses_id)',sql)
        self.assertIn('ON DELETE RESTRICT',sql);self.assertNotIn('DROP',sql);self.assertNotIn('ALTER TABLE',sql)


class FinanceApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.history=Mock(return_value={'entries':[]});self.record=Mock(return_value={'id':'entry'});self.members=Mock(return_value={'members':[]})
        self.module=loaders.load('tenant_finance_api',{'history':self.history,'record':self.record,'list_members':self.members,
            'web':SimpleNamespace(Request=dict,Response=dict,json_response=lambda body,**kw:{'body':body,**kw})},
            {'tenant_finance','aiohttp'})
        self.log=Mock();self.handle=self.module.finance_handler(lambda request:person(),'https://crm.pro.az',self.log)

    async def request(self,data=None,method='POST',origin='https://crm.pro.az',query=None):
        async def read():return data
        return await self.handle(SimpleNamespace(method=method,content_type='application/json',headers={'Origin':origin},json=read,
                           rel_url=SimpleNamespace(query=query or {})))

    async def test_origin_and_switched_company_fail_before_write(self):
        self.assertEqual((await self.request({},origin='https://evil.invalid'))['status'],403)
        self.assertEqual((await self.request({'expected_tenant_id':'foreign','expected_user_id':20}))['status'],409)
        self.record.assert_not_called()

    async def test_api_uses_server_profile_and_no_store(self):
        data={'action':'credit','expected_tenant_id':'company-a','expected_user_id':20}
        result=await self.request(data);self.record.assert_called_once_with(person(),data)
        self.assertEqual(result['headers']['Cache-Control'],'no-store')

    async def test_get_member_picker_and_history_are_separate_bounded_calls(self):
        await self.request(method='GET',query={'members':'1','limit':'100'})
        self.members.assert_called_once_with(person(),limit=100,offset=0)
        await self.request(method='GET');self.history.assert_called_once_with(person(),member_id=None,limit=50,offset=0)

    async def test_db_failure_is_generic_without_sensitive_error_logs(self):
        self.history.side_effect=RuntimeError('PRIVATE MONEY')
        result=await self.request(method='GET');self.assertEqual(result['status'],503)
        self.assertNotIn('PRIVATE',str(result));self.log.error.assert_called_once_with('Tenant finance API failed')
