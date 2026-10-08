"""Task endpoint uses a tenant session, Origin and explicit expected identity."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import test_tenant_hot_orders as loaders
from test_tenant_linear_runtime import PERSON
from tenant_linear_policy import TenantLinearError


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.listing=Mock(return_value={'issues':[]});self.choices=Mock(return_value={});self.execute=Mock(return_value={'issue_id':'id'})
        self.module=loaders.load('tenant_linear_tasks_api',{'listing':self.listing,'editor_choices':self.choices,'execute':self.execute,
            'web':SimpleNamespace(json_response=lambda body,**kw:{'body':body,**kw})},
            {'tenant_linear_tasks','tenant_linear_commands','aiohttp'})
        self.logger=Mock();self.handle=self.module.tasks_handler(lambda r:PERSON,'https://crm.pro.az',self.logger)

    async def request(self,data=None,method='POST',origin='https://crm.pro.az',query=None):
        async def read():return data
        return await self.handle(SimpleNamespace(method=method,content_type='application/json',headers={'Origin':origin},json=read,
            rel_url=SimpleNamespace(query=query or {})))

    async def test_wrong_origin_tenant_and_identity_fail_before_write(self):
        self.assertEqual((await self.request({},origin='https://foreign.invalid'))['status'],403)
        for data in ({'expected_tenant_id':'foreign','expected_user_id':20},
            {'expected_tenant_id':PERSON['tenant_id'],'expected_user_id':999}):
            self.assertEqual((await self.request(data))['status'],409)
        self.execute.assert_not_called()

    async def test_server_identity_not_client_role(self):
        data={'action':'edit','expected_tenant_id':PERSON['tenant_id'],'expected_user_id':20,'role':'owner'}
        result=await self.request(data);self.execute.assert_called_once_with(PERSON,data)
        self.assertEqual(result['headers']['Cache-Control'],'no-store')

    async def test_provider_permission_errors_preserved_but_generic_exceptions_hidden(self):
        self.listing.side_effect=TenantLinearError('Denied',403)
        self.assertEqual((await self.request(method='GET'))['status'],403)
        self.listing.side_effect=RuntimeError('PRIVATE KEY')
        result=await self.request(method='GET');self.assertEqual(result['status'],503)
        self.assertNotIn('PRIVATE',str(result));self.logger.error.assert_called_once_with('Tenant Linear task API failed')

    async def test_reads_are_explicit_and_paginated(self):
        await self.request(method='GET',query={'after':'cursor','search':'customer'})
        self.listing.assert_called_once_with(PERSON,after='cursor',search='customer')
        await self.request(method='GET',query={'catalog':'1'});self.choices.assert_called_once_with(PERSON)

    async def test_unsigned_access_fails(self):
        self.handle=self.module.tasks_handler(lambda r:None,'https://crm.pro.az',self.logger)
        self.assertEqual((await self.request(method='GET'))['status'],403);self.listing.assert_not_called()
