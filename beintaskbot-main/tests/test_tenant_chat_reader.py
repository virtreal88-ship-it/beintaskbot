import unittest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
import test_tenant_hot_orders as loaders
from test_tenant_chat_send import TENANT, SESSION, talk, page
from tenant_chat_reader import read_messages, read_conversations
from tenant_chat_send_policy import resolve_route
from tenant_linear_policy import TenantLinearError


class Reader(unittest.IsolatedAsyncioTestCase):
    async def test_next_page_latest_inbound_changes_route(self):
        first={**page(1,10), '_links':{'next':{'href':'https://evil.invalid/secret'}}}
        second=page(1,200,'source-two'); second['messages'][0]['id']='new'
        provider=AsyncMock(side_effect=[{'_embedded':{'talks':[talk(1),talk(2)]}},first,second,page(2,100)])
        result=await resolve_route(TENANT,7,provider)
        self.assertEqual(result['origin'],'source-two');self.assertEqual(result['talk_id'],1)
        self.assertEqual(provider.call_args_list[2].kwargs['params'],{'limit':250,'page':2})
        self.assertTrue(all(c.args[0]==TENANT and c.args[1]=='GET' for c in provider.call_args_list))
        self.assertTrue(all('evil' not in c.args[2] for c in provider.call_args_list))

    async def test_full_page_without_link_probes_next_number(self):
        rows=[{**page(1)['messages'][0],'id':str(i)} for i in range(250)]
        provider=AsyncMock(side_effect=[{'messages':rows},{}])
        self.assertEqual(len(await read_messages(TENANT,talk(1),provider)),250)
        self.assertEqual(provider.await_count,2)

    async def test_bounded_pages_fail_before_fifth(self):
        pages=[{**page(1), '_links':{'next':{}}, 'messages':[{**page(1)['messages'][0],'id':str(i)}]} for i in range(4)]
        provider=AsyncMock(side_effect=pages)
        with self.assertRaises(TenantLinearError):await read_messages(TENANT,talk(1),provider)
        self.assertEqual(provider.await_count,4)

    async def test_overlapping_identical_rows_deduplicate(self):
        first={**page(1),'_links':{'next':{}}};second=page(1)
        second['messages'].append({**second['messages'][0],'id':'new'})
        result=await read_messages(TENANT,talk(1),AsyncMock(side_effect=[first,second]))
        self.assertEqual(len(result),2)

    async def test_changed_duplicates_and_nonprogress_fail(self):
        first={**page(1),'_links':{'next':{}}}
        for second in [first,page(1,200)]:
            with self.assertRaises(TenantLinearError):await read_messages(TENANT,talk(1),AsyncMock(side_effect=[first,second]))

    async def test_foreign_chat_on_second_page_rejected(self):
        with self.assertRaises(TenantLinearError):await read_messages(TENANT,talk(1),AsyncMock(side_effect=[{**page(1),'_links':{'next':{}}},page(2)]))

    async def test_invalid_shapes_ids_and_empty_continuation_fail(self):
        for payload in [[],{'messages':'oops'},{'_embedded':[],'messages':None},{'messages':[None]},
                        {'messages':[{'chat_id':'1'}]},{'_links':{'next':{}},'messages':[]}]:
            with self.assertRaises(TenantLinearError):await read_messages(TENANT,talk(1),AsyncMock(return_value=payload))

    async def test_incomplete_or_duplicate_catalog_rejected(self):
        for talks in [[talk(1)]*2,[talk(i) for i in range(1,8)],[{**talk(1),'entity_id':8}],[{**talk(1),'talk_id':'bad'}]]:
            provider=AsyncMock(return_value={'_embedded':{'talks':talks}})
            with self.assertRaises(TenantLinearError):await read_conversations(TENANT,7,provider)
            self.assertEqual(provider.await_count,1)


class Sync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.member=Mock(return_value={**SESSION,'tenant_status':'active'})
        self.deal=Mock(return_value={'tenant_id':TENANT,'kommo_lead_id':7})
        self.list=Mock(return_value=['cached']);self.messages=Mock();self.deals=Mock()
        self.reader=AsyncMock(return_value=[(talk(1),page(1)['messages']),(talk(2),page(2)['messages'])])
        self.policy=Mock();self.policy.can_access_deal.return_value=True
        self.module=loaders.load('tenant_chat_sync',{'read_conversations':self.reader,
            'TenantPolicy':lambda _:self.policy,'TenantPlatformError':TenantLinearError},
            {'tenant_chat_reader','tenant_policy','tenant_platform'})
        self.request=AsyncMock(return_value={'id':7,'pipeline_id':1,'status_id':1})
        self.normalize=lambda raw,channel:{'external_id':raw['id'],'body':'hello','happened_at':'2026-10-09T00:00:00+00:00','channel':channel}

    async def run_sync(self):
        return await self.module.sync(SESSION,7,self.request,self.member,self.deal,self.list,self.messages,self.deals,self.normalize,lambda _:'fallback')

    async def test_multiple_talks_single_batch_exact_origins(self):
        self.assertEqual(await self.run_sync(),['cached'])
        batch=self.messages.call_args.kwargs
        self.assertEqual(batch['tenant_id'],TENANT);self.assertEqual(len(batch['messages']),2)
        self.assertEqual(batch['messages'][0]['channel'],'instagram')
        self.messages.assert_called_once();self.assertEqual(self.member.call_count,2)

    async def test_read_failure_never_caches_partial_data(self):
        self.reader.side_effect=TenantLinearError('incomplete')
        with self.assertRaises(TenantLinearError):await self.run_sync()
        self.messages.assert_not_called();self.deals.assert_not_called()

    async def test_revocation_during_read_stops_cache_write(self):
        self.member.side_effect=[{**SESSION,'tenant_status':'active'},None]
        with self.assertRaises(TenantLinearError):await self.run_sync()
        self.messages.assert_not_called();self.request.assert_not_awaited()

    async def test_live_move_or_wrong_lead_blocks_cache(self):
        self.policy.can_access_deal.side_effect=[True,True,False]
        with self.assertRaises(TenantLinearError):await self.run_sync()
        self.messages.assert_not_called()
        self.policy.can_access_deal.side_effect=None;self.request.return_value={'id':8}
        with self.assertRaises(TenantLinearError):await self.run_sync()
        self.messages.assert_not_called()

    async def test_conflicting_message_ids_from_two_chats_stop_write(self):
        self.reader.return_value=[(talk(1),page(1)['messages']),(talk(2),[{**page(2)['messages'][0],'id':'msg-1','origin':'other'}])]
        with self.assertRaises(TenantLinearError):await self.run_sync()
        self.messages.assert_not_called()

    async def test_newer_cached_preview_not_replaced_by_old_read(self):
        self.deal.return_value={'tenant_id':TENANT,'kommo_lead_id':7,'last_message_at':'2026-10-10T00:00:00+00:00'}
        await self.run_sync();self.messages.assert_called_once();self.deals.assert_not_called()

    async def test_storage_failure_does_not_report_success(self):
        self.messages.side_effect=RuntimeError('database')
        with self.assertRaises(RuntimeError):await self.run_sync()
        self.list.assert_not_called();self.deals.assert_not_called()


if __name__=='__main__':unittest.main()
