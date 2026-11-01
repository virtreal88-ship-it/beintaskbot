"""No real credentials, files, database or external messages used."""
import asyncio
import json
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import Mock, MagicMock, AsyncMock, patch
import test_tenant_hot_orders as loaders
from tenant_chat_attachment_provider import drive_host, download, fetch, file_id, MAX_BYTES
from tenant_linear_policy import TenantLinearError

TENANT='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
FILE='bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
SESSION={'tenant_id':TENANT,'telegram_id':20}
ROW={'external_id':'msg','media_url':'https://drive-g.kommo.com/download/signed/a.png','raw':{}}
ITEM={'row':ROW,'connection':{'status':'connected','account_domain':'tenant.kommo.com','connected_at':'one','secrets':b'cipher'}}


class Provider(unittest.TestCase):
    def test_only_account_drive_origin(self):
        self.assertEqual(drive_host('https://drive-c.kommo.com'),'drive-c.kommo.com')
        for value in ('https://evil.com','http://drive-c.kommo.com','https://drive-c.kommo.com.evil',
                      'https://user:pass@drive-c.kommo.com','https://drive-c.kommo.com/path','https://drive-c.kommo.com:444'):
            with self.assertRaises(ValueError):drive_host(value)

    def test_fresh_private_link_metadata_only_has_bearer(self):
        link='https://drive-g.kommo.com/download/signed/new.png'
        row={**ROW,'raw':{'message':{'attachment':{'drive_uuid':FILE}}}}
        with patch('tenant_chat_attachment_provider.fetch',side_effect=[(json.dumps({'_links':{'download':{'href':link}}}).encode(),'application/json'),(b'picture','image/png')]) as call:
            self.assertEqual(download(row,'https://drive-g.kommo.com','company-token'),(b'picture','image/png'))
            self.assertEqual(call.call_args_list[0].kwargs['bearer'],'company-token')
            self.assertTrue(call.call_args_list[0].kwargs['metadata'])
            self.assertNotIn('bearer',call.call_args_list[1].kwargs)
            self.assertEqual(call.call_args_list[0].kwargs['deadline'],call.call_args_list[1].kwargs['deadline'])

    def test_external_or_unsafe_download_blocked_without_credentials(self):
        for url in ('https://evil.com/download/a','https://drive-g.kommo.com/admin','https://drive-g.kommo.com/download/a#fragment'):
            with patch('tenant_chat_attachment_provider.fetch') as call:
                with self.assertRaises(ValueError):download({**ROW,'media_url':url},'https://drive-g.kommo.com','token')
                call.assert_not_called()

    def test_uuid_not_provider_path(self):
        self.assertEqual(file_id({'raw':{'attachment':{'file_uuid':FILE}}}),FILE)
        with self.assertRaises(ValueError):file_id({'raw':{'file_uuid':'../../secrets'}})

    def test_nonmetadata_bearer_rejected_before_dns(self):
        with patch('tenant_chat_attachment_provider.public_addresses') as dns:
            with self.assertRaises(ValueError):fetch(ROW['media_url'],'drive-g.kommo.com',bearer='token')
            dns.assert_not_called()

    def response(self, status=200, mime='image/png', length=None, chunks=None):
        connection=Mock();response=connection.getresponse.return_value;response.status=status
        response.getheader.side_effect=lambda name,default=None:{'Content-Type':mime,'Content-Length':length}.get(name,default)
        response.read1.side_effect=chunks or [b'pic',b'']
        return connection

    def test_redirect_html_svg_and_declared_size_rejected(self):
        for options in ({'status':302},{'mime':'text/html'},{'mime':'image/svg+xml'},{'length':str(MAX_BYTES+1)}):
            connection=self.response(**options)
            with patch('tenant_chat_attachment_provider.public_addresses',return_value=['1.1.1.1']),patch('tenant_chat_attachment_provider.PinnedHTTPS',return_value=connection):
                with self.assertRaises(ValueError):fetch(ROW['media_url'],'drive-g.kommo.com')
            connection.close.assert_called_once()

    def test_stream_limit_and_truncated_body(self):
        for options in ({'chunks':[b'x'*(MAX_BYTES+1)]},{'length':'10','chunks':[b'pic',b'']}):
            connection=self.response(**options)
            with patch('tenant_chat_attachment_provider.public_addresses',return_value=['1.1.1.1']),patch('tenant_chat_attachment_provider.PinnedHTTPS',return_value=connection):
                with self.assertRaises(ValueError):fetch(ROW['media_url'],'drive-g.kommo.com')
            connection.close.assert_called_once()

    def test_direct_signed_download_no_authorization(self):
        connection=self.response()
        with patch('tenant_chat_attachment_provider.public_addresses',return_value=['1.1.1.1']),patch('tenant_chat_attachment_provider.PinnedHTTPS',return_value=connection):
            self.assertEqual(fetch(ROW['media_url'],'drive-g.kommo.com'),(b'pic','image/png'))
        self.assertNotIn('Authorization',connection.request.call_args.kwargs['headers'])


class Storage(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.policy=Mock();self.policy.can_access_deal.return_value=True
        self.module=loaders.load('tenant_chat_attachment_store',{'_connect':self.connect,'_read_tenant_workflow':Mock(return_value={}),
            '_fernet':Mock(),'TenantPolicy':lambda _:self.policy},{'tenant_platform','tenant_policy'})
        self.cur.fetchone.side_effect=[{**SESSION,'active':True,'tenant_status':'active'}, {'tenant_id':TENANT},ROW,ITEM['connection']]

    def test_resource_queries_have_company_and_message_identity(self):
        self.assertEqual(self.module.load(SESSION,7,'msg'),ITEM)
        calls=self.cur.execute.call_args_list
        self.assertEqual(calls[2].args[1],(TENANT,7,'msg'))
        self.assertTrue(all('tenant_id=%s::uuid' in call.args[0] for call in calls))

    def test_revocation_blocks_before_message_or_connection(self):
        self.policy.can_access_deal.return_value=False
        with self.assertRaises(TenantLinearError):self.module.load(SESSION,7,'msg')
        self.assertEqual(self.cur.execute.call_count,2)

    def test_inactive_member_cannot_read(self):
        self.cur.fetchone.side_effect=[{**SESSION,'active':False,'tenant_status':'active'}]
        with self.assertRaises(TenantLinearError):self.module.load(SESSION,7,'msg')
        self.assertEqual(self.cur.execute.call_count,1)

    def test_invalid_identity_does_not_open_database(self):
        for lead,message in [(0,'msg'),(2**63,'msg'),(7,''),(7,'x'*501)]:
            with self.assertRaises(ValueError):self.module.load(SESSION,lead,message)
        self.connect.assert_not_called()


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.load=Mock(return_value=ITEM);self.download=Mock(return_value=(b'picture','image/png'))
        self.provider=AsyncMock(return_value={'drive_url':'https://drive-g.kommo.com'})
        self.module=loaders.load('tenant_chat_attachment_api',{'load':self.load,'token':Mock(return_value='token'),
            'download':self.download,'web':SimpleNamespace(Response=lambda **kw:kw,json_response=lambda data,**kw:{'data':data,**kw})},
            {'aiohttp','tenant_chat_attachment_store','tenant_chat_attachment_provider'})
        self.query={'expected_tenant_id':TENANT,'expected_user_id':'20','lead_id':'7','message_id':'msg'}
        self.handle=self.module.handler(lambda _:SESSION,self.provider,Mock())

    async def test_success_rechecks_authority_and_no_store(self):
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['body'],b'picture');self.assertEqual(self.load.call_count,2)
        self.assertEqual(result['headers']['Cache-Control'],'no-store')
        self.assertEqual(result['headers']['X-Content-Type-Options'],'nosniff')
        self.assertEqual(result['headers']['Content-Disposition'],'inline; filename="attachment.png"; filename*=UTF-8\'\'attachment.png')

    async def test_filename_is_sanitized_and_keeps_extension(self):
        self.load.return_value={**ITEM,
            'row':{**ROW,'raw':{'message':{'attachment':{'fileName':'../../my report.pdf'}}}}}
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertIn('filename="my report.pdf"',result['headers']['Content-Disposition'])
        self.assertIn("filename*=UTF-8''my%20report.pdf",result['headers']['Content-Disposition'])

    async def test_download_forces_attachment_and_rechecks_case_after_download(self):
        guard=Mock();self.module.handler.__globals__['_check_case_attachment']=guard
        result=await self.handle(SimpleNamespace(query={**self.query,'download':'1','case_id':'case','case_message_id':'message','case_version':'1'}))
        self.assertEqual(guard.call_count,2)
        self.assertTrue(result['headers']['Content-Disposition'].startswith('attachment;'))

    async def test_case_revoked_after_download_returns_no_bytes(self):
        self.module.handler.__globals__['_check_case_attachment']=Mock(side_effect=[None,TenantLinearError('case changed',409)])
        result=await self.handle(SimpleNamespace(query={**self.query,'download':'1','case_id':'case','case_message_id':'message','case_version':'1'}))
        self.assertEqual(result['status'],409);self.assertNotIn('body',result)

    async def test_case_guard_binds_exact_message_and_route(self):
        selected={'message_id':'internal','route':{'channel_id':'channel','conversation_id':'dialog','lead_id':7}}
        item={'row':{'id':'internal'},'route':{'channel_id':'channel','conversation_id':'dialog'}}
        query={**self.query,'case_id':'case','case_message_id':'internal','case_version':'4'}
        with patch('tenant_knowledge_candidates_store.conversation_link',return_value=selected) as resolve:
            self.module._check_case_attachment(SESSION,item,query)
            resolve.assert_called_with(SESSION,'case','internal',expected_version=4)
            for changed in ({**item,'row':{'id':'other'}},{**item,'route':{'channel_id':'other','conversation_id':'dialog'}},{**item,'route':{'channel_id':'channel','conversation_id':'other'}}):
                with self.assertRaises(TenantLinearError):self.module._check_case_attachment(SESSION,changed,query)
            with self.assertRaises(TenantLinearError):self.module._check_case_attachment(SESSION,item,{**query,'lead_id':'8'})
            with self.assertRaises(TenantLinearError):self.module._check_case_attachment(SESSION,item,{**query,'case_version':''})

    async def test_cached_repeat_still_rechecks_authority(self):
        first=await self.handle(SimpleNamespace(query=self.query))
        second=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(first['body'],second['body'])
        self.assertEqual(self.download.call_count,1)
        self.assertEqual(self.load.call_count,4)

    async def test_cache_only_miss_never_starts_or_joins_provider_work(self):
        self.query['cache_only']='1'
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['status'],204)
        self.download.assert_not_called();self.provider.assert_not_called()
        self.assertFalse(self.module.MEDIA_INFLIGHT)
        self.assertEqual(self.load.call_count,1)

    async def test_cache_only_hit_still_rechecks_authority(self):
        await self.handle(SimpleNamespace(query=self.query))
        self.query['cache_only']='1'
        self.load.reset_mock()
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['body'],b'picture')
        self.assertEqual(self.load.call_count,2)
        self.download.assert_called_once()
        self.load.side_effect=TenantLinearError('Revoked',403)
        denied=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(denied['status'],403)
        self.assertNotIn('body',denied)

    async def test_audio_range_is_served_from_authorized_attachment(self):
        request=SimpleNamespace(query=self.query,headers={'Range':'bytes=1-3'})
        result=await self.handle(request)
        self.assertEqual(result['status'],206)
        self.assertEqual(result['body'],b'ict')
        self.assertEqual(result['headers']['Content-Range'],'bytes 1-3/7')
        self.assertEqual(result['headers']['Accept-Ranges'],'bytes')

    async def test_concurrent_same_attachment_download_is_deduplicated(self):
        started,release=threading.Event(),threading.Event()
        def slow_download(*_args):
            started.set();release.wait(2);return b'picture','image/png'
        self.download.side_effect=slow_download
        first=asyncio.create_task(self.handle(SimpleNamespace(query=self.query)))
        self.assertTrue(await asyncio.to_thread(started.wait,1))
        second=asyncio.create_task(self.handle(SimpleNamespace(query=self.query)))
        await asyncio.sleep(.01);release.set()
        a,b=await asyncio.gather(first,second)
        self.assertEqual(a['body'],b['body'])
        self.assertEqual(self.download.call_count,1)

    async def test_other_company_rejected_before_database_or_network(self):
        result=await self.handle(SimpleNamespace(query={**self.query,'expected_tenant_id':'other'}))
        self.assertEqual(result['status'],409);self.load.assert_not_called();self.provider.assert_not_called()

    async def test_changed_connection_never_returns_bytes(self):
        self.load.side_effect=[ITEM,{**ITEM,'connection':{}}]
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['status'],409);self.assertNotIn('body',result)

    async def test_revocation_after_download_never_returns_bytes(self):
        self.load.side_effect=[ITEM,TenantLinearError('denied',403)]
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['status'],403);self.assertNotIn('body',result)

    async def test_errors_hide_provider_secrets(self):
        self.download.side_effect=RuntimeError('token-secret https://signed-link')
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['status'],503);self.assertNotIn('token-secret',str(result))


if __name__=='__main__':unittest.main()
