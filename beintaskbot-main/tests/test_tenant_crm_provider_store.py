"""Provider-neutral CRM snapshot store contracts."""
import json
import unittest
from unittest.mock import MagicMock, Mock

from tenant_crm_identity import crm_identity
from tenant_crm_provider_store import ProviderSnapshotStore


class ProviderIdentityTests(unittest.TestCase):
    def test_keeps_uuid_external_ids_as_text(self):
        self.assertEqual(crm_identity("BEINSystems", "Deals", "550e8400-e29b-41d4-a716-446655440000"),
                         ("beinsystems", "deals", "550e8400-e29b-41d4-a716-446655440000"))

    def test_rejects_unsafe_identity_parts(self):
        for value in ("", "bad provider", "<script>"):
            with self.assertRaises(ValueError):
                crm_identity(value, "deals", "1")
        with self.assertRaises(ValueError):
            crm_identity("kommo", "deals", "")


class ProviderSnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.connect = MagicMock()
        self.conn = self.connect.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.schema = Mock()
        self.store = ProviderSnapshotStore(self.connect, self.schema, serialize=json.dumps)

    def test_upsert_accepts_uuid_and_commits_one_batch(self):
        entities = [{"id": "550e8400-e29b-41d4-a716-446655440000", "data": {"name": "Deal"}}]
        self.assertEqual(self.store.upsert(tenant_id="tenant", provider="beinsystems", entity_type="deals", entities=entities), 1)
        sql, rows = self.cur.executemany.call_args.args
        self.assertIn("saas_crm_provider_entities", sql)
        self.assertEqual(rows[0][3], "550e8400-e29b-41d4-a716-446655440000")
        self.schema.assert_called_once_with(self.conn)
        self.conn.commit.assert_called_once_with()

    def test_invalid_row_never_opens_connection(self):
        with self.assertRaises(ValueError):
            self.store.upsert(tenant_id="tenant", provider="beinsystems", entity_type="deals",
                              entities=[{"id": "", "data": {}}])
        self.connect.assert_not_called()

    def test_parent_identity_is_all_or_nothing(self):
        with self.assertRaises(ValueError):
            self.store.list(tenant_id="tenant", provider="beinsystems", entity_type="tasks",
                            parent_entity_type="deals")
        self.connect.assert_not_called()

    def test_list_bounds_page_size(self):
        self.cur.fetchall.return_value = []
        self.store.list(tenant_id="tenant", provider="beinsystems", entity_type="deals", limit=1000, offset=-1)
        _, params = self.cur.execute.call_args.args
        self.assertEqual(params[-2:], [200, 0])


if __name__ == "__main__":
    unittest.main()
