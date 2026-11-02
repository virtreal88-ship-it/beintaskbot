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
                       {'permissions':['tasks','tasks:view']},{'permissions':['tasks:view','tasks:update']}):
            with self.subTest(denied=denied):self.assertFalse(tasks._eligible({**person,**denied},{}))


if __name__=='__main__':unittest.main()
