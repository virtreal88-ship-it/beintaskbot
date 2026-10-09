"""Deal completion is explicit, isolated and checkpointed before a single PATCH."""
import copy
import unittest
import uuid
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from types import SimpleNamespace
from test_tenant_hot_orders import load
from test_tenant_crm_notice import member
from tenant_linear_policy import TenantLinearError


class MemoryStore:
    rows={}
    def __init__(self,tenant,actor,request,lead,fingerprint):
        self.key=(tenant,actor,request);self.fingerprint=fingerprint
        old=self.rows.get(self.key)
        if old and old[0]!=fingerprint:raise TenantLinearError('different',409)
        self.state=copy.deepcopy(old[1] if old else {})
    def save(self,state):
        self.state={**self.state,**copy.deepcopy(state)};self.rows[self.key]=(self.fingerprint,copy.deepcopy(self.state))
    def close(self):pass


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MemoryStore.rows={};self.p=member();self.admin=member('owner',1);self.live={'id':7,'pipeline_id':10,'status_id':100,'name':'Deal'}
        self.data={'request_id':str(uuid.uuid4()),'lead_id':7,'result_text':''};self.fail=False
        self.members={20:self.p,1:self.admin};self.cache=Mock();self.audit=Mock()
        self.module=load('tenant_deal_completion',{'member':Mock(side_effect=lambda t,a:self.members.get(a)),
            'get_crm_deal':Mock(return_value={'tenant_id':'company-a','kommo_lead_id':7,'pipeline_id':10,'status_id':100,'contact_name':'Client','phone':'123'}),
            'upsert_crm_deals':self.cache,'append_audit_event':self.audit,'authorize':Mock(return_value='connection'),
            'DealCommandStore':MemoryStore,'command':Mock(),'pending':Mock()},
            {'tenant_platform','tenant_chat_import_store','tenant_deal_completion_store'})
        self.request=AsyncMock(side_effect=self.provider)
    async def provider(self,tenant,method,path,**kwargs):
        if method=='GET' and path=='leads/7':return dict(self.live)
        if method=='GET' and path.endswith('/statuses'):return {'_embedded':{'statuses':[{'id':142}]}}
        if method=='PATCH':
            self.assertEqual(path,'leads/7');self.assertEqual(kwargs['json_body'],{'status_id':142})
            self.live['status_id']=142
            if self.fail:raise TimeoutError()
            return {'id':7,'updated_at':1}
        raise AssertionError((method,path))
    def writes(self):return [c for c in self.request.await_args_list if c.args[1]!='GET']
    async def test_button_completes_only_deal_once(self):
        for _ in range(2):self.assertTrue((await self.module.complete(self.p,self.data,self.request))['completed'])
        self.assertEqual(len(self.writes()),1);self.assertEqual(self.writes()[0].kwargs['connection'],'connection')
        self.assertEqual(self.cache.call_args.kwargs['deals'][0]['contact_name'],'Client')
        self.audit.assert_called_once()
    async def test_policy_waits_then_review_uses_current_creator(self):
        self.p['workflow']['policies']['members']['20']['deal_completion_requires_admin']=True
        self.assertTrue((await self.module.complete(self.p,self.data,self.request))['approval_pending'])
        self.assertEqual(self.writes(),[])
        self.assertTrue((await self.module.complete(self.p,self.data,self.request,reviewer=self.admin))['completed'])
        self.assertEqual(len(self.writes()),1)
    async def test_deal_transfer_denies_old_creator_even_for_review(self):
        self.live.update(pipeline_id=11,status_id=110)
        with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request)
        self.assertEqual(self.writes(),[])
    async def test_inactive_or_task_only_member_denied(self):
        self.p['active']=False
        with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request)
        self.p['active']=True;self.p['permissions']=['tasks']
        with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request)
        self.assertEqual(self.writes(),[])
    async def test_unknown_patch_never_retried(self):
        self.fail=True
        for _ in range(2):
            with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request)
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(MemoryStore.rows[('company-a',20,self.data['request_id'])][1]['step'],'sending')
    async def test_cache_failure_after_success_does_not_replay(self):
        self.cache.side_effect=RuntimeError('cache')
        self.assertTrue((await self.module.complete(self.p,self.data,self.request))['completed'])
        self.assertTrue((await self.module.complete(self.p,self.data,self.request))['completed'])
        self.assertEqual(len(self.writes()),1)
    async def test_reconnected_kommo_and_lost_deal_fail_closed(self):
        self.p['workflow']['policies']['members']['20']['deal_completion_requires_admin']=True
        await self.module.complete(self.p,self.data,self.request)
        self.module.authorize.return_value='another'
        with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request,reviewer=self.admin)
        self.assertEqual(self.writes(),[])
    async def test_reject_only_before_provider_started(self):
        self.p['workflow']['policies']['members']['20']['deal_completion_requires_admin']=True
        await self.module.complete(self.p,self.data,self.request)
        state=MemoryStore.rows[('company-a',20,self.data['request_id'])][1]
        self.module.command.return_value={'lead_id':7,'state':state}
        result=await self.module.reviews(self.admin,{'creator_id':20,'request_id':self.data['request_id'],'action':'reject'},self.request)
        self.assertTrue(result['rejected']);self.assertEqual(self.writes(),[])
        with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request)
    async def test_foreign_reviewer_cannot_approve(self):
        self.members[1]={**self.admin,'tenant_id':'foreign'}
        with self.assertRaises(TenantLinearError):await self.module.complete(self.p,self.data,self.request,reviewer=self.admin)
        self.assertEqual(self.writes(),[])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.run=AsyncMock(return_value={'completed':True})
        self.module=load('tenant_deal_completion_api',{'complete':self.run,'reviews':AsyncMock(),
            'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},{'aiohttp','tenant_deal_completion'})
        self.session=member();self.handle=self.module.handler(Mock(return_value=self.session),'https://crm.pro.az',Mock(),Mock())
        self.data={'expected_tenant_id':'company-a','expected_user_id':20}
        self.req=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':'https://crm.pro.az'},json=AsyncMock(return_value=self.data))
    async def test_missing_origin_and_wrong_user_denied(self):
        self.req.headers={};self.assertEqual((await self.handle(self.req))['status'],403)
        self.req.headers={'Origin':'https://crm.pro.az'};self.req.json.return_value={**self.data,'expected_user_id':21}
        self.assertEqual((await self.handle(self.req))['status'],409);self.run.assert_not_awaited()
    async def test_success_bound_to_session_and_no_store(self):
        result=await self.handle(self.req)
        self.assertTrue(result['data']['success']);self.assertEqual(result['headers']['Cache-Control'],'no-store')
        self.assertEqual(self.run.call_args.args[0],self.session)
    async def test_unknown_failure_is_redacted_and_keeps_request(self):
        self.run.side_effect=RuntimeError('PRIVATE')
        result=await self.handle(self.req)
        self.assertEqual(result['status'],503);self.assertTrue(result['data']['keep_request']);self.assertNotIn('PRIVATE',str(result))


class ProviderTests(unittest.TestCase):
    def test_one_patch_no_redirect_or_oauth_replay(self):
        http=Mock();http.patch.return_value=SimpleNamespace(status_code=401)
        module=load('tenant_deal_completion_provider',{'_connect':Mock(),'_fernet':Mock(),'_ensure_schema':Mock(),'authority':Mock()},
            {'tenant_platform','tenant_chat_send_store'})
        with patch.dict(module.patch.__globals__,{'requests':http,'credentials':Mock(return_value={'account_domain':'company.kommo.com','access_token':'private'})}):
            with self.assertRaises(RuntimeError):module.patch('company-a','leads/7',{'status_id':142},member(),'connection',Mock())
        http.patch.assert_called_once();self.assertFalse(http.patch.call_args.kwargs['allow_redirects'])
