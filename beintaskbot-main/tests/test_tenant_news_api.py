from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import test_tenant_hot_orders as loaders
from test_tenant_news import SESSION


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.listing=Mock(return_value={'news':[]});self.command=Mock(return_value={});self.published=Mock(return_value={'news':[]})
        self.read=Mock(return_value={});self.save=Mock(return_value={})
        self.service=loaders.load('tenant_news_api',{'listing':self.listing,'command':self.command,'published':self.published,
            'read_ai':self.read,'save_ai':self.save,'web':SimpleNamespace(json_response=lambda body,**kw:{'body':body,**kw})},
            {'tenant_news','tenant_news_ai_settings','aiohttp'})
        self.logger=Mock();self.handle=self.service.news_handler(lambda r:SESSION,'https://crm.pro.az',self.logger)

    async def request(self,data=None,method='POST',origin='https://crm.pro.az'):
        async def read():return data
        return await self.handle(SimpleNamespace(method=method,content_type='application/json',headers={'Origin':origin},json=read,
            rel_url=SimpleNamespace(query={}),match_info={'company':SESSION['tenant_id']}))

    async def test_origin_and_company_checked_before_command(self):
        self.assertEqual((await self.request({},origin='https://evil.invalid'))['status'],403)
        self.assertEqual((await self.request({'expected_tenant_id':'other','expected_user_id':20}))['status'],409)
        self.command.assert_not_called()

    async def test_public_endpoint_does_not_read_session_or_pending_queue(self):
        identify=Mock(side_effect=AssertionError('Session must not be consulted'))
        self.handle=self.service.news_handler(identify,'https://crm.pro.az',self.logger,public=True)
        await self.request(method='GET');self.published.assert_called_once_with(SESSION['tenant_id'],after=None)
        self.listing.assert_not_called();identify.assert_not_called()

    async def test_publication_is_not_an_ai_settings_command(self):
        self.handle=self.service.news_handler(lambda r:SESSION,'https://crm.pro.az',self.logger,ai=True)
        data={'expected_tenant_id':SESSION['tenant_id'],'expected_user_id':20}
        await self.request(data);self.save.assert_called_once_with(SESSION,data);self.command.assert_not_called()

    async def test_sensitive_exceptions_hidden(self):
        self.listing.side_effect=RuntimeError('PRIVATE KEY')
        result=await self.request(method='GET');self.assertEqual(result['status'],503);self.assertNotIn('PRIVATE',str(result))
        self.assertNotIn('PRIVATE',str(self.logger.mock_calls))


if __name__=='__main__':unittest.main()
