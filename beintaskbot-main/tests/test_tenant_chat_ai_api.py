from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock, MagicMock
import test_tenant_hot_orders as loaders
from test_tenant_chat_ai import SESSION, COMMAND, CONFIG, CONNECTION, TENANT
from tenant_linear_policy import TenantLinearError


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.read=Mock(return_value={});self.save=Mock(return_value={});self.run=AsyncMock(return_value={'text':'draft'})
        self.service=loaders.load('tenant_chat_ai_api',{'read':self.read,'save':self.save,'run':self.run,
            'web':SimpleNamespace(json_response=lambda body,**kwargs:{'body':body,**kwargs})},
            {'aiohttp','tenant_chat_ai_settings','tenant_chat_ai_service'})
        self.logger=Mock();self.handle=self.service.handler(lambda request:SESSION,'https://crm.pro.az',self.logger)

    async def request(self,changes=None,origin='https://crm.pro.az',method='POST'):
        data={**COMMAND,'expected_tenant_id':TENANT,'expected_user_id':20,**(changes or {})}
        return await self.handle(SimpleNamespace(method=method,content_type='application/json',headers={'Origin':origin},json=AsyncMock(return_value=data)))

    async def test_foreign_origin_and_company_stop_before_paid_calls(self):
        self.assertEqual((await self.request(origin='https://other.invalid'))['status'],403)
        self.assertEqual((await self.request({'expected_tenant_id':'other'}))['status'],409)
        self.assertEqual((await self.request({'expected_user_id':21}))['status'],409)
        self.run.assert_not_awaited()

    async def test_success_is_draft_only_no_store_header(self):
        result=await self.request();self.assertEqual(result['body']['text'],'draft')
        self.assertEqual(result['headers']['Cache-Control'],'no-store');self.run.assert_awaited_once()

    async def test_owner_settings_path_not_chat_command(self):
        self.handle=self.service.handler(lambda request:SESSION,'https://crm.pro.az',self.logger,settings=True)
        await self.request(method='GET');self.read.assert_called_once_with(SESSION);self.run.assert_not_awaited()
        await self.request();self.save.assert_called_once();self.run.assert_not_awaited()

    async def test_error_does_not_expose_provider_or_credentials(self):
        self.run.side_effect=RuntimeError('sk-SECRET https://private-media')
        result=await self.request();self.assertEqual(result['status'],503);self.assertNotIn('SECRET',str(result))

    async def test_inflight_receipt_preserves_browser_request(self):
        error=TenantLinearError('Pending',409);error.keep_request=True;self.run.side_effect=error
        self.assertTrue((await self.request())['body']['keep_request'])


class Settings(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value;self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.crypto=Mock();self.crypto.encrypt.return_value=b'encrypted'
        self.owner=Mock(return_value=(TENANT,20))
        self.service=loaders.load('tenant_chat_ai_settings',{'_connect':self.connect,'_fernet':lambda:self.crypto,'authorize':self.owner,'ensure_schema':Mock()}, {'tenant_platform','tenant_news_ai_settings','tenant_chat_ai_schema'})
        self.service.save.__globals__['authorize']=self.owner
        self.data={**CONFIG,'api_key':'sk-'+'x'*30,'expected_updated_at':'version-1'}
        self.cur.fetchone.side_effect=[CONNECTION,CONNECTION]

    def test_key_bound_to_company_and_chat_provider_only(self):
        result=self.service.save(SESSION,self.data)
        encrypted_input=self.crypto.encrypt.call_args.args[0].decode()
        self.assertIn(TENANT,encrypted_input);self.assertIn('chat_ai',encrypted_input)
        self.assertNotIn('api_key',result);self.assertNotIn('secrets',result);self.conn.commit.assert_called_once()
        sql=' '.join(call.args[0] for call in self.cur.execute.call_args_list)
        self.assertNotIn('news_ai',sql);self.assertNotIn('OPENAI_API_KEY',sql)

    def test_owner_revocation_and_version_conflict_before_encryption(self):
        self.owner.side_effect=TenantLinearError('Denied',403)
        with self.assertRaises(TenantLinearError):self.service.save(SESSION,self.data)
        self.crypto.encrypt.assert_not_called()
        self.owner.side_effect=None;self.cur.fetchone.side_effect=[CONNECTION]
        with self.assertRaises(TenantLinearError):self.service.save(SESSION,{**self.data,'expected_updated_at':'old'})
        self.crypto.encrypt.assert_not_called()

    def test_removing_active_key_must_disable_ai(self):
        self.cur.fetchone.side_effect=[CONNECTION]
        with self.assertRaises(TenantLinearError):self.service.save(SESSION,{**self.data,'remove_key':True})
        self.conn.commit.assert_not_called()


class Provider(unittest.TestCase):
    def setUp(self):
        from tenant_chat_ai_provider import generate, transcribe
        self.generate,self.transcribe=generate,transcribe

    def test_direct_response_no_tools_no_redirects_and_no_storage(self):
        from unittest.mock import patch
        response=Mock(status_code=200);response.json.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'hello'}]}]}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
            self.assertEqual(self.generate('company-key','text-model','reply',[{'speaker':'CLIENT','text':'source'}],''),'hello')
        self.assertEqual(post.call_args.args[0],'https://api.openai.com/v1/responses')
        body=post.call_args.kwargs['json'];self.assertIs(body['store'],False);self.assertNotIn('tools',body)
        self.assertIs(post.call_args.kwargs['allow_redirects'],False)

    def test_agent_generation_accepts_tenant_instructions_and_verified_images(self):
        from unittest.mock import patch
        response=Mock(status_code=200);response.json.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'draft'}]}]}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
            result=self.generate('company-key','text-model','reply',[{'speaker':'CLIENT','text':'source'}],'',
                instructions='Tenant instructions',extra_context={'language':'az'},
                images=[{'content':b'png-bytes','content_type':'image/png'}])
        self.assertEqual(result,'draft')
        body=post.call_args.kwargs['json'];self.assertEqual(body['instructions'],'Tenant instructions')
        content=body['input'][0]['content'];self.assertIn('extra_context',content[0]['text'])
        self.assertEqual(content[1]['type'],'input_image')
        self.assertTrue(content[1]['image_url'].startswith('data:image/png;base64,'))

    def test_agent_generation_returns_only_provider_reported_usage_when_requested(self):
        from unittest.mock import patch
        response=Mock(status_code=200);response.json.return_value={
            'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'draft'}]}],
            'usage':{'input_tokens':12,'output_tokens':7,'total_tokens':19}}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response):
            result, usage=self.generate('company-key','text-model','reply',[],'',with_usage=True)
        self.assertEqual(result,'draft')
        self.assertEqual(usage,{'input_tokens':12,'output_tokens':7,'total_tokens':19})

    def test_provider_failure_keeps_response_private_but_records_safe_category(self):
        from unittest.mock import patch
        from tenant_chat_ai_provider import ChatAIProviderError
        response=Mock(status_code=403);response.text='private provider response'
        response.json.return_value={'error':{'code':'invalid_api_key','type':'authentication_error','message':'private provider response'}}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response):
            with self.assertRaises(ChatAIProviderError) as caught:
                self.generate('company-key','text-model','reply',[],'')
        self.assertEqual(caught.exception.status_code,403)
        self.assertEqual(caught.exception.category,'invalid_credentials')
        self.assertNotIn('private provider response',str(caught.exception))

    def test_provider_error_body_cannot_inject_diagnostic_category(self):
        from unittest.mock import patch
        from tenant_chat_ai_provider import ChatAIProviderError
        response=Mock(status_code=403)
        response.json.return_value={'error':{'code':'customer-secret-value','type':'permission_denied','message':'private'}}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response):
            with self.assertRaises(ChatAIProviderError) as caught:
                self.generate('company-key','text-model','reply',[],'')
        self.assertEqual(caught.exception.category,'project_access')
        self.assertNotIn('customer-secret-value',str(caught.exception))
        self.assertNotIn('private',str(caught.exception))

    def test_unavailable_model_error_is_classified_without_fallback(self):
        from unittest.mock import patch
        from tenant_chat_ai_provider import ChatAIProviderError
        response=Mock(status_code=404)
        response.json.return_value={'error':{'code':'model_not_found','message':'private model/account detail'}}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
            with self.assertRaises(ChatAIProviderError) as caught:
                self.generate('company-key','selected-copilot-model','reply',[],'')
        self.assertEqual(caught.exception.category,'model_access')
        self.assertEqual(post.call_args.kwargs['json']['model'],'selected-copilot-model')
        self.assertNotIn('private model/account detail',str(caught.exception))

    def test_audio_uploaded_as_bytes_without_remote_media_url(self):
        from unittest.mock import patch
        response=Mock(status_code=200);response.json.return_value={'text':'transcript'}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
            self.assertEqual(self.transcribe('key','audio-model',b'audio','ogg','audio/ogg'),'transcript')
        self.assertEqual(post.call_args.args[0],'https://api.openai.com/v1/audio/transcriptions')
        self.assertEqual(post.call_args.kwargs['files']['file'][1],b'audio')
        self.assertNotIn('media_url',str(post.call_args.kwargs))


if __name__=='__main__':unittest.main()
