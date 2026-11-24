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

    async def test_transport_rejection_does_not_parse_json(self):
        for change in ({'method': 'GET'}, {'content_type': 'text/plain'},
                       {'headers': {}}, {'headers': {'Origin': 'https://crm.pro.az.evil'}}):
            request = SimpleNamespace(method='POST', content_type='application/json',
                headers={'Origin': 'https://crm.pro.az'}, json=AsyncMock())
            for key, value in change.items():
                setattr(request, key, value)
            result = await self.handle(request)
            self.assertEqual(result['status'], 403)
            self.assertEqual(result['data']['error'], 'Sorğunun mənbəyi düzgün deyil.')
            request.json.assert_not_awaited()
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


    async def test_status_checks_own_request_without_invoking_send(self):
        saved = Mock(return_value={'state': 'accepted'})
        with unittest.mock.patch('tenant_chat_send_store.read_send_status', saved):
            status = self.module.send_status_handler(lambda request: SESSION, Mock())
            query = {'expected_tenant_id': TENANT, 'expected_user_id': '20',
                     'lead_id': '7', 'request_id': INPUT['request_id']}
            result = await status(SimpleNamespace(query=query))
            self.assertEqual(result['data'], {'success': True, 'state': 'accepted'})
            self.assertEqual(result['headers']['Cache-Control'], 'no-store')
            saved.assert_called_once_with(SESSION, '7', INPUT['request_id'])
            forbidden = await status(SimpleNamespace(query={**query, 'expected_user_id': '21'}))
            self.assertEqual(forbidden['status'], 409)
            saved.assert_called_once()
        self.run.assert_not_awaited()

    async def test_close_unsubmitted_requires_same_origin_and_identity_without_sending(self):
        closed = Mock(return_value={'state': 'blocked'})
        with unittest.mock.patch('tenant_chat_send_store.close_unsubmitted_send', closed):
            handle = self.module.close_unsubmitted_handler(lambda request: SESSION, 'https://crm.pro.az', Mock())
            body = {'lead_id': 7, 'request_id': INPUT['request_id'],
                    'expected_tenant_id': TENANT, 'expected_user_id': 20}
            def req(origin, data):
                return SimpleNamespace(method='POST', content_type='application/json', headers={'Origin': origin},
                                       json=AsyncMock(return_value=data))
            bad_origin = req('https://other.invalid', body)
            self.assertEqual((await handle(bad_origin))['status'], 403)
            bad_origin.json.assert_not_awaited()
            for mismatch in ({'expected_user_id': 21}, {'expected_tenant_id': 'other'}):
                self.assertEqual((await handle(req('https://crm.pro.az', {**body, **mismatch})))['status'], 409)
            closed.assert_not_called()
            ok = await handle(req('https://crm.pro.az', body))
            self.assertEqual(ok['data'], {'success': True, 'state': 'blocked'})
            self.assertEqual(ok['headers']['Cache-Control'], 'no-store')
            closed.assert_called_once_with(SESSION, 7, INPUT['request_id'])
        self.run.assert_not_awaited()

    async def test_unconfirmed_provider_status_does_not_accept_quote_receipt(self):
        item = {'key': (TENANT, 20, INPUT['request_id']), 'credentials': {}, 'message_id': None,
                'quote': 'PROVIDER_QUOTE', 'receipt_state': 'unknown', 'connection': ['connect', 'source']}
        with (unittest.mock.patch('tenant_chat_send_store.read_quoted_receipt', return_value=item),
              unittest.mock.patch('tenant_chat_send_store.accept_quoted_lookup') as accept,
              unittest.mock.patch('tenant_connect_transport.quoted_delivery_status', return_value={
                  'message_id': 'reserved-at-provider', 'provider_accepted': False, 'delivery_state': 'unknown'})):
            receipt = self.module.receipt_handler(lambda request: SESSION, Mock())
            result = await receipt(SimpleNamespace(query={'expected_tenant_id': TENANT, 'expected_user_id': '20',
                                                           'lead_id': '7', 'request_id': INPUT['request_id']}))
        self.assertEqual(result['data']['state'], 'unknown')
        accept.assert_not_called()


if __name__ == '__main__': unittest.main()
