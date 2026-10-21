import copy
import unittest

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

    def test_action_grants_narrow_a_legacy_module_grant_and_operator_role_is_supported(self):
        person=profile('manager');person['permissions']=['tasks','tasks:view','tasks:create']
        policy=TenantPolicy(person)
        self.assertTrue(policy.allows_action('tasks','view'))
        self.assertTrue(policy.allows_action('tasks','create'))
        self.assertFalse(policy.allows_action('tasks','delete'))
        operator=profile('operator');operator['permissions']=['tasks'];operator['modules']={'tasks':True}
        self.assertTrue(TenantPolicy(operator).allows('tasks'))
        self.assertFalse(TenantPolicy(operator).allows('deals'))

    def test_master_only_has_hot_orders_and_worker_no_deals(self):
        policy=TenantPolicy(profile('master'))
        self.assertTrue(policy.allows('hot_orders'))
        self.assertFalse(policy.allows('deals'))
        self.assertFalse(policy.allows('tasks'))
        self.assertFalse(TenantPolicy(profile('worker')).allows('customers'))

    def test_ai_agents_is_independent_of_crm_modules_for_company_owner(self):
        person=profile('owner')
        person['modules']={}
        person['workflow']={}
        capabilities=TenantPolicy(person).public_capabilities()['modules']
        self.assertTrue(capabilities['ai_agents'])

    def test_ai_agents_admin_access_requires_explicit_manage_grant(self):
        person=profile('admin')
        self.assertFalse(TenantPolicy(person).public_capabilities()['modules']['ai_agents'])
        person['workflow']={'policies':[{'policy_key':'members','value':{'20':{'ai_agents_manage':True}}}]}
        self.assertTrue(TenantPolicy(person).public_capabilities()['modules']['ai_agents'])
        person['workflow']['policies'][0]['value']['20']['ai_agents_manage']=False
        self.assertFalse(TenantPolicy(person).public_capabilities()['modules']['ai_agents'])

    def test_no_assignment_and_all_hidden_stages_fail_closed(self):
        self.assertEqual(TenantPolicy(profile(telegram_id=99)).pipeline_scope(),[])
        person=profile();person['workflow']['stages'][0]['settings']['visible']=False
        self.assertNotIn(10,[item['pipeline_id'] for item in TenantPolicy(person).pipeline_scope()])

    def test_task_scope_does_not_depend_on_provider_user(self):
        self.assertEqual(TenantPolicy(profile(telegram_id=99)).task_scope(),{'kind':'pipelines','pipelines':[]})
        self.assertEqual(TenantPolicy(profile()).task_scope()['pipelines'],[{'pipeline_id':10,'status_ids':[100]}])
        self.assertEqual(TenantPolicy(profile('worker')).task_scope()['kind'],'marker')
        self.assertEqual(TenantPolicy(profile('owner')).task_scope(),{'kind':'all'})

    def test_marker_is_stable_and_tenant_specific(self):
        person=profile('worker');marker=TenantPolicy(person).task_marker()
        person['display_name']='New name';person['workflow']={}
        self.assertEqual(TenantPolicy(person).task_marker(),marker)
        person['tenant_id']='other-company'
        self.assertNotEqual(TenantPolicy(person).task_marker(),marker)
        self.assertNotEqual(TenantPolicy(profile('worker',21)).task_marker(),marker)

    def test_pipeline_executor_never_moves_existing_deal(self):
        policy=TenantPolicy(profile())
        self.assertEqual(policy.task_creation_route(has_deal=True),{'action':'keep_deal','marker':policy.task_marker()})
        self.assertEqual(policy.task_creation_route(has_deal=False),
                         {'action':'create_deal','pipeline_id':10,'status_id':100,'marker':''})

    def test_marker_executor_does_not_move_deal(self):
        policy=TenantPolicy(profile('worker'))
        self.assertEqual(policy.task_creation_route(has_deal=True),{'action':'keep_deal','marker':policy.task_marker()})
        self.assertEqual(policy.task_creation_route(has_deal=False)['action'],'standalone')

    def test_closed_and_ambiguous_pipeline_creation_is_rejected(self):
        with self.assertRaises(ValueError):
            TenantPolicy(profile(telegram_id=99)).task_creation_route(has_deal=False)
        person=profile();person['workflow']['pipelines'][1]['owner_telegram_id']=20
        with self.assertRaises(ValueError):
            TenantPolicy(person).task_creation_route(has_deal=False)
        person['permissions']=[]
        with self.assertRaises(ValueError):
            TenantPolicy(person).task_creation_route(has_deal=False)

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

    def test_native_task_trigger_is_opt_in_and_validated(self):
        validate_workflow_patch([], [{'pipeline_id':10,'stage_id':100,'settings':{'native_task':{'enabled':True,'text':'Call','responsible_id':20,'due_days':2}}}], {})
        with self.assertRaises(ValueError):
            validate_workflow_patch([], [{'pipeline_id':10,'stage_id':100,'settings':{'native_task':{'enabled':True,'text':''}}}], {})


if __name__=='__main__':
    unittest.main()
