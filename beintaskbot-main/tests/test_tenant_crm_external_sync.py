import unittest
import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock
from tenant_crm_sync_import import _when, import_contacts, import_deals
from tenant_crm_sync_outbox import _conflicts
from tenant_crm_sync_policy import _capable, _owner, FIELDS, SyncPolicyError

class ExternalSyncSafetyTests(unittest.TestCase):
    def test_provider_capabilities_are_conservative(self):
        self.assertTrue(_capable('kommo','contact','bidirectional'))
        self.assertTrue(_capable('kommo','deal','from_external'))
        self.assertTrue(_capable('beinsystems','deal','to_external'))
        self.assertFalse(_capable('beinsystems','deal','bidirectional'))
        self.assertFalse(_capable('beinsystems','contact','to_external'))
        self.assertNotIn(('beinsystems','contact'),FIELDS)

    def test_only_active_owner_can_change_sync_settings(self):
        tenant='12345678-1234-4234-8234-123456789012'
        self.assertEqual(_owner({'role':'owner','tenant_id':tenant,'telegram_id':7}), (tenant,7))
        for role in ('admin','manager','viewer'):
            with self.assertRaises(SyncPolicyError):
                _owner({'role':role,'tenant_id':tenant,'telegram_id':7})
        with self.assertRaises(SyncPolicyError):
            _owner({'role':'owner','active':False,'tenant_id':tenant,'telegram_id':7})

    def test_unknown_or_invalid_version_is_never_treated_as_timestamp(self):
        self.assertIsNotNone(_when(1792411200))
        self.assertIsNone(_when('not-a-date'))
        self.assertIsNone(_when(None))

    def test_import_is_noop_without_explicit_policy(self):
        cur=Mock();cur.fetchone.return_value=None
        self.assertEqual(import_contacts(cur,'tenant', [{'id':1}]),0)
        cur=Mock();cur.fetchone.return_value=None
        self.assertEqual(import_deals(cur,'tenant', [{'id':1}]),0)

    def test_outbound_update_fails_closed_if_external_version_is_missing_or_newer(self):
        row={'tenant_id':'tenant','provider':'kommo','entity_type':'deal'}
        link={'external_id':'5','last_synced_at':datetime(2026,1,1,tzinfo=timezone.utc)}
        async def missing(*args,**kwargs): return {'_embedded':{'leads':[{'id':5}]}}
        async def newer(*args,**kwargs): return {'_embedded':{'leads':[{'id':5,'updated_at':1790000000}]}}
        async def old(*args,**kwargs): return {'_embedded':{'leads':[{'id':5,'updated_at':1760000000}]}}
        self.assertTrue(asyncio.run(_conflicts(row,{},link,missing,None)))
        self.assertTrue(asyncio.run(_conflicts(row,{},link,newer,None)))
        self.assertFalse(asyncio.run(_conflicts(row,{},link,old,None)))

if __name__=='__main__': unittest.main()
