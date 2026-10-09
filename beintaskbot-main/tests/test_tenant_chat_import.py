"""Manual history import isolation and bounded provider/cache contracts."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, MagicMock
from tenant_chat_import_service import run, position, token
from tenant_linear_policy import TenantLinearError
from test_tenant_hot_orders import load
from test_tenant_chat_send import TENANT, SESSION, talk, page


class Cursor(unittest.TestCase):
    def test_roundtrip_scope_and_bounded_page(self):
        scope=[TENANT,'20',7,'connection','catalog']
        self.assertEqual(position('',scope,1),(0,1))
        self.assertEqual(position(token(scope,0,1000),scope,1),(0,1000))
        for value in [token(scope,0,1001),token(scope,1,1),token(scope,0,0),token(scope,True,1),
                      token(['foreign'],0,1),'?', 'a'*2049]:
            with self.assertRaises(TenantLinearError):position(value,scope,1)


class Import(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth=Mock(return_value='connection')
        self.member=Mock(return_value={**SESSION,'active':True,'tenant_status':'active'})
        self.write=Mock();self.policy=Mock();self.policy.can_access_deal.return_value=True
        self.module=load('tenant_chat_import_service',{'TenantPolicy':lambda profile:self.policy}, {'tenant_policy'})
        self.request=AsyncMock(side_effect=[{'talks':[talk(1)]},page(1),{'id':7}])
        self.normalize=lambda row,channel:{'external_id':row['id'],'channel':channel,'body':'Salam'}

    async def run_import(self,cursor=''):
        return await self.module.run(SESSION,7,cursor,self.request,self.auth,self.member,self.write,self.normalize,lambda row:'fallback')

    async def test_single_page_keeps_tenant_bound_paths_and_origin(self):
        result=await self.run_import()
        self.assertEqual(result,{'imported':1,'has_more':False,'next_cursor':None})
        self.assertEqual(self.request.await_count,3)
        self.assertEqual(self.request.call_args_list[1].kwargs['params'],{'page':1,'limit':250})
        self.assertTrue(all(c.args[0]==TENANT and c.args[1]=='GET' for c in self.request.call_args_list))
        self.write.assert_called_once_with(tenant_id=TENANT,kommo_lead_id=7,messages=[{'external_id':page(1)['messages'][0]['id'],'channel':'instagram','body':'Salam'}])
        self.assertEqual(self.auth.call_count,2)

    async def test_full_page_then_next_page_no_provider_url_followed(self):
        messages=[{**page(1)['messages'][0],'id':str(i)} for i in range(250)]
        self.request.side_effect=[{'talks':[talk(1)]},{'messages':messages,'_links':{'next':{'href':'https://evil.test/private'}}},{'id':7}]
        first=await self.run_import();self.assertTrue(first['has_more'])
        self.request.reset_mock();self.request.side_effect=[{'talks':[talk(1)]},page(1),{'id':7}]
        second=await self.run_import(first['next_cursor']);self.assertFalse(second['has_more'])
        self.assertEqual(self.request.call_args_list[1].kwargs['params']['page'],2)
        self.assertFalse(any('evil' in c.args[2] for c in self.request.call_args_list))

    async def test_next_talk_only_after_current_page_exhausted(self):
        self.request.side_effect=[{'talks':[talk(2),talk(1)]},page(1),{'id':7}]
        first=await self.run_import();self.assertTrue(first['has_more'])
        self.request.reset_mock();self.request.side_effect=[{'talks':[talk(1),talk(2)]},page(2),{'id':7}]
        self.assertFalse((await self.run_import(first['next_cursor']))['has_more'])
        self.assertEqual(self.request.call_args_list[1].args[2],'talks/2/messages')

    async def test_wrong_chat_malformed_or_conflicting_page_never_written(self):
        first=page(1)['messages'][0]
        for payload in [page(2),{'messages':[None]}, {'messages':[first,{**first,'text':'changed'}]},
                        {'messages':[first]*251},{'messages':[],'_links':{'next':{}}}]:
            self.request.side_effect=[{'talks':[talk(1)]},payload]
            with self.assertRaises(TenantLinearError):await self.run_import()
        self.write.assert_not_called()

    async def test_duplicate_identical_messages_are_one_cache_row(self):
        self.request.side_effect=[{'talks':[talk(1)]},{'messages':page(1)['messages']*2},{'id':7}]
        self.assertEqual((await self.run_import())['imported'],1)

    async def test_authority_revoked_or_connection_changed_stops_write(self):
        self.auth.side_effect=['old','new']
        with self.assertRaises(TenantLinearError):await self.run_import()
        self.write.assert_not_called()

    async def test_live_move_inactive_or_wrong_id_stops_write(self):
        for member in [None,{**SESSION,'active':False,'tenant_status':'active'}]:
            self.request.side_effect=[{'talks':[talk(1)]},page(1),{'id':7}];self.member.return_value=member
            with self.assertRaises(TenantLinearError):await self.run_import()
        self.member.return_value={**SESSION,'active':True,'tenant_status':'active'}
        self.request.side_effect=[{'talks':[talk(1)]},page(1),{'id':8}]
        with self.assertRaises(TenantLinearError):await self.run_import()
        self.policy.can_access_deal.return_value=False
        self.request.side_effect=[{'talks':[talk(1)]},page(1),{'id':7}]
        with self.assertRaises(TenantLinearError):await self.run_import()
        self.write.assert_not_called()

    async def test_catalog_change_rejects_cursor_before_message_fetch(self):
        first=await self.run_import()
        # Build a continuing cursor using an initial two-talk catalog.
        self.request.side_effect=[{'talks':[talk(1),talk(2)]},page(1),{'id':7}]
        first=await self.run_import()
        self.request.reset_mock();self.request.side_effect=[{'talks':[talk(1)]}]
        with self.assertRaises(TenantLinearError):await self.run_import(first['next_cursor'])
        self.assertEqual(self.request.await_count,1)

    async def test_write_failure_does_not_return_success_cursor(self):
        self.write.side_effect=RuntimeError('database')
        with self.assertRaises(RuntimeError):await self.run_import()

    async def test_empty_catalog_completes_without_write(self):
        self.request.side_effect=[{'talks':[]}]
        self.assertFalse((await self.run_import())['has_more']);self.write.assert_not_called()


class Store(unittest.TestCase):
    def setUp(self):
        self.conn=MagicMock();self.conn.__enter__.return_value=self.conn
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.policy=Mock();self.policy.can_access_deal.return_value=True
        self.module=load('tenant_chat_import_store',{'_connect':Mock(return_value=self.conn),'_ensure_schema':Mock(),
            '_read_tenant_workflow':Mock(return_value={}), 'TenantPolicy':lambda profile:self.policy}, {'tenant_platform','tenant_policy'})

    def test_no_credentials_selected_and_all_queries_tenant_scoped(self):
        self.cur.fetchone.side_effect=[{'active':True,'tenant_status':'active'},{'kommo_lead_id':7},
                                      {'account_domain':'company.kommo.com','connected_at':'date'}]
        self.assertEqual(self.module.authorize(SESSION,7),'company.kommo.com:date')
        for call in self.cur.execute.call_args_list:
            self.assertIn('tenant_id',call.args[0]);self.assertEqual(call.args[1][0],TENANT)
            self.assertNotIn('secrets',call.args[0])

    def test_revoked_or_deleted_deal_and_missing_connection_fail_closed(self):
        for rows in [[None],[{'active':False,'tenant_status':'active'}],
                     [{'active':True,'tenant_status':'active'},None,None]]:
            self.cur.fetchone.side_effect=rows
            with self.assertRaises(TenantLinearError):self.module.authorize(SESSION,7)
        self.policy.can_access_deal.return_value=False
        self.cur.fetchone.side_effect=[{'active':True,'tenant_status':'active'},None]
        with self.assertRaises(TenantLinearError):self.module.authorize(SESSION,7)


if __name__=='__main__':unittest.main()
