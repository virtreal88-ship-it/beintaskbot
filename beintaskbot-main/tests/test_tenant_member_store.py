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
