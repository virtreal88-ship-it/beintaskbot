"""Additive provider-neutral CRM schema checks."""
import unittest

from tenant_platform_schema_crm import migrate_crm_schema


class Cursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)


class ProviderSchemaTests(unittest.TestCase):
    def test_common_plane_is_additive_and_backfills_kommo_without_overwrite(self):
        cursor = Cursor()
        migrate_crm_schema(cursor)
        source = "\n".join(cursor.sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS saas_crm_provider_entities", source)
        self.assertIn("kommo_lead_id::text", source)
        self.assertIn("kommo_task_id::text", source)
        self.assertIn("ON CONFLICT (tenant_id, provider, entity_type, external_id) DO NOTHING", source)
        self.assertNotIn("DROP TABLE", source.upper())


if __name__ == "__main__":
    unittest.main()
