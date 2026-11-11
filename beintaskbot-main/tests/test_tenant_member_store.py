import unittest
from datetime import datetime, timezone
import json
from unittest.mock import patch

import tenant_member_store


class Cursor:
    def __init__(self, existing=None):
        self.rowcount = 1
        self.queries = []
        self.existing = existing
        self.owner = {'tenant_id':'tenant-a','telegram_id':1,'role':'owner','active':True}

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def __enter__(self): return self
    def __exit__(self, *_args): return False

    def fetchone(self):
        query, params = self.queries[-1]
        if 'AS ready' in query: return {'ready': True}
        if 'SELECT id,modules' in query: return {'id':'tenant-a','modules':{'tasks':True}}
        if 'INSERT INTO saas_tenant_members' in query:
            _, person, name, role, permissions, _, department = params
        elif 'UPDATE saas_tenant_members SET display_name' in query:
            name, role, permissions, _, person = params
        else: raise AssertionError(query)
        return {'tenant_id':'tenant-a','telegram_id':person,'display_name':name,'role':role,
                'permissions':json.loads(permissions),'active':True,
                'updated_at':datetime(2026,11,2,tzinfo=timezone.utc)}

    def fetchall(self):
        query, _ = self.queries[-1]
        if 'SELECT * FROM saas_tenant_members' in query:
            return [self.owner] + ([self.existing] if self.existing else [])
        return []


class Connection:
    def __init__(self, existing=None):
        self.cursor_value = Cursor(existing)
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
        connection = Connection(existing)
        with patch.object(tenant_member_store, '_connect', return_value=connection):
            tenant_member_store.upsert_member(tenant_id='tenant-a', owner_id=1, telegram_id=22,
                display_name='Legacy', role='legacy_role', permissions=['tasks','support'])
        params=next(params for query,params in connection.cursor_value.queries
                    if 'UPDATE saas_tenant_members SET display_name' in query)
        self.assertEqual(params[1], 'legacy_role')
        self.assertEqual(set(json.loads(params[2])), {'tasks','support','employees','finance','legacy:retired'})

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
        with patch.object(tenant_member_store, "_connect", return_value=connection):
            result = tenant_member_store.upsert_member(tenant_id="tenant-a", owner_id=1,
                telegram_id=22, display_name="Operator", role="operator", permissions=["tasks"])
        self.assertEqual(result["telegram_id"], 22)
        self.assertTrue(connection.committed)
        queries=connection.cursor_value.queries
        self.assertTrue(any('employee.created' in params for _,params in queries))
        insert=next(query for query,_ in queries if 'INSERT INTO saas_tenant_members' in query)
        self.assertIn('DO NOTHING',insert)
        self.assertTrue(any('ORDER BY telegram_id FOR UPDATE' in query for query,_ in queries))
        self.assertFalse(any('ALTER ' in query or 'CREATE ' in query for query,_ in queries))


if __name__ == "__main__":
    unittest.main()
