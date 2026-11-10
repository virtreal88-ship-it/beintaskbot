"""Native task lifecycle and safe workflow hook tests."""
import unittest
from unittest import mock
import tenant_crm_native_tasks as tasks


class NativeTaskTests(unittest.TestCase):
    def test_text_and_due_validation(self):
        self.assertIsNone(tasks._due(None))
        self.assertEqual(tasks._due('2026-10-19T10:00:00+04:00').utcoffset().total_seconds(),0)
        with self.assertRaises(ValueError):tasks._due('2026-10-19T10:00:00')
        self.assertIsNone(tasks._text(''))
        self.assertEqual(tasks._text(' call '),'call')

    def test_workflow_trigger_is_off_by_default(self):
        cur=mock.MagicMock();cur.fetchone.return_value={'settings':{'native_task':{'enabled':False,'text':'Call'}}}
        self.assertIsNone(tasks.maybe_create_stage_task(cur,'tenant',{'id':'deal','pipeline_id':'p','stage_id':'s'},event_key='evt'))
        self.assertEqual(cur.execute.call_count,1)

    def test_assignee_must_have_tenant_task_right(self):
        person={'role':'worker','active':True,'permissions':[],'modules':{'tasks':True}}
        self.assertFalse(tasks._eligible(person,{}))
        person['permissions']=['tasks']
        self.assertTrue(tasks._eligible(person,{}))
        for denied in ({'active':False},{'modules':{'tasks':False}},
                       {'permissions':['tasks','tasks:view']}):
            with self.subTest(denied=denied):self.assertFalse(tasks._eligible({**person,**denied},{}))


    def test_native_granular_and_override_assignees_require_visible_page_and_view_update(self):
        person={'tenant_id':'a','telegram_id':20,'role':'worker','active':True,
                'permissions':[],'modules':{'tasks':False},
                'workflow':{'policies':{'members':{'20':{'native_crm_actions':{'tasks':{'view':True,'update':True}}}}}}}
        self.assertTrue(tasks._eligible(person,{}))
        settings=person['workflow']['policies']['members']['20']
        settings['page_visibility']={'tasks':False}
        self.assertFalse(tasks._eligible(person,{}))
        self.assertTrue(tasks.TenantPolicy(person).allows_action('tasks','update'))
        settings['page_visibility']={}
        settings['native_crm_actions']['tasks']['update']=False
        self.assertFalse(tasks._eligible(person,{}))
        settings['native_crm_actions']['tasks']['update']=True
        person['active']=False
        self.assertFalse(tasks._eligible(person,{}))
        person['active']=True
        person['workflow']={}
        person['permissions']=['tasks:view','tasks:update']
        self.assertTrue(tasks._eligible(person,{}))
        person['role']='manager'
        self.assertFalse(tasks._eligible(person,{'pipeline_id':'1','stage_id':'10'}))


if __name__=='__main__':unittest.main()
