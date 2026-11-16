import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from tenant_chat_recent import handler
from tenant_chat_reader import read_recent_conversations
from tenant_linear_policy import TenantLinearError


class Recent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {'tenant_id': 'a', 'telegram_id': 11}
        self.request = SimpleNamespace(query={'expected_tenant_id': 'a', 'expected_user_id': '11'})
        self.context = SimpleNamespace(member=Mock(side_effect=lambda _: self.profile),
            get_deal=Mock(return_value={'raw': {}}), policy=Mock(), sync_chat=AsyncMock(), logger=Mock())
        self.context.policy.return_value.can_read_kommo_chat.return_value=True
        self.page=Mock(return_value={'messages': [], 'next_cursor': None})
        self.patch=patch('tenant_chat_recent.page', self.page);self.patch.start();self.addCleanup(self.patch.stop)
        self.handle=handler(self.context)

    async def test_identity_mismatch_never_reads_or_syncs(self):
        self.request.query['expected_user_id']='22'
        self.assertEqual((await self.handle(self.request,7)).status,409)
        self.page.assert_not_called();self.context.sync_chat.assert_not_called()

    async def test_coalesces_refresh_but_reauthorizes_every_response(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def sync(*args,**kw):entered.set();await release.wait()
        self.context.sync_chat.side_effect=sync
        first=asyncio.create_task(self.handle(self.request,7));await entered.wait()
        second=asyncio.create_task(self.handle(self.request,7))
        while self.page.call_count<2:await asyncio.sleep(0)
        await asyncio.sleep(.02);release.set()
        responses=await asyncio.gather(first,second)
        self.context.sync_chat.assert_awaited_once_with(self.profile,7,recent=True)
        self.assertEqual(self.page.call_count,4)
        for response in responses:
            self.assertEqual(response.status,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertIn('provider_refresh;dur=',response.headers['Server-Timing'])

    async def test_revocation_during_provider_cannot_return_old_snapshot(self):
        self.page.side_effect=[{'messages':[{'body':'private'}]},TenantLinearError('Denied',403)]
        response=await self.handle(self.request,7)
        self.assertEqual(response.status,403);self.assertNotIn('private',response.text)

    async def test_session_switch_during_provider_blocks_final_read(self):
        async def sync(*a,**kw):self.profile={**self.profile,'tenant_id':'b'}
        self.context.sync_chat.side_effect=sync
        self.assertEqual((await self.handle(self.request,7)).status,409)
        self.assertEqual(self.page.call_count,1)

    async def test_provider_error_returns_fresh_authorized_cache_and_no_secret(self):
        self.context.sync_chat.side_effect=RuntimeError('secret message credential')
        response=await self.handle(self.request,7)
        self.assertEqual(response.status,200);self.assertTrue(json.loads(response.text)['stale'])
        self.assertNotIn('secret',response.text);self.assertNotIn('secret',str(self.context.logger.mock_calls))
        self.assertEqual(self.page.call_args.kwargs,{'limit':40})

    async def test_connect_native_or_denied_provider_never_syncs(self):
        self.context.get_deal.return_value={'raw':{'source':'connect'}}
        self.assertEqual((await self.handle(self.request,7)).status,200)
        self.context.get_deal.return_value={'raw':{}}
        self.context.policy.return_value.can_read_kommo_chat.return_value=False
        self.assertEqual((await self.handle(self.request,7)).status,200)
        self.context.sync_chat.assert_not_called()

    async def test_source_denial_prevents_provider(self):
        self.page.side_effect=TenantLinearError('Denied',403)
        self.assertEqual((await self.handle(self.request,7)).status,403)
        self.context.sync_chat.assert_not_called()


class BoundedReader(unittest.IsolatedAsyncioTestCase):
    async def test_one_page_and_timestamp_filter_no_following_provider_urls(self):
        talk={'id':1,'entity_id':7,'entity_type':'lead','chat_id':'exact'}
        request=AsyncMock(side_effect=[{'_embedded':{'talks':[talk]}},{'_embedded':{'messages':[{'id':'m','chat_id':'exact'}]}}])
        result=await read_recent_conversations('a',7,request,123)
        self.assertEqual(result[0][1][0]['id'],'m')
        self.assertEqual(request.await_args.kwargs['params'],{'page':1,'limit':50,'filter[created_at][from]':123})
        self.assertEqual(request.await_count,2)

    async def test_overflow_and_wrong_chat_fail_without_following_next_page(self):
        talk={'id':1,'entity_id':7,'entity_type':'lead','chat_id':'exact'}
        for payload in [{'_links':{'next':{'href':'https://secret.invalid'}},'messages':[]},
                        {'messages':[{'id':str(i),'chat_id':'exact'} for i in range(50)]},
                        {'messages':[{'id':'m','chat_id':'other'}]}]:
            request=AsyncMock(side_effect=[{'talks':[talk]},payload])
            with self.assertRaises(TenantLinearError):await read_recent_conversations('a',7,request,123)
            self.assertEqual(request.await_count,2)
