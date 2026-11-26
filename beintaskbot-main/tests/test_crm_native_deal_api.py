"""Native deal API auth and tenant routing tests."""
import asyncio,json,unittest
from multidict import MultiDict
from types import SimpleNamespace
from unittest import mock
import tenant_crm_native_deal_api as api


class ApiTests(unittest.TestCase):
    def test_forbidden_and_tenant_scoped_read(self):
        handler=api.handler(lambda _:None,'https://crm.test',mock.MagicMock())
        denied=asyncio.run(handler(SimpleNamespace(method='GET',match_info={},query={})))
        self.assertEqual(denied.status,403)
        handler=api.handler(lambda _:{'tenant_id':'tenant-a','telegram_id':20,'role':'manager','active':True,
            'permissions':['deals:view'],'modules':{'deals':True}},'https://crm.test',mock.MagicMock())
        with mock.patch.object(api,'list_deals',return_value=[]) as listing:
            response=asyncio.run(handler(SimpleNamespace(method='GET',match_info={},query=MultiDict(search='abc'))))
        self.assertEqual(response.status,200)
        listing.assert_called_once_with('tenant-a','abc',51,actor_id=20,offset=0)

    def test_list_pagination_under_the_same_tenant_and_search(self):
        handler = api.handler(lambda _:{'tenant_id':'tenant-a','telegram_id':20,'role':'owner',
            'active':True},'https://crm.test',mock.MagicMock())
        def request(query):
            return asyncio.run(handler(SimpleNamespace(method='GET',match_info={},query=MultiDict(query))))
        rows = [{'id': index} for index in range(50, 101)]
        with mock.patch.object(api,'list_deals',return_value=rows) as listing:
            response = request({'search':'abc','offset':'50'})
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.body), {'success':True,'deals':rows[:50],'next_offset':100})
            listing.assert_called_once_with('tenant-a','abc',51,actor_id=20,offset=50)
            listing.reset_mock()
            for query in ({'offset':'-1'}, {'offset':'9999999999'}, [('offset','50'),('offset','100')]):
                self.assertEqual(request(query).status, 400)
            listing.assert_not_called()

    def test_invalid_id_never_reaches_store(self):
        handler=api.handler(lambda _:{'tenant_id':'tenant-a','role':'owner'},'https://crm.test',mock.MagicMock())
        with mock.patch.object(api,'get_deal') as getter:
            response=asyncio.run(handler(SimpleNamespace(method='GET',match_info={'id':'7'},query={})))
        self.assertEqual(response.status,400); getter.assert_not_called()

    def test_post_requires_exact_origin(self):
        handler=api.handler(lambda _:{'tenant_id':'tenant-a','role':'owner'},'https://crm.test',mock.MagicMock())
        request=SimpleNamespace(method='POST',match_info={},query={},content_type='application/json',headers={'Origin':'https://evil.test'})
        response=asyncio.run(handler(request))
        self.assertEqual(response.status,403)


if __name__=='__main__': unittest.main()
