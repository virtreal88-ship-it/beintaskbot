"""Native deal safety tests; no database/provider access."""
import unittest
from unittest import mock
import tenant_crm_native_deals as deals


class NativeDealTests(unittest.TestCase):
    def test_fields_validate_identifiers_and_conversation_pair(self):
        with self.assertRaises(ValueError): deals._fields('', 'p', 's')
        with self.assertRaises(ValueError): deals._fields('Deal', 'p', 's', channel_id='bad')
        values=deals._fields(' Deal ','pipe','stage',contact_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',responsible_id='7')
        self.assertEqual(values[:3],('Deal','pipe','stage'))
        self.assertEqual(values[4],7)

    def test_create_validates_tenant_relations_before_insert(self):
        connection=mock.MagicMock(); cur=connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[{'ok':1},None]
        with mock.patch.object(deals,'_connect',return_value=connection),mock.patch.object(deals,'_ensure_schema'):
            with self.assertRaisesRegex(ValueError,'contact unavailable'):
                deals.create_deal('tenant-a','Deal','pipe','stage',contact_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        self.assertEqual(cur.execute.call_count,2)
        self.assertNotIn('INSERT INTO saas_crm_native_deals',cur.execute.call_args.args[0])

    def test_update_and_archive_are_tenant_scoped(self):
        connection=mock.MagicMock(); cur=connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        with mock.patch.object(deals,'_connect',return_value=connection),mock.patch.object(deals,'_ensure_schema'),mock.patch.object(deals,'_related'):
            deals.update_deal('tenant-a','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','D','p','s')
            sql,args=cur.execute.call_args.args
            self.assertIn('tenant_id=%s::uuid',sql); self.assertEqual(args[-2:][0],'tenant-a')
            deals.archive_deal('tenant-a','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
            sql,args=cur.execute.call_args.args
            self.assertIn('archived_at=now()',sql); self.assertEqual(args[0],'tenant-a')


if __name__=='__main__': unittest.main()
