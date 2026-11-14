"""Only simulated provider and database calls; never production task writes."""
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
import tenant_kommo_task_reschedule as task
import tenant_kommo_board_commands as commands
from tenant_task_flow import TaskFlowError
from tenant_crm_relation_contract import ProviderBinding

TENANT='11111111-1111-4111-8111-111111111111'
REQUEST='22222222-2222-4222-8222-222222222222'
PROFILE={'tenant_id':TENANT,'telegram_id':1,'active':True,'role':'owner'}
BINDING=ProviderBinding(TENANT,'kommo','test.kommo.com','kommo_manual:2026-01-01T00:00:00+00:00')

class Inputs(unittest.TestCase):
    def test_exact_fields_timezone_and_positive(self):
        body={'task_id':'12','request_id':REQUEST,'expected_due_at':None,'due_at':'2030-01-01T10:00:00+04:00','source_revision':'a'*64}
        self.assertEqual(task.normalize(body)['expected_due_at'],0)
        self.assertEqual(task.normalize(body)['due_at'],1893477600)
        for invalid in ({**body,'due_at':None},{**body,'due_at':'2030-01-01T10:00:00'}, {**body,'task_id':'-1'}, {**body,'text':'oops'}):
            with self.assertRaises(TaskFlowError): task.normalize(invalid)
    @patch.object(task,'_rows',return_value=[{'id':'12'}])
    def test_deadline_only_cas_and_completed(self,rows):
        payload={'task_id':'12','expected_due_at':100,'due_at':2000000000}
        remote={'id':12,'complete_till':100,'is_completed':False}
        self.assertEqual(task.prepare(None,PROFILE,BINDING,remote,payload,False,Mock()),{'complete_till':2000000000})
        for r in ({**remote,'complete_till':101},{**remote,'is_completed':True}):
            with self.assertRaises(TaskFlowError): task.prepare(None,PROFILE,BINDING,r,payload,False,Mock())
    @patch.object(task,'_rows',return_value=[])
    def test_hidden_cached_task_denied(self,rows):
        with self.assertRaises(TaskFlowError): task.authorize_cached(None,PROFILE,{'task_id':'12'})
    @patch.object(task,'_rows',return_value=[{'id':'12'}])
    def test_live_scope_rechecked_not_shared_responsible(self,rows):
        profile={**PROFILE,'role':'manager','workflow':{'pipelines':[],'policies':[]},'permissions':[]}
        with self.assertRaises(TaskFlowError): task.prepare(None,profile,BINDING,{'id':12,'responsible_user_id':1}, {'task_id':'12','expected_due_at':0,'due_at':2000000000},False,Mock())
    def test_receipt_does_not_claim_unverified(self):
        with self.assertRaises(TaskFlowError): task.verified({'id':12,'complete_till':100},{'task_id':'12','due_at':101})

class Store:
    state={}
    def __init__(self,*args):
        self.state=dict(type(self).state)
        self.conn=Mock(); self.cur=Mock();self.cur.fetchone.return_value=None
        self.conn.cursor.return_value.__enter__=Mock(return_value=self.cur)
        self.conn.cursor.return_value.__exit__=Mock(return_value=False)
    def lock_task(self,*args): pass
    def lock_deal(self,*args): pass
    def save(self,state): self.state=state;type(self).state=state
    def close(self): pass

class Lifecycle(unittest.TestCase):
    def setUp(self):
        Store.state={}
        self.patches=[patch.object(commands,'TaskCommandStore',Store),patch.object(commands,'current_binding',return_value=BINDING),patch.object(commands,'_profiles',return_value=(PROFILE,{})),patch.object(commands,'_require_schema'),patch('tenant_kommo_board_provenance.require')]
        for p in self.patches:p.start()
        self.payload={'request_id':REQUEST,'task_id':'12','due_at':2000000000,'expected_due_at':100}
        self.options=dict(operation='reschedule',path='tasks/12',prepare=Mock(return_value={'complete_till':2000000000}),verified=Mock(return_value={'task_id':'12'}),authorize_cached=Mock())
    def tearDown(self):
        for p in self.patches:p.stop()
    def test_single_patch_and_done_replay_reauthorizes(self):
        request=Mock(return_value={'id':12})
        commands.execute_sync(PROFILE,self.payload,request,**self.options)
        self.assertEqual([x.args[1] for x in request.call_args_list],['GET','PATCH','GET'])
        request.reset_mock();self.options['authorize_cached'].reset_mock()
        commands.execute_sync(PROFILE,self.payload,request,**self.options)
        self.assertEqual([x.args[1] for x in request.call_args_list],['GET'])
        self.options['authorize_cached'].assert_called_once()
    def test_lost_patch_only_get_on_same_key(self):
        def transport(binding,method,path,**kwargs):
            if method=='PATCH':raise TimeoutError()
            return {'id':12}
        request=Mock(side_effect=transport)
        with self.assertRaises(TaskFlowError):commands.execute_sync(PROFILE,self.payload,request,**self.options)
        self.assertEqual(Store.state['step'],'board_sending')
        request.reset_mock()
        commands.execute_sync(PROFILE,self.payload,request,**self.options,verify_only=True)
        self.assertEqual([x.args[1] for x in request.call_args_list],['GET'])
    def test_scope_denied_before_provider_read(self):
        self.options['authorize_cached'].side_effect=TaskFlowError('task_forbidden',403)
        request=Mock()
        with self.assertRaises(TaskFlowError):commands.execute_sync(PROFILE,self.payload,request,**self.options)
        request.assert_not_called()
    def test_cache_failure_after_patch_stays_unknown(self):
        self.options['cache_confirmed']=Mock(side_effect=RuntimeError('cache down'))
        request=Mock(return_value={'id':12})
        with self.assertRaises(TaskFlowError): commands.execute_sync(PROFILE,self.payload,request,**self.options)
        self.assertEqual(Store.state['step'],'board_sending')
        self.assertEqual([x.args[1] for x in request.call_args_list],['GET','PATCH','GET'])
    def test_scoped_revoke_before_replay_never_returns_receipt(self):
        request=Mock(return_value={'id':12})
        commands.execute_sync(PROFILE,self.payload,request,**self.options)
        self.options['authorize_cached'].side_effect=TaskFlowError('task_forbidden',403)
        request.reset_mock()
        with self.assertRaises(TaskFlowError) as caught: commands.execute_sync(PROFILE,self.payload,request,**self.options,verify_only=True)
        self.assertEqual(caught.exception.status,403)
        request.assert_not_called()
    def test_reconnected_integration_never_replayed(self):
        Store.state={'step':'board_sending','board_binding':{}}
        request=Mock()
        with self.assertRaises(TaskFlowError):commands.execute_sync(PROFILE,self.payload,request,**self.options)
        request.assert_not_called()

if __name__=='__main__':unittest.main()

class Transport(unittest.IsolatedAsyncioTestCase):
    async def test_single_attempt_no_redirect_no_refresh(self):
        import tenant_kommo
        from types import SimpleNamespace
        context=SimpleNamespace(platform_error=RuntimeError,credentials=Mock(return_value={'account_domain':'test.kommo.com','auth_mode':'manual','access_token':'not-a-real-token'}))
        service=tenant_kommo.TenantKommo(context)
        for code in (401,302,500):
            with patch.object(tenant_kommo,'wait_slot'),patch.object(tenant_kommo.requests,'request',return_value=SimpleNamespace(status_code=code)) as send:
                with self.assertRaises(RuntimeError):await service.request_bound(BINDING,'PATCH','tasks/12',json_body={'complete_till':2000000000})
                send.assert_called_once()
                self.assertIs(send.call_args.kwargs['allow_redirects'],False)
    async def test_exact_deadline_field_only(self):
        import tenant_kommo
        from types import SimpleNamespace
        service=tenant_kommo.TenantKommo(SimpleNamespace(platform_error=RuntimeError))
        for path,body in [('tasks/12',{'text':'no'}),('tasks/12',{'complete_till':False}),('tasks/12',{'complete_till':0}),('tasks/12/other',{'complete_till':3}),('tasks/12',{'complete_till':3,'is_completed':True})]:
            with self.assertRaises(RuntimeError):await service.request_bound(BINDING,'PATCH',path,json_body=body)
