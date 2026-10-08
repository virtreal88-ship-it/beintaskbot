import copy
import unittest

from tenant_hot_order_policy import HotOrderPolicy
from tenant_policy import TenantPolicy, validate_workflow_patch


def person(role='master'):
    return {'tenant_id':'company-a','telegram_id':20,'role':role,'active':True,
            'permissions':['hot_orders'],'modules':{'hot_orders':True},
            'workflow':{'policies':{'hot_orders':{'services':[
                {'id':'repair','name':'Təmir','active':True},{'id':'old','name':'Köhnə','active':False}]},
                'members':{'20':{'hot_order_services':['repair','old']}}}}}


def order(**updates):
    return {'tenant_id':'company-a','status':'open','service_id':'repair','created_by':21,**updates}


class HotOrderPolicyTests(unittest.TestCase):
    def policy(self, profile=None):
        return HotOrderPolicy(TenantPolicy(profile or person()))

    def test_defaults_and_per_member_overrides(self):
        self.assertFalse(self.policy().allowed('create'));self.assertTrue(self.policy().allowed('claim'))
        p=person();p['workflow']['policies']['members']['20']['hot_order_create']=True
        self.assertTrue(self.policy(p).allowed('create'))
        p['workflow']['policies']['members']['20']['hot_order_claim']=False
        self.assertFalse(self.policy(p).can_claim(order()))

    def test_company_roles_are_configurable_and_empty_list_does_not_restore_defaults(self):
        p=person('worker');p['workflow']['policies']['hot_orders']['claim_roles']=['worker']
        self.assertTrue(self.policy(p).can_claim(order()))
        p['workflow']['policies']['hot_orders']['claim_roles']=[]
        self.assertFalse(self.policy(p).can_claim(order()))

    def test_inactive_members_and_closed_page_cannot_override_access(self):
        for key,value in [('active',False),('permissions',[])]:
            p=person();p[key]=value;p['workflow']['policies']['members']['20']['hot_order_claim']=True
            self.assertFalse(self.policy(p).can_claim(order()))
            self.assertEqual(self.policy(p).capabilities()['services'],[])
        p=person();p['modules']['hot_orders']=False
        self.assertFalse(self.policy(p).allowed('claim'))

    def test_only_assigned_active_services_are_claimable(self):
        self.assertTrue(self.policy().can_claim(order()))
        for service in ('old','unknown',''):
            self.assertFalse(self.policy().can_claim(order(service_id=service)))
        p=person();p['workflow']['policies']['members']['20']['hot_order_services']=[]
        self.assertFalse(self.policy(p).can_claim(order()))

    def test_cross_company_access_denied_even_to_owner(self):
        for role in ('master','owner','admin'):
            policy=self.policy(person(role));foreign=order(tenant_id='company-b',created_by=20,claimed_by=20)
            self.assertFalse(policy.can_view(foreign));self.assertFalse(policy.can_edit(foreign))
            self.assertFalse(policy.can_claim(foreign))

    def test_claimed_order_cannot_be_edited_or_claimed_again(self):
        for role in ('master','owner','admin'):
            policy=self.policy(person(role));accepted=order(status='claimed',created_by=20,claimed_by=20)
            self.assertTrue(policy.can_view(accepted));self.assertFalse(policy.can_edit(accepted))
            self.assertFalse(policy.can_claim(accepted))

    def test_open_order_edit_requires_creator_or_administrator(self):
        self.assertFalse(self.policy().can_edit(order()))
        self.assertTrue(self.policy().can_edit(order(created_by=20)))
        self.assertTrue(self.policy(person('admin')).can_edit(order()))

    def test_capabilities_are_settings_only_and_fresh(self):
        p=person();before=copy.deepcopy(p);cap=self.policy(p).capabilities()
        self.assertTrue(cap['settings_only']);self.assertEqual(cap['assigned_services'],['repair'])
        cap['services'][0]['name']='Changed';self.assertEqual(p,before)

    def test_validation_rejects_bad_rules_and_references(self):
        invalid=[{'hot_orders':{'services':[{'id':'x','name':''}]}},
                 {'hot_orders':{'services':[{'id':'x','name':'A'},{'id':'x','name':'B'}]}},
                 {'hot_orders':{'claim_roles':['superuser']}},
                 {'hot_orders':{'claim_roles':None}},
                 {'hot_orders':{'create_roles':'master'}},
                 {'members':{'20':{'hot_order_create':'true'}}},
                 {'members':{'20':{'hot_order_services':'repair'}}},
                 {'hot_orders':{'services':[]},'members':{'20':{'hot_order_services':['foreign']}}}]
        for rules in invalid:
            with self.subTest(rules=rules),self.assertRaises(ValueError):validate_workflow_patch([],[],rules)
        validate_workflow_patch([],[],person()['workflow']['policies'])

    def test_existing_workflow_without_new_fields_remains_valid(self):
        validate_workflow_patch([],[],{'hot_orders':{'existing_custom_key':42},'members':{'20':{}}})
