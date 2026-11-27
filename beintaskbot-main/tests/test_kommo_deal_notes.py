import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch
import tenant_kommo_deal_notes as notes
from tenant_task_flow import TaskFlowError
from tenant_crm_relation_contract import ProviderBinding

TENANT='11111111-1111-4111-8111-111111111111'
PROFILE={'tenant_id':TENANT,'telegram_id':1,'role':'owner','active':True}
BINDING=ProviderBinding(TENANT,'kommo','test.kommo.com','kommo_manual:test')
class Notes(unittest.TestCase):
    def test_plain_text_only_and_identity(self):
        body={'_embedded':{'notes':[{'id':2,'entity_id':12,'note_type':'common','params':{'text':'hello'}},
            {'id':3,'entity_id':12,'note_type':'call_in','params':{'text':'secret url'}}]},'_links':{'next':{'href':'untrusted'}}}
        result=notes.sanitize_notes(body,'12')
        self.assertEqual([x['text'] for x in result['notes']],['hello']);self.assertTrue(result['has_more'])
        self.assertEqual(notes.sanitize_notes(body,'12',include_events=True)['calls'],
                         [{'id':'3','type':'call_in','created_at':None}])
        body['_embedded']['notes'][0]['entity_id']=13
        with self.assertRaises(TaskFlowError):notes.sanitize_notes(body,'12')
    def test_stage_event_only_for_exact_lead(self):
        row={'id':'event','type':'lead_status_changed','entity_type':'lead','entity_id':12,'created_at':100,
             'value_after':[{'lead_status':{'id':3,'pipeline_id':1}}]}
        payload={'_embedded':{'events':[row]}}
        self.assertEqual(notes.sanitize_stage_events(payload,'12'),
                         ([{'id':'event','created_at':100,'stage_id':'3','pipeline_id':'1'}],False))
        with self.assertRaises(TaskFlowError):notes.sanitize_stage_events(payload,'13')
    def run_read(self, remote, *, cached=None, allowed=True):
        cur=Mock();cur.fetchone.return_value=cached or {'tenant_id':TENANT,'kommo_lead_id':12,'pipeline_id':2,'raw':{}}
        conn=Mock();conn.cursor.return_value.__enter__=Mock(return_value=cur);conn.cursor.return_value.__exit__=Mock(return_value=False)
        db=Mock();db.__enter__=Mock(return_value=conn);db.__exit__=Mock(return_value=False)
        provider=Mock(side_effect=[remote,{'_embedded':{'notes':[]}}])
        with ExitStack() as stack:
            stack.enter_context(patch.object(notes,'_connect',return_value=db))
            stack.enter_context(patch.object(notes,'current_binding',return_value=BINDING))
            stack.enter_context(patch.object(notes,'_profiles',return_value=(PROFILE,{})))
            stack.enter_context(patch.object(notes,'require_source'))
            stack.enter_context(patch('tenant_member_access.page_capabilities',return_value={'deals':allowed}))
            try: result=notes.read_sync(PROFILE,'12',provider)
            except TaskFlowError: result=None
        return result,provider,cur
    def test_authorized_read_only_exact_paths(self):
        result,provider,cur=self.run_read({'id':12,'pipeline_id':2})
        self.assertEqual(result,{'notes':[],'has_more':False,'can_create':False})
        self.assertEqual([c.args[1:] for c in provider.call_args_list],[('GET','leads/12'),('GET','leads/12/notes')])
        self.assertIn(TENANT,cur.execute.call_args.args[1])
    def test_wrong_live_lead_stops_notes(self):
        result,provider,_=self.run_read({'id':13,'pipeline_id':2})
        self.assertIsNone(result);self.assertEqual(provider.call_count,1)
    def test_page_denial_stops_all_network(self):
        result,provider,_=self.run_read({'id':12},allowed=False)
        self.assertIsNone(result);provider.assert_not_called()
    def test_foreign_tenant_cache_stops_network(self):
        result,provider,_=self.run_read({'id':12},cached={'tenant_id':'foreign','raw':{}})
        self.assertIsNone(result);provider.assert_not_called()
if __name__=='__main__':unittest.main()
