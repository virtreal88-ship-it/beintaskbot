from types import SimpleNamespace
import unittest
from unittest.mock import Mock,AsyncMock
import test_tenant_hot_orders as loaders
from test_tenant_news import SESSION
from test_tenant_news_telegram import verified
from tenant_linear_policy import TenantLinearError


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.read=Mock(return_value={});self.save=Mock(return_value={});self.operate=Mock(return_value={});self.verify=AsyncMock(return_value=verified())
        self.bot=SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='companybot')))
        self.service=loaders.load('tenant_news_telegram_api',{'read':self.read,'save':self.save,'operate':self.operate,'verify':self.verify,
            'web':SimpleNamespace(json_response=lambda body,**kw:{'body':body,**kw})},
            {'aiohttp','tenant_news_telegram_settings','tenant_news_telegram_queue','tenant_news_telegram_policy'})
        # Pure validators excluded with the provider; supply them explicitly.
        from tenant_news_telegram_policy import settings,address
        self.service.handler.__globals__.update(settings=settings,address=address)
        self.logger=Mock();self.handle=self.service.handler(lambda r:SESSION,'https://crm.pro.az',lambda:self.bot,self.logger,configuration=True)

    async def request(self,data=None,method='POST',origin='https://crm.pro.az'):
        async def body():return data
        return await self.handle(SimpleNamespace(method=method,content_type='application/json',headers={'Origin':origin},json=body))

    async def test_identity_and_origin_checked_before_telegram(self):
        self.assertEqual((await self.request({},origin='https://evil.invalid'))['status'],403)
        self.assertEqual((await self.request({'expected_tenant_id':'other','expected_user_id':20}))['status'],409)
        self.verify.assert_not_called();self.save.assert_not_called()

    async def test_revoked_owner_rejected_before_readonly_bot_call(self):
        self.read.side_effect=TenantLinearError('Denied',403)
        result=await self.request({'expected_tenant_id':SESSION['tenant_id'],'expected_user_id':20})
        self.assertEqual(result['status'],403);self.verify.assert_not_called()

    async def test_canonical_verified_values_passed_to_save(self):
        data={'expected_tenant_id':SESSION['tenant_id'],'expected_user_id':20,'enabled':True,'channel':'@channel'}
        await self.request(data);self.verify.assert_awaited_once_with(self.bot,'@channel',20)
        self.save.assert_called_once_with(SESSION,data,verified())

    async def test_pause_does_not_call_external_provider(self):
        data={'expected_tenant_id':SESSION['tenant_id'],'expected_user_id':20,'enabled':False}
        await self.request(data);self.verify.assert_not_called();self.save.assert_called_once_with(SESSION,data,None)


if __name__=='__main__':unittest.main()
