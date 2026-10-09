from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import test_tenant_hot_orders as loaders
from test_tenant_chat_send import SESSION, INPUT, TENANT


class Api(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.run = AsyncMock(return_value={'state': 'accepted', 'message_id': 'id'})
        self.module = loaders.load('tenant_chat_send_api', {'run': self.run,
            'web': SimpleNamespace(json_response=lambda data, **kw: {'data': data, **kw})}, {'aiohttp', 'tenant_chat_send_service'})
        self.handle = self.module.handler(lambda request: SESSION, 'https://crm.pro.az', Mock(), Mock(), Mock())

    async def invoke(self, data=None, origin='https://crm.pro.az'):
        return await self.handle(SimpleNamespace(method='POST', content_type='application/json',
            headers={'Origin': origin}, json=AsyncMock(return_value=data if data is not None else {
                **INPUT, 'expected_tenant_id': TENANT, 'expected_user_id': 20})))

    async def test_wrong_origin_stops_before_provider(self):
        self.assertEqual((await self.invoke(origin='https://other.invalid'))['status'], 403)
        self.run.assert_not_awaited()

    async def test_company_and_user_mismatch_stop_before_provider(self):
        for data in ({**INPUT, 'expected_tenant_id': 'other', 'expected_user_id': 20}, {**INPUT, 'expected_tenant_id': TENANT, 'expected_user_id': 21}):
            self.assertEqual((await self.invoke(data))['status'], 409)
        self.run.assert_not_awaited()

    async def test_bad_json_shape_stops_before_provider(self):
        self.assertEqual((await self.invoke(['bad']))['status'], 400)
        self.run.assert_not_awaited()

    async def test_success_and_unknown_keep_receipt(self):
        result = await self.invoke()
        self.assertEqual(result['data']['state'], 'accepted')
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')
        self.run.side_effect = RuntimeError('db')
        result = await self.invoke()
        self.assertEqual(result['status'], 503)
        self.assertTrue(result['data']['keep_request'])


if __name__ == '__main__': unittest.main()
