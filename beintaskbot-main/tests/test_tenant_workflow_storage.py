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
        root=Path(__file__).resolve().parents[1]
        sources=[ast.parse((root/name).read_text(encoding='utf-8-sig')) for name in
                 ('tenant_workflow_store.py','tenant_crm_store.py','tenant_platform_common.py')]
        names={'save_tenant_workflow_config','list_crm_deals','list_crm_tasks','_permissions'}
        nodes=[node for source in sources for node in source.body
               if isinstance(node,ast.FunctionDef) and node.name in names]
        cls.code=compile(ast.Module(body=nodes,type_ignores=[]),'<workflow-storage>','exec')

    def setUp(self):
        self.connection=MagicMock()
        self.cursor=self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.side_effect=[[{'telegram_id':1,'role':'owner'},{'telegram_id':2,'role':'manager'}],[{'pipeline_id':'10'}]]
        self.cursor.fetchone.return_value={'owner_telegram_id':1,'status':'active','value':{},'telegram_id':1,'role':'owner'}
        connect=MagicMock();connect.return_value.__enter__.return_value=self.connection
        self.ns={'_member':Mock(return_value={'role':'owner','telegram_id':1}),'_connect':connect,
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
        locks=[call for call in self.cursor.execute.call_args_list if 'saas_tenant_members' in call.args[0] and ' FOR ' in call.args[0]]
        self.assertTrue(locks[0].args[0].endswith('FOR SHARE'))
        self.assertTrue(locks[1].args[0].endswith('FOR UPDATE'))
        self.assertIn('saas_tenant_audit_events',sqls[-1])
        self.connection.commit.assert_called_once()

    def test_owner_cannot_be_downgraded_by_workflow_payload(self):
        with self.assertRaises(RuntimeError):
            self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
                members=[{'telegram_id':1,'display_name':'Owner','role':'worker','permissions':[]}])
        self.connection.commit.assert_not_called()

    def sparse_change(self, **overrides):
        return dict(telegram_id=2, expected_pipeline_ids=['10'], pipeline_ids=[], **overrides)

    def test_sparse_pipeline_changes_preserve_latest_work_settings_and_inactive_members(self):
        latest={'2':{'pipeline_ids':['10'],'ai_enabled':True,'provider_scopes':{'linear':['secret-team']},
                     'notifications':{'task_assigned':{'push':True}}},
                '99':{'pipeline_ids':['old'],'ai_enabled':False}}
        self.cursor.fetchone.side_effect=[{'owner_telegram_id':1,'status':'active'}, {'value':latest}, {'telegram_id':1,'role':'owner'}, {'telegram_id':2,'role':'manager'}]
        self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
            member_pipeline_changes=[self.sparse_change()])
        writes=[c for c in self.cursor.execute.call_args_list if 'DO UPDATE SET value=' in c.args[0]]
        saved=json.loads(writes[0].args[1][2])
        self.assertEqual(saved, {**latest,'2':{**latest['2'],'pipeline_ids':[]}})
        self.assertEqual(latest['2']['pipeline_ids'], ['10'])
        sqls=[c.args[0] for c in self.cursor.execute.call_args_list]
        self.assertIn('FOR SHARE',sqls[0])
        self.assertIn('policy_key=%s FOR UPDATE',sqls[2])
        self.assertIn('telegram_id=%s FOR SHARE',sqls[3])

    def test_sparse_pipeline_conflict_rolls_back_instead_of_overwriting(self):
        self.cursor.fetchone.side_effect=[{'owner_telegram_id':1,'status':'active'},
                                         {'value':{'2':{'pipeline_ids':['new']}}}, {'telegram_id':1,'role':'owner'}, {'telegram_id':2,'role':'manager'}]
        with self.assertRaisesRegex(RuntimeError,'dəyişib'):
            self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
                member_pipeline_changes=[self.sparse_change()])
        self.connection.commit.assert_not_called()

    def test_nonmanager_and_foreign_target_cannot_be_changed(self):
        for target in (1,999):
            with self.assertRaises(RuntimeError):
                self.cursor.fetchall.side_effect=[[{'telegram_id':1,'role':'owner'}, {'telegram_id':2,'role':'manager'}],[{'pipeline_id':'10'}]]
                self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
                    member_pipeline_changes=[dict(telegram_id=target,expected_pipeline_ids=None,pipeline_ids=[])])
        self.connection.commit.assert_not_called()

    def test_only_referenced_members_are_locked_in_numeric_order(self):
        self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
            pipelines=[{'pipeline_id':'10','owner_telegram_id':2}],
            member_pipeline_changes=[dict(telegram_id=2,expected_pipeline_ids=None,pipeline_ids=[])])
        locks=[call for call in self.cursor.execute.call_args_list
               if 'saas_tenant_members' in call.args[0] and ' FOR ' in call.args[0]]
        self.assertEqual([call.args[1] for call in locks],[('company-a',1),('company-a',2)])
        self.assertTrue(all(call.args[0].endswith('FOR SHARE') for call in locks))

    def test_pipeline_reassignment_does_not_lock_previous_owner(self):
        self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1,
            pipelines=[{'pipeline_id':'10','owner_telegram_id':1}])
        locks=[call for call in self.cursor.execute.call_args_list
               if 'saas_tenant_members' in call.args[0] and ' FOR ' in call.args[0]]
        self.assertEqual([call.args[1] for call in locks],[('company-a',1)])

    def test_stale_owner_is_rechecked_in_transaction(self):
        self.cursor.fetchone.return_value={'owner_telegram_id':99,'status':'active'}
        with self.assertRaises(RuntimeError):
            self.ns['save_tenant_workflow_config'](tenant_id='company-a',owner_id=1)
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
