"""Native task API access and assignment checks."""
import asyncio,unittest
from types import SimpleNamespace
from unittest import mock
import tenant_crm_native_task_api as api

class ApiTests(unittest.TestCase):
    def request(self,method='GET',body=None,id=''):
        async def json():return body or {}
        return SimpleNamespace(method=method,match_info={'id':id} if id else {},query={},content_type='application/json',headers={'Origin':'https://crm.test'},json=json)
    def test_requires_task_permission(self):
        h=api.handler(lambda _:None,'https://crm.test',mock.MagicMock())
        self.assertEqual(asyncio.run(h(self.request())).status,403)
    def test_worker_cannot_assign_others_or_bypass_approval(self):
        profile={'tenant_id':'t','telegram_id':1,'role':'worker','active':True,'modules':{'tasks':True},'permissions':['tasks']}
        h=api.handler(lambda _:profile,'https://crm.test',mock.MagicMock())
        r=asyncio.run(h(self.request('POST',{'deal_id':'x','text':'Do','responsible_id':2,'request_id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'})))
        self.assertEqual(r.status,403)
    def test_privileged_native_create_is_idempotency_keyed(self):
        profile={'tenant_id':'t','telegram_id':1,'role':'owner'}
        h=api.handler(lambda _:profile,'https://crm.test',mock.MagicMock())
        with mock.patch.object(api,'create_task',return_value={'id':'task'}) as create:
            r=asyncio.run(h(self.request('POST',{'deal_id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','text':'Do','responsible_id':1,'request_id':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'})))
        self.assertEqual(r.status,200);self.assertEqual(create.call_args.kwargs['idempotency_key'],'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')

if __name__=='__main__':unittest.main()
