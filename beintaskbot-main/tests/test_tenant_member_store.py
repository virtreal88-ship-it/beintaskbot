import unittest
from unittest.mock import patch

import tenant_member_store


class Cursor:
    def __init__(self):
        self.rowcount = 1
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def __enter__(self): return self
    def __exit__(self, *_args): return False

    def fetchone(self):
        return {"tenant_id": "tenant-a", "telegram_id": 22, "display_name": "Operator",
                "role": "operator", "permissions": ["tasks"], "active": True}


class Connection:
    def __init__(self):
        self.cursor_value = Cursor()
        self.committed = False

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def cursor(self): return self.cursor_value
    def commit(self): self.committed = True


class MemberStoreTests(unittest.TestCase):
    def test_permission_payload_distinguishes_explicit_effective_and_legacy(self):
        value=tenant_member_store._member_payload({'tenant_id':'tenant-a','telegram_id':22,
            'role':'worker','permissions':['tasks','support','legacy:retired'],'active':True,
            'modules':{'tasks':True}})
        self.assertEqual(value['explicit_permissions'],['tasks','support','legacy:retired'])
        self.assertEqual(value['effective_permissions'],['tasks','support'])
        self.assertEqual(value['legacy_permissions'],['legacy:retired'])

    def test_effective_permissions_uses_member_workflow_policy(self):
        value=tenant_member_store._member_payload({'tenant_id':'tenant-a','telegram_id':22,
            'role':'admin','permissions':['dialogs'],'active':True,'modules':{},
            'workflow':{'policies':[{'policy_key':'members','value':{'22':{'ai_agents_manage':True}}}]}})
        self.assertEqual(value['explicit_permissions'],['dialogs'])
        self.assertEqual(value['effective_permissions'],['dialogs'])

    def test_edit_preserves_hidden_known_and_unknown_grants_and_legacy_role(self):
        existing={'tenant_id':'tenant-a','telegram_id':22,'display_name':'Legacy','role':'legacy_role',
                  'permissions':['tasks','support','employees','finance','legacy:retired'],'active':True}
        # This is the exact state sent by the visible checkbox editor: it only
        # knows its five checkboxes, while historical grants must survive.
        connection = Connection()
        owner={'tenant_id':'tenant-a','telegram_id':1,'role':'owner','active':True}
        with patch.object(tenant_member_store, 'member', side_effect=[owner, existing]), \
             patch.object(tenant_member_store, '_connect', return_value=connection), \
             patch.object(tenant_member_store, '_ensure_schema'):
            tenant_member_store.upsert_member(tenant_id='tenant-a', owner_id=1, telegram_id=22,
                display_name='Legacy', role='legacy_role', permissions=['tasks','support'])
        params=connection.cursor_value.queries[0][1]
        self.assertEqual(params[3], 'legacy_role')
        self.assertEqual(set(__import__('json').loads(params[4])), {'tasks','support','employees','finance','legacy:retired'})

    def test_whatsapp_number_normalization_is_exact_and_tenant_input_safe(self):
        normalize=tenant_member_store._whatsapp_digits
        self.assertEqual(normalize('+994 50 123 45 67'),'994501234567')
        self.assertEqual(normalize('994501234567'),'994501234567')
        self.assertEqual(normalize('0501234567'),'994501234567')
        self.assertEqual(normalize('501234567'),'')
        self.assertEqual(normalize('https://wa.me/994501234567'),'')
        self.assertEqual(normalize('99450+1234567'),'')
        self.assertEqual(normalize('++994501234567'),'')

    def test_upsert_creates_member_and_audits_without_overwriting_owner(self):
        connection = Connection()
        owner = {"tenant_id": "tenant-a", "telegram_id": 1, "role": "owner", "active": True}
        with patch.object(tenant_member_store, "member", side_effect=[owner, None]), \
             patch.object(tenant_member_store, "_connect", return_value=connection), \
             patch.object(tenant_member_store, "_ensure_schema"):
            result = tenant_member_store.upsert_member(tenant_id="tenant-a", owner_id=1,
                telegram_id=22, display_name="Operator", role="operator", permissions=["tasks"])
        self.assertEqual(result["telegram_id"], 22)
        self.assertTrue(connection.committed)
        self.assertEqual(len(connection.cursor_value.queries), 2)
        self.assertIn("employee.created", connection.cursor_value.queries[1][1])
        self.assertIn("role = CASE WHEN saas_tenant_members.role = 'owner' THEN 'owner'", connection.cursor_value.queries[0][0])


if __name__ == "__main__":
    unittest.main()
