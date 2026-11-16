import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack
from dataclasses import asdict
import tenant_kommo_note_create as n
from tenant_task_flow import TaskFlowError
from tenant_crm_relation_contract import ProviderBinding
P={'tenant_id':'11111111-1111-4111-8111-111111111111','telegram_id':1,'role':'owner','active':True}
B=ProviderBinding(P['tenant_id'],'kommo','test.kommo.com','kommo_manual:test')
PAY={'request_id':'22222222-2222-4222-8222-222222222222','lead_id':'12','text':'hello'}
NOTE={'id':8,'entity_id':12,'note_type':'common','params':{'text':'hello'}}
class Create(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(n.normalize(PAY),PAY)
        for bad in ({**PAY,'text':''},{**PAY,'text':'x'*10001},{**PAY,'text':False},{**PAY,'other':'x'}):
            with self.assertRaises(TaskFlowError):n.normalize(bad)
    def run_command(self,state=None, provider=None, fail_auth=False):
        store=Mock();store.state=state or {};cur=Mock();store.conn.cursor.return_value.__enter__=Mock(return_value=cur);store.conn.cursor.return_value.__exit__=Mock(return_value=False)
        store.save.side_effect=lambda state:setattr(store,'state',state)
        provider=provider or Mock(side_effect=[{'_embedded':{'notes':[{'id':8,'entity_id':12}]}},NOTE])
        stack=ExitStack();stack.enter_context(patch.object(n,'TaskCommandStore',return_value=store));stack.enter_context(patch.object(n,'current_binding',return_value=B));stack.enter_context(patch.object(n,'_profiles',return_value=(P,{})));auth=stack.enter_context(patch.object(n,'authorize',side_effect=TaskFlowError('task_forbidden',403) if fail_auth else None))
        with stack:
            try:result=n.create_sync(P,PAY,provider)
            except Exception as e:result=e
        return result,provider,store,auth
    def test_success_checkpoint_and_exact_post(self):
        result,provider,store,auth=self.run_command()
        self.assertEqual(result['note']['id'],'8');self.assertEqual(auth.call_count,3)
        self.assertEqual(provider.call_args_list[0].args[1:],('POST','leads/12/notes'))
        self.assertEqual(provider.call_args_list[0].kwargs['json_body'],[{'note_type':'common','params':{'text':'hello'}}])
        self.assertEqual(store.state['step'],'done')
    def test_timeout_never_posts_again(self):
        result,provider,store,_=self.run_command(provider=Mock(side_effect=TimeoutError()))
        self.assertTrue(result.keep_request);self.assertEqual(store.state['step'],'note_sending')
        result,p,_,_=self.run_command(state=store.state)
        self.assertTrue(result.keep_request);p.assert_not_called()
    def test_known_id_check_only_gets(self):
        result,p,_,_=self.run_command(state={'step':'note_sending','provider_note_id':'8','board_binding':asdict(B)},provider=Mock(return_value=NOTE))
        self.assertEqual(result['state'],'confirmed');self.assertEqual(p.call_args.args[1:],('GET','leads/12/notes/8'))
    def test_denied_before_post(self):
        result,p,store,_=self.run_command(fail_auth=True)
        self.assertEqual(result.status,403);p.assert_not_called()
    def test_denied_unknown_preserves_uncertainty(self):
        result,p,store,_=self.run_command(state={'step':'note_sending'},fail_auth=True)
        self.assertTrue(result.keep_request);self.assertEqual(store.state['step'],'note_sending');p.assert_not_called()
    def test_receipt_rejects_wrong_note(self):
        for row in ({**NOTE,'entity_id':13},{**NOTE,'params':{'text':'different'}},{**NOTE,'note_type':'call_in'}):
            with self.assertRaises(TaskFlowError):n.note_receipt({'_embedded':{'notes':[row]}},PAY)

class Transport(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_common_note_post(self):
        from types import SimpleNamespace
        from tenant_kommo import TenantKommo
        service=TenantKommo(SimpleNamespace(platform_error=RuntimeError,credentials=Mock(return_value={'account_domain':'test.kommo.com','auth_mode':'manual','access_token':'fake'})))
        invalid=[('POST','leads/12',[]),('POST','leads/12/notes',[{'note_type':'call_in','params':{'text':'x'}}]),('POST','leads/12/notes',[{'note_type':'common','params':{'text':'x','url':'secret'}}]),('POST','leads/12/notes',[]),('PATCH','leads/12/notes',{'text':'x'}),('GET','leads/12/notes/8?x=1',None)]
        for method,path,body in invalid:
            with self.assertRaises(RuntimeError):await service.request_bound(B,method,path,json_body=body)
        with patch('tenant_kommo.wait_slot'),patch('tenant_kommo.requests.request',return_value=SimpleNamespace(status_code=200,content=b'{}',json=lambda:{})) as call:
            await service.request_bound(B,'POST','leads/12/notes',json_body=[{'note_type':'common','params':{'text':'hello'}}])
            await service.request_bound(B,'GET','leads/12/notes/8')
            self.assertEqual(call.call_count,2)
            self.assertFalse(call.call_args.kwargs['allow_redirects'])

class Authorization(unittest.TestCase):
    def test_explicit_stage_not_read_visibility(self):
        d={'tenant_id':P['tenant_id'],'pipeline_id':2,'status_id':3}
        with patch.object(n,'stage_choices',return_value=[]):self.assertFalse(n.can_create(P,d))
        with patch.object(n,'stage_choices',return_value=[{'pipeline_id':2,'stage_id':3}]):
            self.assertTrue(n.can_create(P,d))
            self.assertFalse(n.can_create(P,{**d,'tenant_id':'other'}))
            self.assertFalse(n.can_create(P,{**d,'status_id':4}))
