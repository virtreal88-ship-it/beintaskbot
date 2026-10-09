import unittest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
import test_tenant_hot_orders as loaders
from kommo_phone_duplicates import normalize_phone, variants, search

NUMBER='+994551234567'


def contact(cid, phone, lead):
    return {'id':cid,'name':'Client','custom_fields_values':[{'field_code':'PHONE','values':[{'value':phone}]}],
            '_embedded':{'leads':[{'id':lead}]}}


class Phones(unittest.TestCase):
    def test_azerbaijan_equivalence(self):
        for value in ('+994551234567','994551234567','0551234567','551234567','+994 (55) 123-45-67','055 123 45 67','00994551234567'):
            self.assertEqual(normalize_phone(value),NUMBER)

    def test_full_length_no_suffix_collision(self):
        self.assertNotEqual(normalize_phone('+7994551234567'),NUMBER)
        for value in ('+99455','055','55','1234567','05512345678','+9940551234567','0551234567, 0557654321',None,55,'tel:+994551234567'):
            with self.assertRaises(ValueError):normalize_phone(value)

    def test_variants(self):
        self.assertEqual(variants(NUMBER),['551234567','0551234567',NUMBER,'994551234567'])
        self.assertEqual(variants('+15551234567'),['15551234567','+15551234567'])


class Search(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls=[]
        self.contacts=[contact(1,'0551234567',10),contact(2,'551234567',11),contact(3,'+7994551234567',12),contact(4,NUMBER,7)]
        self.leads=[{'id':10,'name':'First','pipeline_id':1,'status_id':2},{'id':11,'name':'Second','pipeline_id':1,'status_id':2}]
        async def read(path, params):
            self.calls.append((path,params))
            if path=='contacts':return {'_embedded':{'contacts':self.contacts}}
            if path=='leads':return {'_embedded':{'leads':self.leads}}
            return {'_embedded':{'pipelines':[{'id':1,'name':'Sales','_embedded':{'statuses':[{'id':2,'name':'New'}]}}]}}
        self.read=read

    async def test_exact_match_source_exclusion_dedup_and_permissions(self):
        result=await search('0551234567',7,self.read,lambda row:row['id']==10)
        self.assertEqual([row['id'] for row in result['deals']],[10])
        self.assertEqual(result['deals'][0]['pipeline_name'],'Sales')
        request=next(params for path,params in self.calls if path=='leads')
        self.assertEqual(request['filter[id][]'],[10,11]);self.assertNotIn(12,request['filter[id][]'])
        self.assertEqual(len(self.calls),6)

    async def test_empty_and_invalid_number(self):
        self.contacts=[]
        result=await search(NUMBER,7,self.read,lambda _:True)
        self.assertEqual(result['deals'],[]);self.assertTrue(all(path=='contacts' for path,_ in self.calls))
        read=AsyncMock()
        with self.assertRaises(ValueError):await search('55',7,read,lambda _:True)
        read.assert_not_called()

    async def test_no_phone_field_does_not_match_name_or_email(self):
        self.contacts=[{'id':1,'name':NUMBER,'custom_fields_values':[{'field_code':'EMAIL','values':[{'value':NUMBER}]}],'_embedded':{'leads':[{'id':10}]}}]
        result=await search(NUMBER,7,self.read,lambda _:True)
        self.assertEqual(result['deals'],[])

    async def test_page_limit_reported_not_silent_full_search(self):
        self.contacts=[contact(i,NUMBER,10) for i in range(100)]
        result=await search(NUMBER,7,self.read,lambda _:True)
        self.assertTrue(result['incomplete']);self.assertEqual(len([p for p,_ in self.calls if p=='contacts']),8)

    async def test_candidate_limit(self):
        self.contacts=[contact(i,NUMBER,100+i) for i in range(60)]
        result=await search(NUMBER,7,self.read,lambda _:True)
        self.assertTrue(result['incomplete'])
        query=next(params for path,params in self.calls if path=='leads')
        self.assertEqual(len(query['filter[id][]']),50)

    async def test_provider_failure_not_empty_success(self):
        with self.assertRaises(RuntimeError):await search(NUMBER,7,AsyncMock(side_effect=RuntimeError('provider')),lambda _:True)


class LegacyApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.authorize=Mock(return_value=({'id':7},None))
        self.search=AsyncMock(return_value={'phone':NUMBER,'deals':[{'id':10}], 'incomplete':False})
        self.http=Mock();self.can_view=Mock(return_value=True)
        self.module=loaders.load('kommo_phone_duplicates_api',{'search':self.search,
            'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},{'aiohttp','kommo_phone_duplicates'})
        self.handle=self.module.handler(lambda _:20,self.authorize,self.can_view,self.http,'https://company.kommo.com',{},Mock())
        self.request=SimpleNamespace(query={'lead_id':'7','phone':NUMBER})

    async def test_requires_source_access(self):
        self.authorize.return_value=(None,{'status':403})
        result=await self.handle(self.request)
        self.assertEqual(result['status'],403);self.search.assert_not_called();self.http.get.assert_not_called()

    async def test_success_has_recheck_and_no_store(self):
        result=await self.handle(self.request)
        self.assertTrue(result['data']['success']);self.assertEqual(self.authorize.call_count,2)
        self.assertEqual(result['headers']['Cache-Control'],'no-store');self.http.post.assert_not_called()

    async def test_revoke_after_search(self):
        self.authorize.side_effect=[({'id':7},None),(None,{'status':403})]
        self.assertEqual((await self.handle(self.request))['status'],403)

    async def test_errors_not_disclosed_or_reported_as_no_matches(self):
        self.search.side_effect=RuntimeError('secret')
        result=await self.handle(self.request)
        self.assertEqual(result['status'],503);self.assertNotIn('secret',str(result))


class TenantApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.member={'tenant_id':'company-A','telegram_id':20}
        self.identify=Mock(return_value=self.member)
        self.policy=Mock();self.policy.can_access_deal.return_value=True
        self.provider=AsyncMock(return_value={'id':7,'pipeline_id':1,'status_id':2})
        self.search=AsyncMock(return_value={'phone':NUMBER,'deals':[{'id':10,'pipeline_id':1,'status_id':2}],'incomplete':False})
        self.module=loaders.load('kommo_phone_duplicates_api',{'search':self.search,'TenantPolicy':lambda _:self.policy,
            'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},
            {'aiohttp','kommo_phone_duplicates','tenant_policy'})
        self.handle=self.module.tenant_handler(self.identify,self.provider,Mock())
        self.query={'lead_id':'7','phone':NUMBER,'expected_tenant_id':'company-A','expected_user_id':'20'}

    async def test_cross_company_rejected_before_provider(self):
        result=await self.handle(SimpleNamespace(query={**self.query,'expected_tenant_id':'company-B'}))
        self.assertEqual(result['status'],409);self.provider.assert_not_called();self.search.assert_not_called()

    async def test_resource_policy_and_read_bound_to_session(self):
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertTrue(result['data']['success'])
        self.provider.assert_awaited_once_with('company-A','GET','leads/7',params={})
        allowed=self.search.call_args.args[3]
        allowed({'id':10,'pipeline_id':1,'status_id':2})
        self.assertEqual(self.policy.can_access_deal.call_args.args[0]['tenant_id'],'company-A')

    async def test_source_denied_before_contact_search(self):
        self.policy.can_access_deal.return_value=False
        self.assertEqual((await self.handle(SimpleNamespace(query=self.query)))['status'],403)
        self.search.assert_not_called()

    async def test_identity_change_after_search_blocks_output(self):
        self.identify.side_effect=[self.member,{**self.member,'tenant_id':'company-B'}]
        result=await self.handle(SimpleNamespace(query=self.query))
        self.assertEqual(result['status'],409);self.assertNotIn('deals',result['data'])


if __name__=='__main__':unittest.main()
