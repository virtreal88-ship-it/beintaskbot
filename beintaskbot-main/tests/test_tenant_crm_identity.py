import unittest
from tenant_crm_identity import (CRMIdentityConflict, read_link, record_link,
                                 set_entity_mode)
from tenant_crm_identity_schema import migrate_crm_identity_schema
from tenant_platform_schema_crm import migrate_crm_schema

TENANT = '00000000-0000-0000-0000-000000000101'
LOCAL = '00000000-0000-0000-0000-000000000201'


class Cursor:
    def __init__(self, rows=None, one=None):
        self.rows = list(rows or [])
        self.one = one
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


class CRMIdentityTests(unittest.TestCase):
    def test_link_is_tenant_scoped_idempotent_and_records_sync_origin(self):
        cur = Cursor(rows=[{'internal_id': LOCAL, 'external_id': 'abc-42'}],
                     one={'tenant_id': TENANT, 'provider': 'beinsystems', 'entity_type': 'deal',
                          'internal_id': LOCAL, 'external_id': 'abc-42', 'sync_state': 'synced',
                          'last_synced_at': None, 'last_origin_id': 'event-1'})
        result = record_link(cur, tenant_id=TENANT, provider='beinsystems', entity_type='deal',
                             internal_id=LOCAL, external_id='abc-42', sync_state='synced', origin_id='event-1')
        self.assertEqual(result['external_id'], 'abc-42')
        self.assertIn('tenant_id=%s::uuid', cur.executed[0][0])
        self.assertIn('ON CONFLICT(tenant_id,provider,entity_type,internal_id)', cur.executed[1][0])
        self.assertEqual(cur.executed[0][1], (TENANT, 'beinsystems', 'deal', LOCAL, 'abc-42'))

    def test_link_cannot_reassign_external_id(self):
        cur = Cursor(rows=[{'internal_id': '00000000-0000-0000-0000-000000000202', 'external_id': 'abc-42'}])
        with self.assertRaises(CRMIdentityConflict):
            record_link(cur, tenant_id=TENANT, provider='kommo', entity_type='contact',
                        internal_id=LOCAL, external_id='abc-42')
        self.assertEqual(len(cur.executed), 1)

    def test_reads_are_scoped_and_mode_policy_is_explicit(self):
        cur = Cursor(one={'tenant_id': TENANT, 'provider': 'kommo', 'entity_type': 'contact',
                          'internal_id': LOCAL, 'external_id': '19', 'sync_state': 'synced'})
        self.assertEqual(read_link(cur, tenant_id=TENANT, provider='kommo',
                                   entity_type='contact', internal_id=LOCAL)['external_id'], '19')
        self.assertEqual(cur.executed[0][1], (TENANT, 'kommo', 'contact', LOCAL))
        with self.assertRaises(ValueError):
            set_entity_mode(cur, tenant_id=TENANT, entity_type='deal', mode='beintaskbot',
                            primary_provider='kommo')

    def test_schema_has_tenant_unique_links_and_per_entity_modes(self):
        cur = Cursor()
        migrate_crm_identity_schema(cur)
        sql = '\n'.join(item[0] for item in cur.executed)
        self.assertIn('saas_crm_provider_settings', sql)
        self.assertIn('saas_crm_entity_links', sql)
        self.assertIn('UNIQUE(tenant_id,provider,entity_type,external_id)', sql)
        self.assertIn('PRIMARY KEY(tenant_id,entity_type)', sql)
        self.assertIn('last_synced_at TIMESTAMPTZ NULL', sql)

    def test_crm_migration_installs_identity_schema(self):
        cur = Cursor()
        migrate_crm_schema(cur)
        sql = '\n'.join(item[0] for item in cur.executed)
        self.assertIn('CREATE TABLE IF NOT EXISTS saas_crm_entity_links', sql)
        self.assertIn('CREATE TABLE IF NOT EXISTS saas_crm_provider_settings', sql)


if __name__ == '__main__':
    unittest.main()
