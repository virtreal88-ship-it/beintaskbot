"""Database filters and atomic settings writes without a live tenant database."""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock
import uuid

from tenant_policy import ROLE_PERMISSIONS, validate_workflow_patch


class WorkflowStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=ast.parse((Path(__file__).resolve().parents[1]/'tenant_platform.py').read_text(encoding='utf-8-sig'))
        names={'save_tenant_workflow_config','list_crm_deals','list_crm_tasks','_permissions'}
        cls.code=compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<workflow-storage>','exec')

    def setUp(self):
        self.connection=MagicMock()
        self.cursor=self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.side_effect=[[{'telegram_id':1,'role':'owner'},{'telegram_id':2,'role':'manager'}],[{'pipeline_id':'10'}]]
        connect=MagicMock();connect.return_value.__enter__.return_value=self.connection
        self.ns={'member':Mock(return_value={'role':'owner','telegram_id':1}),'_connect':connect,
                 '_ensure_schema':Mock(),'validate_workflow_patch':validate_workflow_patch,'TenantPlatformError':RuntimeError,
                 '_json':json.dumps,'uuid':uuid,'_ROLE_PERMISSIONS':ROLE_PERMISSIONS,
                 'list_tenant_workflow_config':Mock(return_value={'pipelines':[]}),'_public_crm_row':lambda row:row}
        exec(self.code,self.ns)

    def test_foreign_member_reference_is_rejected_before_mutation(self):
        with self.assertRaises(RuntimeError):
            self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
                pipelines=[{'pipeline_id':'10','owner_telegram_id':999}])
        self.connection.commit.assert_not_called()
        self.assertTrue(all('SELECT' in call.args[0] for call in self.cursor.execute.call_args_list))

    def test_membership_roles_and_workflow_are_saved_in_one_transaction(self):
        self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
            pipelines=[{'pipeline_id':'10','owner_telegram_id':2}],
            policies={'members':{'2':{'pipeline_ids':['10'],'kommo_user_id':50}}},
            members=[{'telegram_id':2,'display_name':'Colleague','role':'manager','permissions':['tasks']}])
        sqls=[call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertTrue(any('UPDATE saas_tenant_members' in sql for sql in sqls))
        self.assertIn('saas_tenant_audit_events',sqls[-1])
        self.connection.commit.assert_called_once()

    def test_owner_cannot_be_downgraded_by_workflow_payload(self):
        with self.assertRaises(RuntimeError):
            self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
                members=[{'telegram_id':1,'display_name':'Owner','role':'worker','permissions':[]}])
        self.connection.commit.assert_not_called()

    def test_pipeline_and_stage_scope_is_applied_before_pagination(self):
        self.cursor.fetchall.side_effect=None;self.cursor.fetchall.return_value=[]
        self.cursor.fetchone.return_value={'total':0}
        self.ns['list_crm_deals'](tenant_id='company-a',pipeline_ids=[11],scope=[{'pipeline_id':10,'status_ids':[100]}],offset=40)
        sql, params=self.cursor.execute.call_args.args
        self.assertIn('pipeline_id = %s AND status_id = ANY(%s)',sql)
        self.assertIn('pipeline_id = ANY(%s)',sql)
        self.assertIn('LIMIT %s OFFSET %s',sql)
        self.assertEqual(params[:4],['company-a',10,[100],[11]])

    def test_empty_scope_is_not_an_all_pipeline_query(self):
        self.cursor.fetchall.side_effect=None;self.cursor.fetchall.return_value=[]
        self.cursor.fetchone.return_value={'total':0}
        self.ns['list_crm_deals'](tenant_id='company-a',scope=[])
        self.assertIn(' AND FALSE',self.cursor.execute.call_args.args[0])

    def test_missing_kommo_mapping_does_not_read_unassigned_tasks(self):
        self.assertEqual(self.ns['list_crm_tasks'](tenant_id='company-a',responsible_id=0),[])
        self.ns['_connect'].assert_not_called()

    def test_tasks_use_tenant_deal_scope_before_limit_not_kommo_user(self):
        self.cursor.fetchall.side_effect=None;self.cursor.fetchall.return_value=[]
        self.ns['list_crm_tasks'](tenant_id='company-a',scope={'kind':'pipelines','pipelines':[{'pipeline_id':10,'status_ids':[100]}]})
        sql,params=self.cursor.execute.call_args.args
        self.assertIn('d.tenant_id = saas_crm_tasks.tenant_id',sql)
        self.assertIn('d.kommo_lead_id = saas_crm_tasks.kommo_lead_id',sql)
        self.assertIn('d.status_id = ANY(%s)',sql)
        self.assertNotIn('responsible_id = %s',sql)
        self.assertEqual(params,['company-a',10,[100],100])

    def test_task_markers_match_exactly_and_missing_scope_denies(self):
        self.cursor.fetchall.side_effect=None;self.cursor.fetchall.return_value=[]
        self.ns['list_crm_tasks'](tenant_id='company-a',scope={'kind':'marker','marker':'[CRM:abc]'})
        sql,params=self.cursor.execute.call_args.args
        self.assertIn('strpos(text, %s) > 0',sql)
        self.assertEqual(params,['company-a','[CRM:abc]',100])
        self.ns['list_crm_tasks'](tenant_id='company-a',scope={'kind':'pipelines','pipelines':[]})
        self.assertIn('FALSE',self.cursor.execute.call_args.args[0])

    def test_explicitly_closed_permissions_are_not_restored(self):
        self.assertEqual(self.ns['_permissions']([],'worker'),[])
        self.assertEqual(self.ns['_permissions']([],'master'),[])


if __name__=='__main__':
    unittest.main()
