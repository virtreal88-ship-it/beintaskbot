"""Runtime policy and two-company isolation tests. No real Linear mutations."""
import unittest
from unittest.mock import Mock, MagicMock
import test_tenant_hot_orders as loaders
from tenant_linear_policy import settings, TenantLinearError
from tenant_linear_runtime_policy import visible, metadata, capabilities

TEAM='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
OTHER='bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
STATE='cccccccc-cccc-4ccc-cccc-cccccccccccc'
TARGET='dddddddd-dddd-4ddd-dddd-dddddddddddd'
PERSON={'tenant_id':TEAM,'telegram_id':20,'role':'worker','active':True,'tenant_status':'active','permissions':['linear'],'modules':{'linear':True}}


def config():
    return settings({'team_id':TEAM,'workflow':{'enabled':True,'creation_state_id':STATE,'buttons':[
        {'id':'test-failed','label':'Testdən keçmədi','from_state_ids':[STATE],'to_state_id':TARGET,'require_reason':True}]},
        'members':{'20':{'account':'worker-account','operator':'worker@example','can_create':True,'button_ids':['test-failed']}}})


def issue(**changes):
    return {'id':OTHER,'title':'Example','description':'Account: worker-account\nOperator: other@example\n\nDetails',
        'team':{'id':TEAM},'state':{'id':STATE},'updatedAt':'version',**changes}


class PolicyTests(unittest.TestCase):
    def test_or_and_have_different_effects(self):
        rules=config();self.assertTrue(visible(PERSON,rules,issue()))
        rules['workflow']['visibility_mode']='all';self.assertFalse(visible(PERSON,rules,issue()))
        self.assertTrue(visible(PERSON,rules,issue(description='Account: worker-account\nOperator: worker@example')))

    def test_operator_union_and_exact_account_not_substring(self):
        self.assertTrue(visible(PERSON,config(),issue(description='Account: other\nOperator: worker@example')))
        self.assertFalse(visible(PERSON,config(),issue(description='Account: worker-account-extra\nOperator: other@example')))

    def test_missing_or_duplicate_bindings_do_not_grant_access(self):
        rules=config();rules['members']={};self.assertFalse(visible(PERSON,rules,issue()))
        self.assertFalse(visible(PERSON,config(),issue(description='Account: worker-account\nAccount: another')))
        self.assertEqual(metadata(issue(description='Account:\nOperator: worker@example'),{})['account'],'')

    def test_owner_all_but_not_foreign_team(self):
        owner={**PERSON,'role':'owner'}
        self.assertTrue(visible(owner,config(),issue(description='')))
        self.assertFalse(visible(owner,config(),issue(team={'id':OTHER})))

    def test_assignee_and_explicit_view_all(self):
        rules=config();rules['members']['20']={'assignee_id':OTHER}
        self.assertTrue(visible(PERSON,rules,issue(description='',assignee={'id':OTHER})))
        self.assertFalse(visible(PERSON,rules,issue(description='')))
        rules['members']['20']={'can_view_all':True};self.assertTrue(visible(PERSON,rules,issue(description='')))

    def test_staff_only_gets_selected_buttons_on_selected_states(self):
        rights=capabilities(PERSON,config(),issue());self.assertFalse(rights['can_edit']);self.assertFalse(rights['can_change_status'])
        self.assertTrue(rights['can_create']);self.assertEqual(rights['buttons'][0]['id'],'test-failed')
        self.assertEqual(capabilities(PERSON,config(),issue(state={'id':TARGET}))['buttons'],[])
        rules=config();rules['members']['20']['button_ids']=[];self.assertEqual(capabilities(PERSON,rules,issue())['buttons'],[])

    def test_invalid_workflow_and_unknown_buttons_fail(self):
        for workflow in ({'enabled':'yes'},{'visibility_mode':'none'},{'account_prefix':' '},
            {'account_prefix':'Same','operator_prefix':'same'},{'buttons':[{'id':'wrong','label':'X','from_state_ids':[],'to_state_id':TARGET}]}):
            with self.assertRaises(TenantLinearError):settings({'workflow':workflow})
        with self.assertRaises(TenantLinearError):settings({'members':{'20':{'button_ids':['unknown']}}})


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.page=Mock(return_value={'nodes':[issue(),issue(id=TARGET,description='Account: other'),issue(id=STATE,team={'id':OTHER})],
            'pageInfo':{'hasNextPage':True,'endCursor':'cursor'}})
        self.context=Mock(return_value=(PERSON,config(),'tenant-secret','configuration'))
        self.module=loaders.load('tenant_linear_tasks',{'_connect':self.connect,'_ensure_schema':Mock(),'context':self.context,
            'issue_page':self.page,'team_members':Mock(),'team_catalog':Mock()}, {'tenant_platform','tenant_linear_context','tenant_linear_tasks_provider','tenant_linear_provider'})

    def test_server_filters_before_returning_any_data(self):
        result=self.module.listing(PERSON)
        self.assertEqual([row['id'] for row in result['issues']],[OTHER])
        self.assertNotIn('tenant-secret',str(result));self.assertTrue(result['pageInfo']['hasNextPage'])
        self.assertEqual(self.page.call_args.args[1]['team']['id']['eq'],TEAM)

    def test_search_and_cursor_never_replace_team_scope(self):
        self.module.listing(PERSON,search='text',after='cursor')
        self.assertEqual(self.page.call_args.args[1]['and'][0]['team']['id']['eq'],TEAM)
        self.assertEqual(self.page.call_args.args[2],'cursor')

    def test_no_binding_no_provider_request(self):
        rules=config();rules['members']={};self.context.return_value=(PERSON,rules,'secret','v')
        self.assertEqual(self.module.listing(PERSON)['issues'],[]);self.page.assert_not_called()


class ContextTests(unittest.TestCase):
    def setUp(self):
        from cryptography.fernet import Fernet
        import json
        self.cipher=Fernet(Fernet.generate_key());self.cur=MagicMock()
        self.connection={'status':'connected','secrets':self.cipher.encrypt(json.dumps({'tenant_id':TEAM,'provider':'linear','api_key':'private'}).encode()),
            'metadata':{'settings':config()},'updated_at':'v'}
        self.module=loaders.load('tenant_linear_context',{'_fernet':Mock(return_value=self.cipher),'_read_tenant_workflow':Mock(return_value={})}, {'tenant_platform'})

    def test_current_membership_and_connection_not_request_claims(self):
        self.cur.fetchone.side_effect=[PERSON,self.connection]
        profile,rules,key,version=self.module.context(self.cur,{**PERSON,'role':'owner'})
        self.assertEqual(profile['role'],'worker');self.assertEqual(key,'private')
        self.assertEqual(self.cur.execute.call_args_list[0].args[1],(TEAM,20))

    def test_inactive_denied_module_and_foreign_cipher_fail(self):
        for changes in ({'active':False},{'permissions':[]},{'tenant_status':'disabled'},{'role':'master'}):
            self.cur.fetchone.side_effect=[{**PERSON,**changes},self.connection]
            with self.assertRaises(TenantLinearError):self.module.context(self.cur,PERSON)
        self.cur.fetchone.side_effect=[{**PERSON,'tenant_id':OTHER},self.connection]
        with self.assertRaises(TenantLinearError):self.module.context(self.cur,{**PERSON,'tenant_id':OTHER})

    def test_connected_but_not_enabled_is_closed(self):
        rules=config();rules['workflow']['enabled']=False
        self.cur.fetchone.side_effect=[PERSON,{**self.connection,'metadata':{'settings':rules}}]
        with self.assertRaises(TenantLinearError) as caught:self.module.context(self.cur,PERSON)
        self.assertEqual(caught.exception.status,409)


class ProviderCommandTests(unittest.TestCase):
    def test_comment_duplicate_or_lost_response_reconciles_same_id(self):
        query=Mock(side_effect=[TenantLinearError('Timeout',502),{'comment':{'id':TARGET,'body':'Reason','issue':{'id':OTHER}}}])
        module=loaders.load('tenant_linear_tasks_provider',{'query':query},{'tenant_linear_provider'})
        module.ensure_comment('secret',TARGET,OTHER,'Reason')
        self.assertEqual(query.call_args_list[0].args[2]['input']['id'],TARGET)
        self.assertNotIn('mutation',query.call_args_list[1].args[1])

    def test_existing_foreign_comment_cannot_satisfy_reason(self):
        query=Mock(side_effect=[TenantLinearError('Duplicate',502),{'comment':{'id':TARGET,'body':'Reason','issue':{'id':TEAM}}}])
        module=loaders.load('tenant_linear_tasks_provider',{'query':query},{'tenant_linear_provider'})
        with self.assertRaises(TenantLinearError):module.ensure_comment('secret',TARGET,OTHER,'Reason')
