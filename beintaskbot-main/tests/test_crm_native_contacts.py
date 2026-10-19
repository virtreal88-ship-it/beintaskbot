"""Targeted native contact CRUD tests; no production or Kommo calls."""
import unittest
from unittest import mock
import tenant_crm_native_contacts as contacts


class NativeContactsTests(unittest.TestCase):
    def test_validation_rejects_empty_and_invalid_ids(self):
        with self.assertRaises(ValueError):
            contacts._fields('  ')
        with self.assertRaises(ValueError):
            contacts._valid_id('123')
        self.assertEqual(contacts._fields(' A ', ' 123 ', ' a@b.test '),
                         ('A', '123', 'a@b.test'))

    def test_create_is_tenant_scoped_and_idempotent(self):
        connection = mock.MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {'id': 'uuid', 'display_name': 'A', 'phone': '', 'email': ''}
        with mock.patch.object(contacts, '_connect', return_value=connection), \
             mock.patch.object(contacts, '_ensure_schema'):
            row = contacts.create_contact('tenant-1', 'A',
                contact_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        self.assertEqual(row['display_name'], 'A')
        sql, args = cursor.execute.call_args.args
        self.assertIn('ON CONFLICT (id) DO NOTHING', sql)
        self.assertEqual(args[1], 'tenant-1')
        connection.__enter__.return_value.commit.assert_called_once()

    def test_update_archive_and_read_are_tenant_scoped(self):
        connection = mock.MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        with mock.patch.object(contacts, '_connect', return_value=connection), \
             mock.patch.object(contacts, '_ensure_schema'):
            ident = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            contacts.update_contact('tenant-1', ident, 'B')
            sql, args = cursor.execute.call_args.args
            self.assertIn('tenant_id=%s::uuid', sql)
            self.assertEqual(args[3], 'tenant-1')
            contacts.archive_contact('tenant-1', ident)
            sql, args = cursor.execute.call_args.args
            self.assertIn('archived_at=now()', sql)
            self.assertEqual(args[0], 'tenant-1')
            contacts.get_contact('tenant-1', ident)
            sql, args = cursor.execute.call_args.args
            self.assertIn('archived_at IS NULL', sql)
            self.assertEqual(args[0], 'tenant-1')


if __name__ == '__main__':
    unittest.main()
