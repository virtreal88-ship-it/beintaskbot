"""Explicit provider deletion evidence, durable phases and history retention."""
import ast
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock

import test_tenant_crm_sync as fixtures
from tenant_crm_sync_data import deleted_page, normalize_page
from tenant_crm_deletion_schema import migrate_crm_deletions, migrate_deletion_scan


class DeletionTests(unittest.TestCase):
    def setUp(self):
        fixtures.StorageTests.setUp(self)

    def store(self, phase='active'):
        return self.module.SyncPageStore(self.conn, {**self.row, 'phase':phase})

    def current(self):
        fixtures.StorageTests.current(self)

    def test_only_explicit_deleted_flag_and_valid_timestamp_are_accepted(self):
        for item in ({'id':1,'updated_at':123}, {'id':1,'is_deleted':False,'updated_at':123},
                     {'id':1,'is_deleted':True}, {'id':0,'is_deleted':True,'updated_at':123}):
            with self.assertRaises(ValueError): deleted_page([item])
        self.assertEqual(deleted_page([{'id':7,'is_deleted':True,'updated_at':123}])[0]['kommo_lead_id'],7)

    def test_end_of_active_scan_starts_deleted_phase_without_success_watermark(self):
        self.current();self.store().save_page([],done=True)
        calls=self.cur.execute.call_args_list
        checkpoint=next(call for call in calls if 'watermark=CASE' in call.args[0])
        self.assertEqual(checkpoint.args[1][0],'queued')
        self.assertEqual(checkpoint.args[1][3:6],(False,False,False))
        self.assertIn("phase='deleted',next_page=1,last_record_id=0",calls[-1].args[0])
        self.conn.commit.assert_called_once()

    def test_delete_preserves_history_and_newer_provider_snapshot(self):
        self.current();self.store('deleted').save_page(deleted_page([
            {'id':7,'is_deleted':True,'updated_at':123}]),done=True)
        sql,rows=self.cur.executemany.call_args.args
        self.assertEqual(rows[0][:2],('company-a',7))
        self.assertIn('source_updated_at<=EXCLUDED.deleted_at',sql)
        conflict=sql.split('DO UPDATE SET')[1]
        self.assertNotIn('raw=',conflict);self.assertNotIn('pipeline_id=',conflict)
        self.assertFalse(any('DELETE FROM' in call.args[0] for call in self.cur.execute.call_args_list))
        checkpoint=next(call for call in self.cur.execute.call_args_list if 'watermark=CASE' in call.args[0])
        self.assertEqual(checkpoint.args[1][0],'done')

    def test_restore_requires_newer_source_version(self):
        self.current();self.store().save_page([{'id':7}],done=False)
        sql=self.cur.executemany.call_args.args[0]
        self.assertIn('deleted_at=NULL',sql)
        self.assertIn('EXCLUDED.source_updated_at>saas_crm_deals.deleted_at',sql)

    def test_deleted_record_cannot_be_interpreted_as_an_active_restore(self):
        with self.assertRaises(ValueError):
            normalize_page('leads',[{'id':7,'is_deleted':True,'updated_at':123}],{})

    def test_migrations_only_add_metadata(self):
        cur=Mock();migrate_crm_deletions(cur);migrate_deletion_scan(cur)
        self.assertTrue(all('ADD COLUMN IF NOT EXISTS' in call.args[0] for call in cur.execute.call_args_list))

    def test_all_current_read_paths_hide_confirmed_deleted_leads_and_linked_tasks(self):
        source=ast.parse((fixtures.ROOT/'tenant_platform.py').read_text(encoding='utf-8-sig'))
        names={'list_crm_deals','get_crm_deal','list_crm_tasks','get_crm_task'}
        tree=ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
        connect=MagicMock();conn=connect.return_value.__enter__.return_value
        cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value=None;cur.fetchall.return_value=[]
        ns={'_connect':connect,'_ensure_schema':Mock(),'_public_crm_row':lambda row:row}
        exec(compile(tree,'<deletion-reads>','exec'),ns)
        for name in names:
            cur.execute.reset_mock()
            args={'tenant_id':'company-a'}
            if name=='get_crm_deal':args['kommo_lead_id']=7
            if name=='get_crm_task':args['kommo_task_id']=8
            ns[name](**args)
            self.assertTrue(all('deleted_at IS' in call.args[0] for call in cur.execute.call_args_list))
            self.assertTrue(all('company-a' in call.args[1] for call in cur.execute.call_args_list))


class DeletionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleted_phase_skips_catalog_and_requests_explicit_deleted_records(self):
        worker=fixtures.load_module('tenant_crm_sync_worker', {'SyncPageStore':Mock()}, {'tenant_crm_sync_store'})
        store=Mock();store.row={'tenant_id':'company-a','resource':'leads','phase':'deleted','next_page':1,
                              'window_from':100,'window_to':200}
        store.connection_is_current.return_value=True
        request=AsyncMock(return_value={'_embedded':{'leads':[{'id':7,'updated_at':123,'is_deleted':True}]}})
        await worker.sync_one_page(store,request)
        request.assert_awaited_once()
        self.assertEqual(request.await_args.args,('company-a','GET','leads'))
        self.assertEqual(request.await_args.kwargs['params']['with'],'only_deleted')
        self.assertEqual(request.await_args.kwargs['params']['filter[updated_at][from]'],100)
        store.save_page.assert_called_once_with(deleted_page([{'id':7,'updated_at':123,'is_deleted':True}]),done=True)
