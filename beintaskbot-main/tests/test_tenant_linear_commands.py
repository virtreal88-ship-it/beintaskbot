"""Durable intents, permission enforcement and provider reconciliation contracts."""
import hashlib
import json
import uuid
import unittest
from unittest.mock import Mock, MagicMock
import test_tenant_hot_orders as loaders
from test_tenant_linear_runtime import TEAM, OTHER, STATE, TARGET, PERSON, config, issue
from tenant_linear_policy import TenantLinearError
from tenant_linear_task_inputs import text


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.provider_issue=Mock(return_value=issue());self.mutate=Mock(return_value={'id':OTHER,'updatedAt':'new'})
        self.comment=Mock();self.context=Mock(return_value=(PERSON,config(),'secret','cfg'))
        self.module=loaders.load('tenant_linear_commands',{'_connect':self.connect,'ensure_commands_schema':Mock(),'context':self.context,
            'issue':self.provider_issue,'mutate':self.mutate,'ensure_comment':self.comment,'text':text,
            'team_catalog':Mock(return_value={'states':{'nodes':[{'id':STATE},{'id':TARGET}],'pageInfo':{'hasNextPage':False}}}),
            'edit_fields':Mock(return_value={'title':'Example','description':'Account: worker-account\nOperator: worker@example\n\nText','priority':0})},
            {'tenant_platform','tenant_linear_commands_schema','tenant_linear_context','tenant_linear_tasks_provider','tenant_linear_provider','tenant_linear_task_inputs'})
        self.data={'action':'button','button_id':'test-failed','reason':'Bug still exists','issue_id':OTHER,
            'request_id':TARGET,'expected_updated_at':'version','config_version':'cfg'}
        self.intent=None
        def fetch():
            if self.intent is not None:return {'intent':self.intent,'result':None}
            return None
        def execute(sql,params=None):
            if 'INSERT INTO saas_linear_commands' in sql:self.intent=json.loads(params[-1])
        self.cur.fetchone.side_effect=fetch;self.cur.execute.side_effect=execute

    def test_intent_committed_before_remote_write_and_reason_comment(self):
        events=[];self.conn.commit.side_effect=lambda:events.append('commit')
        def mutate(*args,**kw):
            self.assertEqual(events,['commit']);events.append('provider');return {'id':OTHER,'updatedAt':'new'}
        self.mutate.side_effect=mutate
        result=self.module.execute(PERSON,self.data)
        self.assertEqual(events,['commit','provider','commit']);self.assertEqual(result['issue_id'],OTHER)
        self.assertEqual(self.comment.call_args.args[2:],(OTHER,'Bug still exists'))
        self.assertEqual(len(self.comment.call_args.args[1]),36)

    def test_staff_cannot_edit_change_status_or_use_unassigned_button(self):
        for data in ({**self.data,'action':'edit'},{**self.data,'action':'status','state_id':TARGET},
                     {**self.data,'button_id':'other'}):
            with self.assertRaises(TenantLinearError):self.module.execute(PERSON,data)
        self.mutate.assert_not_called();self.comment.assert_not_called();self.conn.commit.assert_not_called()

    def test_unknown_scope_and_stale_version_fail_before_mutation(self):
        self.provider_issue.return_value=issue(description='Account: other')
        with self.assertRaises(TenantLinearError):self.module.execute(PERSON,self.data)
        self.provider_issue.return_value=issue(updatedAt='changed')
        with self.assertRaises(TenantLinearError) as caught:self.module.execute(PERSON,self.data)
        self.assertEqual(caught.exception.status,409);self.mutate.assert_not_called()

    def test_mandatory_comment_not_blank(self):
        with self.assertRaises(TenantLinearError):self.module.execute(PERSON,{**self.data,'reason':' '})
        self.mutate.assert_not_called()

    def test_comment_failure_leaves_pending_intent_no_success_receipt(self):
        self.comment.side_effect=TenantLinearError('Uncertain comment',502)
        with self.assertRaises(TenantLinearError):self.module.execute(PERSON,self.data)
        self.assertIsNotNone(self.intent);self.conn.commit.assert_called_once()
        self.assertFalse(any('SET result=' in call.args[0] for call in self.cur.execute.call_args_list))

    def test_pending_retry_reconciles_status_then_finishes_same_comment(self):
        intent={'action':'button','fields':{'stateId':TARGET},'before':'version','comment':'Bug still exists','button_id':'test-failed','config_version':'cfg'}
        fingerprint=hashlib.sha256(json.dumps(self.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.cur.fetchone.side_effect=[{'fingerprint':fingerprint,'intent':intent,'result':None},{'intent':intent,'result':None}]
        self.provider_issue.return_value=issue(state={'id':TARGET},updatedAt='new')
        result=self.module.execute(PERSON,self.data)
        self.assertEqual(result['updated_at'],'new');self.mutate.assert_not_called();self.comment.assert_called_once()

    def test_completed_receipt_returns_without_second_provider_write(self):
        fingerprint=hashlib.sha256(json.dumps(self.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.cur.fetchone.side_effect=[{'fingerprint':fingerprint,'result':{'issue_id':OTHER,'updated_at':'new'}}]
        self.assertEqual(self.module.execute(PERSON,self.data)['issue_id'],OTHER)
        self.mutate.assert_not_called();self.provider_issue.assert_not_called()

    def test_request_id_reuse_with_other_payload_conflicts(self):
        self.cur.fetchone.side_effect=[{'fingerprint':'other'}]
        with self.assertRaises(TenantLinearError) as caught:self.module.execute(PERSON,self.data)
        self.assertEqual(caught.exception.status,409);self.mutate.assert_not_called()

    def test_configuration_changed_between_prepare_and_execute(self):
        self.context.side_effect=[(PERSON,config(),'secret','cfg'),(PERSON,config(),'secret','changed')]
        with self.assertRaises(TenantLinearError) as caught:self.module.execute(PERSON,self.data)
        self.assertEqual(caught.exception.status,409);self.mutate.assert_not_called();self.conn.commit.assert_called_once()

    def test_create_uses_same_deterministic_id_and_configured_initial_state(self):
        data={'action':'create','request_id':TARGET,'config_version':'cfg'}
        expected=str(uuid.uuid5(uuid.UUID(TEAM),'20:'+TARGET))
        self.mutate.return_value={'id':expected,'updatedAt':'new'}
        self.module.execute(PERSON,data)
        self.assertEqual(self.mutate.call_args.args[1],expected)
        self.assertEqual(self.mutate.call_args.args[2]['stateId'],STATE);self.assertTrue(self.mutate.call_args.kwargs['create'])

    def test_create_timeout_reconciles_deterministic_issue_without_duplicate(self):
        data={'action':'create','request_id':TARGET,'config_version':'cfg'}
        expected=str(uuid.uuid5(uuid.UUID(TEAM),'20:'+TARGET))
        self.mutate.side_effect=TenantLinearError('Network',502)
        self.provider_issue.return_value=issue(id=expected,description='Account: worker-account\nOperator: worker@example\n\nText',
            priority=0,state={'id':STATE},assignee=None,updatedAt='new')
        result=self.module.execute(PERSON,data)
        self.assertEqual(result['issue_id'],expected);self.mutate.assert_called_once()

    def test_changed_task_after_pending_write_never_overwritten(self):
        intent={'action':'button','fields':{'stateId':TARGET},'before':'version','comment':'Reason','button_id':'test-failed','config_version':'cfg'}
        fingerprint=hashlib.sha256(json.dumps(self.data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.cur.fetchone.side_effect=[{'fingerprint':fingerprint,'intent':intent,'result':None},{'intent':intent,'result':None}]
        self.provider_issue.return_value=issue(updatedAt='external-change')
        with self.assertRaises(TenantLinearError):self.module.execute(PERSON,self.data)
        self.mutate.assert_not_called();self.comment.assert_not_called()

    def test_created_issue_uuid_is_different_for_other_company(self):
        self.assertNotEqual(str(uuid.uuid5(uuid.UUID(TEAM),'20:'+TARGET)),str(uuid.uuid5(uuid.UUID(OTHER),'20:'+TARGET)))


class InputTests(unittest.TestCase):
    def setUp(self):
        self.people=Mock(return_value={'nodes':[{'id':OTHER}],'pageInfo':{'hasNextPage':False}})
        self.team=Mock(return_value={'projects':{'nodes':[{'id':OTHER}],'pageInfo':{'hasNextPage':False}}})
        self.module=loaders.load('tenant_linear_task_inputs',{'team_members':self.people,'team_catalog':self.team},
            {'tenant_linear_tasks_provider','tenant_linear_provider'})
        self.data={'title':'Title','body':'Details','project_id':OTHER,'priority':2,'account':'foreign','operator':'foreign'}

    def test_staff_identity_is_from_company_settings_not_input(self):
        fields=self.module.edit_fields(PERSON,config(),'key',self.data)
        self.assertIn('Account: worker-account',fields['description']);self.assertNotIn('foreign',fields['description'])

    def test_metadata_injection_and_foreign_project_rejected(self):
        with self.assertRaises(TenantLinearError):self.module.edit_fields(PERSON,config(),'key',{**self.data,'body':'Details\nAccount: other'})
        with self.assertRaises(TenantLinearError):self.module.edit_fields(PERSON,config(),'key',{**self.data,'project_id':TARGET})

    def test_edit_keeps_existing_metadata_for_staff(self):
        fields=self.module.edit_fields(PERSON,config(),'key',self.data,issue())
        self.assertIn('Operator: other@example',fields['description'])

    def test_assignee_scope_cannot_be_changed_away(self):
        rules=config();rules['members']['20']={'assignee_id':OTHER}
        with self.assertRaises(TenantLinearError):self.module.edit_fields(PERSON,rules,'key',{**self.data,'assignee_id':''},issue(assignee={'id':OTHER}))

    def test_invalid_priorities_and_missing_text_fail(self):
        for changes in ({'priority':True},{'priority':5},{'body':''},{'title':''}):
            with self.assertRaises(TenantLinearError):self.module.edit_fields(PERSON,config(),'key',{**self.data,**changes})

    def test_assignee_only_company_can_create_without_account_operator(self):
        rules=config();rules['workflow']['required_fields']=[];rules['members']['20']={'assignee_id':OTHER,'can_create':True}
        fields=self.module.edit_fields(PERSON,rules,'key',{'title':'Task','body':'','priority':0})
        self.assertEqual(fields['assigneeId'],OTHER);self.assertEqual(fields['projectId'],None)
        self.assertEqual(fields['description'],'')
