import unittest
from unittest.mock import Mock, patch
import tenant_kommo_task_update as edit
from tenant_task_flow import TaskFlowError

BASE={'request_id':'22222222-2222-4222-8222-222222222222','task_id':'12','source_revision':'a'*64,'expected_text':'old','text':'new'}
PROFILE={'tenant_id':'11111111-1111-4111-8111-111111111111','telegram_id':1,'active':True,'role':'owner'}
class TextEdit(unittest.TestCase):
    def test_strict_fields_and_text(self):
        for bad in ({**BASE,'responsible_user_id':99},{**BASE,'text':False},{**BASE,'text':'a'*4001},{**BASE,'source_revision':'x'*64}):
            with self.assertRaises(TaskFlowError):edit.normalize(bad)
    def test_preserves_hidden_marker_without_granting_visibility(self):
        mark='[CRM:'+'a'*32+']'
        payload=edit.normalize({**BASE,'expected_text':'old\n'+mark})
        self.assertEqual(payload['text'],'new\n'+mark)
        self.assertEqual(payload['visible_text'],'new')
        with self.assertRaises(TaskFlowError):edit.normalize({**BASE,'text':'new '+mark})
    @patch.object(edit,'authorize_live')
    def test_cas_and_no_other_fields(self,auth):
        p=edit.normalize(BASE)
        remote={'id':12,'text':'old','responsible_user_id':5}
        self.assertEqual(edit.prepare(None,PROFILE,None,remote,p,False,Mock()),{'text':'new'})
        auth.assert_called_once()
        for r in ({**remote,'text':'changed'},{**remote,'is_completed':True}):
            with self.assertRaises(TaskFlowError):edit.prepare(None,PROFILE,None,r,p,False,Mock())
    @patch.object(edit,'authorize_live',side_effect=TaskFlowError('task_forbidden',403))
    def test_unknown_still_requires_access(self,auth):
        with self.assertRaises(TaskFlowError):edit.prepare(None,PROFILE,None,{'id':12},edit.normalize(BASE),True,Mock())
    def test_receipt_requires_readback_and_raw_text(self):
        p=edit.normalize({**BASE,'expected_text':'old\n[CRM:abc]'})
        with self.assertRaises(TaskFlowError):edit.verified({'id':12,'text':'new'},p)
        r=edit.verified({'id':12,'text':p['text']},p)
        self.assertEqual(r['text'],'new');self.assertEqual(r['task_text'],p['text'])
    def test_cache_is_tenant_scoped_and_only_text(self):
        c=Mock();p=edit.normalize(BASE);edit.cache_confirmed(c,PROFILE,p,edit.verified({'id':12,'text':'new'},p))
        sql,args=c.execute.call_args.args
        self.assertIn('tenant_id=%s',sql);self.assertNotIn('responsible_id=',sql)
        self.assertIn(PROFILE['tenant_id'],args)
if __name__=='__main__':unittest.main()

class Transport(unittest.IsolatedAsyncioTestCase):
    async def test_exact_text_only_and_note_get_only(self):
        from types import SimpleNamespace
        from tenant_kommo import TenantKommo
        from tenant_crm_relation_contract import ProviderBinding
        binding=ProviderBinding(PROFILE['tenant_id'],'kommo','test.kommo.com','kommo_manual:test')
        service=TenantKommo(SimpleNamespace(platform_error=RuntimeError,credentials=Mock(return_value={'account_domain':'test.kommo.com','auth_mode':'manual','access_token':'fake'})))
        for method,path,body in [('PATCH','tasks/12',{'text':'ok','responsible_user_id':99}),('PATCH','tasks/12',{'text':False}),('PATCH','tasks/12',{'text':'x'*4001}),('PATCH','leads/12/notes',{'text':'bad'}),('GET','leads/12/notes?url=bad',None)]:
            with self.assertRaises(RuntimeError):await service.request_bound(binding,method,path,json_body=body)
        with patch('tenant_kommo.wait_slot'),patch('tenant_kommo.requests.request',return_value=SimpleNamespace(status_code=200,content=b'{}',json=lambda:{})) as call:
            await service.request_bound(binding,'PATCH','tasks/12',json_body={'text':'new'})
            await service.request_bound(binding,'GET','leads/12/notes')
            self.assertEqual(call.call_count,2)
            self.assertFalse(call.call_args.kwargs['allow_redirects'])
