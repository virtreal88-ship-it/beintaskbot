from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from test_tenant_hot_orders import load
from test_tenant_chat_send import TENANT, SESSION
from tenant_linear_policy import TenantLinearError


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.run=AsyncMock(return_value={'imported':250,'has_more':True,'next_cursor':'cursor'})
        self.member=Mock(return_value=SESSION)
        self.module=load('tenant_chat_import_api',{'run':self.run,'authorize':Mock(),
            'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},
            {'aiohttp','tenant_chat_import_service','tenant_chat_import_store'})
        self.handle=self.module.handler(self.member,'https://crm.pro.az',Mock(),Mock(),Mock(),Mock(),Mock(),Mock())
        self.data={'lead_id':7,'expected_tenant_id':TENANT,'expected_user_id':'20'}
        self.request=SimpleNamespace(method='POST',content_type='application/json',
            headers={'Origin':'https://crm.pro.az'},json=AsyncMock(return_value=self.data))

    async def test_session_origin_and_method_required_before_import(self):
        for attr,value in [('method','GET'),('content_type','text/plain'),('headers',{})]:
            old=getattr(self.request,attr);setattr(self.request,attr,value)
            self.assertEqual((await self.handle(self.request))['status'],403)
            setattr(self.request,attr,old)
        self.member.return_value=None
        self.assertEqual((await self.handle(self.request))['status'],403)
        self.run.assert_not_awaited()

    async def test_company_user_and_input_rejected_before_import(self):
        for data,status in [({**self.data,'expected_tenant_id':'other'},409),
                           ({**self.data,'expected_user_id':'21'},409),
                           ({**self.data,'lead_id':True},400),({**self.data,'lead_id':0},400),
                           ({**self.data,'lead_id':'7'},400),({**self.data,'cursor':'a'*2049},400),([],400)]:
            self.request.json.return_value=data
            self.assertEqual((await self.handle(self.request))['status'],status)
        self.run.assert_not_awaited()

    async def test_success_is_no_store_and_scoped(self):
        result=await self.handle(self.request)
        self.assertTrue(result['data']['success']);self.assertEqual(result['headers']['Cache-Control'],'no-store')
        self.assertEqual(self.run.call_args.args[:3],(SESSION,7,''))

    async def test_failed_import_not_empty_success_or_private_error(self):
        for error,status in [(TenantLinearError('denied',403),403),(ValueError('bad'),400),
                             (RuntimeError('PRIVATE_PROVIDER_TOKEN'),503)]:
            self.run.side_effect=error
            result=await self.handle(self.request)
            self.assertEqual(result['status'],status);self.assertFalse(result['data']['success'])
            self.assertNotIn('PRIVATE',str(result))


if __name__=='__main__':unittest.main()
