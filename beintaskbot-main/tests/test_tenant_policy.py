import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tenant_policy import TenantPolicy, validate_workflow_patch


def profile(role='manager', telegram_id=20):
    return {'tenant_id':'company-a','telegram_id':telegram_id,'role':role,'active':True,
            'permissions':['tasks','customers','deals','hot_orders'],
            'modules':{'tasks':True,'customers':True,'deals':True,'hot_orders':True},
            'workflow':{'pipelines':[
                {'pipeline_id':'10','active':True,'owner_telegram_id':20},
                {'pipeline_id':'11','active':True,'owner_telegram_id':21}],
                'stages':[{'pipeline_id':'10','stage_id':'100','settings':{'visible':True}},
                          {'pipeline_id':'10','stage_id':'101','settings':{'visible':False}}],
                'policies':[{'policy_key':'members','value':{'20':{'kommo_user_id':50}}}]}}


class TenantPolicyTests(unittest.TestCase):
    def test_manager_only_reads_assigned_pipeline_and_visible_stage(self):
        policy=TenantPolicy(profile())
        self.assertEqual(policy.pipeline_scope(),[{'pipeline_id':10,'status_ids':[100]}])
        self.assertTrue(policy.can_access_deal({'tenant_id':'company-a','pipeline_id':10,'status_id':100}))
        self.assertFalse(policy.can_access_deal({'tenant_id':'company-a','pipeline_id':10,'status_id':101}))
        self.assertFalse(policy.can_access_deal({'tenant_id':'company-a','pipeline_id':11,'status_id':100}))

    def test_cross_tenant_deal_is_denied_even_to_owner(self):
        self.assertFalse(TenantPolicy(profile('owner')).can_access_deal({'tenant_id':'company-b','pipeline_id':10,'status_id':100}))

    def test_owner_and_admin_have_all_active_pipelines(self):
        for role in ('owner','admin'):
            self.assertEqual(len(TenantPolicy(profile(role)).pipeline_scope()),2)

    def test_closed_module_and_explicit_empty_permissions_stay_closed(self):
        person=profile();person['permissions']=[]
        self.assertFalse(TenantPolicy(person).allows('tasks'))
        person=profile();person['workflow']['policies'].append({'policy_key':'modules','value':{'deals':False}})
        self.assertFalse(TenantPolicy(person).allows('deals'))

    def test_master_only_has_hot_orders_and_worker_no_deals(self):
        policy=TenantPolicy(profile('master'))
        self.assertTrue(policy.allows('hot_orders'))
        self.assertFalse(policy.allows('deals'))
        self.assertFalse(policy.allows('tasks'))
        self.assertFalse(TenantPolicy(profile('worker')).allows('customers'))

    def test_no_assignment_and_all_hidden_stages_fail_closed(self):
        self.assertEqual(TenantPolicy(profile(telegram_id=99)).pipeline_scope(),[])
        person=profile();person['workflow']['stages'][0]['settings']['visible']=False
        self.assertNotIn(10,[item['pipeline_id'] for item in TenantPolicy(person).pipeline_scope()])

    def test_missing_kommo_id_is_not_an_all_task_scope(self):
        self.assertEqual(TenantPolicy(profile(telegram_id=99)).task_responsible_id(),0)
        self.assertEqual(TenantPolicy(profile('worker')).task_responsible_id(),50)
        self.assertIsNone(TenantPolicy(profile('owner')).task_responsible_id())

    def test_configuration_change_applies_to_next_snapshot(self):
        person=profile();self.assertTrue(TenantPolicy(person).allows('tasks'))
        newer=copy.deepcopy(person);newer['permissions'].remove('tasks')
        self.assertFalse(TenantPolicy(newer).allows('tasks'))

    def test_self_created_tasks_skip_creation_review_but_not_completion_review(self):
        person=profile();person['workflow']['policies'].append({'policy_key':'task_approval','value':{
            'creation_requires_admin':True,'completion_requires_admin':True,'self_created_exempt':True}})
        policy=TenantPolicy(person)
        self.assertFalse(policy.requires_task_approval(creator_id=20,executor_id=20))
        self.assertTrue(policy.requires_task_approval(creator_id=20,executor_id=21))
        self.assertTrue(policy.requires_task_approval(creator_id=20,executor_id=20,completion=True))

    def test_owner_onboarding_fallback_is_not_implicitly_shared(self):
        person=profile();person['workflow']={};person['onboarding']={'pipeline':{'selected':[{'id':10,'stage_ids':[100]}]}}
        self.assertEqual(TenantPolicy(person).pipeline_scope(),[])
        person['role']='owner';self.assertEqual(TenantPolicy(person).pipeline_scope(),[{'pipeline_id':10,'status_ids':[100]}])

    def test_invalid_workflow_payloads_fail_before_db_write(self):
        for pipelines,stages,policies in [({},[],{}),([],[],[]),([{'pipeline_id':'bad'}],[],{}),
            ([],[{'pipeline_id':1,'stage_id':2,'settings':[]}],{}),([],[],{'modules':{'deals':'false'}}),
            ([],[],{'members':{'20':{'kommo_user_id':'invalid'}}})]:
            with self.subTest(payload=(pipelines,stages,policies)),self.assertRaises(ValueError):
                validate_workflow_patch(pipelines,stages,policies)


class Query(dict):
    def getall(self,key,default):
        return self.get(key,default)


class TenantRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))
        names={'_tenant_has_module','handle_platform_crm_deals','handle_platform_crm_deal',
               'handle_platform_crm_chat','handle_platform_crm_chat_send','handle_platform_crm_tasks','handle_platform_workflow_config'}
        cls.code=compile(ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names],type_ignores=[]),'<tenant-api>','exec')

    def setUp(self):
        self.person=profile()
        self.ns={'TenantPolicy':TenantPolicy,'asyncio':asyncio,'ROLE_PERMISSIONS':{},
                 'web':SimpleNamespace(Request=dict,Response=dict,json_response=lambda data,status=200:{'status':status,'data':data}),
                 '_tenant_member_from_request':lambda request,owner_only=False: self.person if not owner_only or self.person['role']=='owner' else None,
                 'list_tenant_crm_deals':Mock(return_value=([],0)),'list_tenant_crm_tasks':Mock(return_value=[]),
                 'get_tenant_crm_deal':Mock(return_value={'tenant_id':'company-a','pipeline_id':11,'status_id':100}),
                 'list_tenant_crm_messages':Mock(), 'logger':Mock()}
        exec(self.code,self.ns)
        self.request=SimpleNamespace(rel_url=SimpleNamespace(query=Query(lead_id='1')))

    def test_list_filters_are_intersected_with_database_scope(self):
        self.request.rel_url.query=Query(pipeline_id=['11'])
        result=asyncio.run(self.ns['handle_platform_crm_deals'](self.request))
        self.assertEqual(result['status'],200)
        self.assertEqual(self.ns['list_tenant_crm_deals'].call_args.kwargs['scope'],[{'pipeline_id':10,'status_ids':[100]}])
        self.assertEqual(self.ns['list_tenant_crm_deals'].call_args.kwargs['pipeline_ids'],[11])

    def test_direct_deal_and_cached_chat_cannot_bypass_assignment(self):
        for name in ('handle_platform_crm_deal','handle_platform_crm_chat'):
            result=asyncio.run(self.ns[name](self.request))
            self.assertEqual(result['status'],404)
        self.ns['list_tenant_crm_messages'].assert_not_called()

    def test_task_query_is_scoped_to_assigned_kommo_user(self):
        self.person['role']='worker'
        result=asyncio.run(self.ns['handle_platform_crm_tasks'](self.request))
        self.assertEqual(result['status'],200)
        self.assertEqual(self.ns['list_tenant_crm_tasks'].call_args.kwargs['responsible_id'],50)

    def test_workflow_settings_remain_owner_only(self):
        self.person['role']='admin';self.request.method='GET'
        result=asyncio.run(self.ns['handle_platform_workflow_config'](self.request))
        self.assertEqual(result['status'],403)


if __name__=='__main__':
    unittest.main()
