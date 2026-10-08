"""Incremental watermarks and additive migration without production access."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, MagicMock

from tenant_crm_sync_windows import choose_window, window_params
from tenant_crm_sync_incremental_schema import migrate_incremental_sync
import test_tenant_crm_sync as base


class WindowTests(unittest.TestCase):
    def test_first_run_reads_all_before_fixed_upper_bound(self):
        self.assertEqual(choose_window(None,100000), (0,99970,0,0))

    def test_next_run_overlaps_five_minutes_without_advancing_watermark(self):
        result = choose_window({'watermark':99000,'last_full_sync':90000},100000)
        self.assertEqual(result, (98700,99970,99000,90000))

    def test_daily_full_repair_and_forced_full(self):
        previous = {'watermark':99000,'last_full_sync':1000}
        self.assertEqual(choose_window(previous,100000).start,0)
        previous['last_full_sync']=90000
        self.assertEqual(choose_window(previous,100000,force_full=True).start,0)

    def test_old_jobs_and_clock_rollback_require_full_repair(self):
        self.assertEqual(window_params({'next_page':2}),{})
        self.assertEqual(choose_window({'watermark':110000,'last_full_sync':90000},100000).start,0)
        self.assertEqual(choose_window({'watermark':99000},100000).start,0)

    def test_params_remain_fixed_for_every_page_and_resource(self):
        row = {'window_from':100,'window_to':200,'next_page':1}
        expected = {'filter[updated_at][from]':100,'filter[updated_at][to]':200}
        self.assertEqual(window_params(row),expected)
        self.assertEqual(window_params({**row,'next_page':10}),expected)
        self.assertEqual(window_params({'window_from':0,'window_to':200}), {'filter[updated_at][to]':200})

    def test_migration_only_adds_fields_to_existing_jobs(self):
        cur = MagicMock(); migrate_incremental_sync(cur)
        self.assertEqual(cur.execute.call_count,4)
        self.assertTrue(all('ADD COLUMN IF NOT EXISTS' in call.args[0] for call in cur.execute.call_args_list))


class IncrementalStorageTests(unittest.TestCase):
    setUp = base.StorageTests.setUp
    store = base.StorageTests.store
    current = base.StorageTests.current
    def test_new_connection_discards_old_account_watermark(self):
        self.cur.fetchone.side_effect = [{'account_domain':'new.kommo.com','connected_at':'new'},
            {**self.row,'status':'done','watermark':9999999999,'last_full_sync':9999999999},
            {'resource':'leads','status':'queued'}]
        self.module.enqueue('company-a','leads')
        params = self.cur.execute.call_args.args[1]
        self.assertEqual(params[4],0)
        self.assertEqual(params[6:],(0,0))

    def test_page_and_failure_never_advance_watermark_to_partial_run(self):
        self.current(); self.store().save_page([{'id':7}],done=False)
        sql, params = self.cur.execute.call_args.args
        self.assertIn('watermark=CASE WHEN %s',sql)
        self.assertEqual(params[3:6],(False,False,False))
        self.cur.reset_mock(); self.store().fail()
        self.assertNotIn('watermark=',self.cur.execute.call_args.args[0])

    def test_manual_full_can_restart_failed_cursor(self):
        self.cur.fetchone.side_effect = [{'account_domain':'example.kommo.com','connected_at':'connected'},
            {**self.row,'status':'failed','watermark':100,'last_full_sync':100},
            {'resource':'leads','status':'queued'}]
        self.module.enqueue('company-a','leads',force_full=True)
        sql, params = self.cur.execute.call_args.args
        self.assertIn('next_page=1',sql); self.assertEqual(params[4],0)


class IncrementalWorkerTests(unittest.IsolatedAsyncioTestCase):
    setUp = base.WorkerTests.setUp
    async def test_reads_fixed_delta_window_and_resumes_same_window(self):
        self.store.row.update(window_from=100,window_to=200)
        request = AsyncMock(return_value={})
        await self.worker.sync_one_page(self.store,request)
        self.assertEqual(request.await_args.kwargs['params']['filter[updated_at][from]'],100)
        self.assertEqual(request.await_args.kwargs['params']['filter[updated_at][to]'],200)


class FullRepairApiTests(unittest.IsolatedAsyncioTestCase):
    setUp = base.ApiTests.setUp
    async def test_employee_cannot_force_full_repair(self):
        self.ns['TenantPlatformError'] = RuntimeError
        with self.assertRaises(RuntimeError):
            await self.ns['_sync_tenant_crm'](self.profile,include_tasks=True,force_full=True)
        self.queue.assert_not_called()

    async def test_admin_can_force_full_repair(self):
        self.profile.update(role='admin',permissions=['tasks'],modules={'tasks':True})
        await self.ns['_sync_tenant_crm'](self.profile,include_tasks=True,force_full=True)
        self.queue.assert_called_once_with('company-a','tasks',force_full=True)


if __name__ == '__main__': unittest.main()
