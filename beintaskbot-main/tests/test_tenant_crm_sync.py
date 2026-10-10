"""Durable read pagination, isolation and restart tests without live services."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import unittest
import uuid
from threading import Event
from unittest.mock import Mock, MagicMock, AsyncMock

from tenant_crm_snapshots import deal_rows, task_rows
from tenant_crm_sync_data import PAGE_SIZE, normalize_page, page_items
from tenant_policy import TenantPolicy

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, injected, excluded):
    tree = ast.parse((ROOT / (name + '.py')).read_text(encoding='utf-8-sig'))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or node.module not in excluded]
    ns = dict(injected)
    exec(compile(tree, name, 'exec'), ns)
    return SimpleNamespace(**ns)


class PageTests(unittest.TestCase):
    def test_204_and_empty_embedded_page(self):
        self.assertEqual(page_items({}, 'leads'), [])
        self.assertEqual(page_items({'_embedded': {'tasks': []}}, 'tasks'), [])

    def test_invalid_response_is_not_silently_treated_as_end(self):
        for payload in ({'error': 'oops'}, {'_embedded': {'tasks': {}}},
                        {'_embedded': {'tasks': [None]}}, {'_embedded': {'tasks': [{}] * 251}}):
            with self.assertRaises(ValueError): page_items(payload, 'tasks')

    def test_task_marker_and_completion_survive_normalization(self):
        task = {'id': 9, 'entity_type': 'leads', 'entity_id': 7, 'text': 'Call\n[CRM:abc]',
                'responsible_user_id': 99, 'complete_till': 1900000000, 'is_completed': True}
        row = normalize_page('tasks', [task], {})[0]
        self.assertEqual(row['text'], task['text']); self.assertTrue(row['completed'])
        self.assertEqual(row['kommo_lead_id'], 7)
        self.assertEqual(row['raw'], task)

    def test_moved_deal_keeps_actual_provider_pipeline_even_if_unselected(self):
        lead = {'id': 7, 'pipeline_id': 999, 'status_id': 142, 'updated_at': 1900000000,
                '_embedded': {'contacts': [{'name': 'Client', 'custom_fields_values': [
                    {'field_code': 'PHONE', 'values': [{'value': '+994123'}]}]}]}}
        row = normalize_page('leads', [lead], {142:'Done'})[0]
        self.assertEqual(row['pipeline_id'], 999); self.assertEqual(row['status_id'], 142)
        self.assertEqual(row['phone'], '+994123'); self.assertEqual(row['contact_name'], 'Client')


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SimpleNamespace(row={'tenant_id':'company-a', 'resource':'tasks', 'next_page':3},
            connection_is_current=Mock(return_value=True), save_page=Mock(), fail=Mock(), close=Mock())
        self.claim = Mock()
        self.worker = load_module('tenant_crm_sync_worker', {'SyncPageStore':SimpleNamespace(claim=self.claim)},
                                  {'tenant_crm_sync_store'})

    async def test_full_page_advances_then_restart_uses_persisted_next_page(self):
        provider = AsyncMock(return_value={'_embedded': {'tasks':[{'id':i} for i in range(1,251)]}})
        await self.worker.sync_one_page(self.store, provider)
        self.assertEqual(provider.await_args.args, ('company-a','GET','tasks'))
        self.assertEqual(provider.await_args.kwargs['params'], {'limit':250,'page':3,'order[id]':'asc'})
        self.assertFalse(self.store.save_page.call_args.kwargs['done'])
        self.store.row['next_page'] = 4
        provider.return_value = {}
        await self.worker.sync_one_page(self.store, provider)
        self.assertEqual(provider.await_args.kwargs['params']['page'], 4)
        self.store.save_page.assert_called_with([], done=True)

    async def test_only_get_requests_and_server_owned_paths_are_used(self):
        self.store.row['resource'] = 'leads'
        provider = AsyncMock(side_effect=[{'_embedded': {'pipelines':[
            {'_embedded': {'statuses':[{'id':100,'name':'New'}]}}]}},
            {'_embedded': {'leads':[{'id':7,'pipeline_id':10,'status_id':100}]},
             '_links': {'next': {'href':'https://attacker.invalid/token'}}}])
        await self.worker.sync_one_page(self.store, provider)
        self.assertTrue(all(call.args[1]=='GET' for call in provider.await_args_list))
        self.assertEqual(provider.await_args.args[2], 'leads')
        self.assertTrue(self.store.save_page.call_args.kwargs['done'])

    async def test_disabled_or_replaced_integration_stops_before_provider_request(self):
        self.store.connection_is_current.return_value = False
        provider = AsyncMock()
        await self.worker.sync_one_page(self.store, provider)
        provider.assert_not_awaited(); self.store.save_page.assert_not_called()
        self.store.fail.assert_called_once_with(permanent=True)

    async def test_failed_read_preserves_cursor_and_closes_connection(self):
        self.claim.side_effect = [self.store, None]
        await self.worker.run_sync_batch(AsyncMock(side_effect=TimeoutError()), Mock())
        self.store.fail.assert_called_once_with(); self.store.save_page.assert_not_called()
        self.store.close.assert_called_once()
        self.assertEqual(self.store.row['next_page'], 3)

    async def test_tick_is_bounded_to_four_pages(self):
        self.claim.return_value = self.store
        provider = AsyncMock(return_value={})
        await self.worker.run_sync_batch(provider, Mock())
        self.assertEqual(self.claim.call_count, 4)
        self.assertEqual(self.store.close.call_count, 4)

    async def test_repeated_or_unsorted_page_never_advances_cursor(self):
        self.store.row['last_record_id'] = 8
        for ids in ([8,9], [10,9], [9,9]):
            provider = AsyncMock(return_value={'_embedded':{'tasks':[{'id':i} for i in ids]}})
            with self.assertRaises(ValueError): await self.worker.sync_one_page(self.store, provider)
        self.store.save_page.assert_not_called()

    async def test_cancelled_commit_finishes_before_connection_is_closed(self):
        started, release = Event(), Event()
        order = []
        def save(*args, **kwargs):
            started.set()
            if not release.wait(3): raise RuntimeError('test timeout')
            order.append('commit')
        self.store.save_page = save
        self.store.close = lambda:order.append('close')
        self.claim.side_effect = [self.store, None]
        task = asyncio.create_task(self.worker.run_sync_batch(AsyncMock(return_value={}),Mock()))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        task.cancel(); release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(order, ['commit','close'])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.connect = MagicMock()
        self.conn = self.connect.return_value
        self.conn.__enter__.return_value = self.conn
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.module = load_module('tenant_crm_sync_store', {'_connect':self.connect,
            'ensure_sync_schema':Mock(), 'TenantPlatformError':RuntimeError},
            {'tenant_platform','tenant_crm_sync_schema'})
        self.row = {'tenant_id':'company-a','resource':'leads','run_id':'run-1', 'next_page':2,
                    'connection_id':'example.kommo.com:connected'}

    def store(self, resource='leads'):
        return self.module.SyncPageStore(self.conn, {**self.row,'resource':resource})

    def current(self):
        self.cur.fetchone.side_effect = [{'run_id':'run-1'},
            {'account_domain':'example.kommo.com','connected_at':'connected'}]

    def test_page_snapshot_and_cursor_share_one_commit(self):
        self.current()
        self.store().save_page([{'id':7,'pipeline_id':10,'status_id':100}], done=False)
        self.conn.commit.assert_called_once()
        sql, rows = self.cur.executemany.call_args.args
        self.assertEqual(rows[0][:4], ('company-a',7,10,100))
        update = self.cur.execute.call_args
        self.assertIn('next_page=next_page+1', update.args[0])
        self.assertEqual(update.args[1][-3:], ('company-a','leads','run-1'))
        conflict = sql.split('DO UPDATE SET')[1]
        self.assertNotIn('last_message=', conflict); self.assertNotIn('channel=', conflict)

    def test_database_failure_cannot_advance_cursor(self):
        self.current(); self.cur.executemany.side_effect = RuntimeError('write failed')
        with self.assertRaises(RuntimeError): self.store().save_page([{'id':7}], done=False)
        self.conn.commit.assert_not_called()
        self.assertFalse(any('next_page=next_page+1' in call.args[0] for call in self.cur.execute.call_args_list))

    def test_obsolete_run_cannot_write_snapshots(self):
        self.cur.fetchone.return_value = {'run_id':'new-run'}
        self.store().save_page([{'id':7}], done=True)
        self.cur.executemany.assert_not_called(); self.conn.rollback.assert_called_once()

    def test_replaced_integration_during_get_cannot_write_snapshots(self):
        self.cur.fetchone.side_effect = [{'run_id':'run-1'},
            {'account_domain':'other.kommo.com','connected_at':'new'}]
        self.store().save_page([{'id':7}], done=True)
        self.cur.executemany.assert_not_called()
        self.assertIn("status=CASE WHEN", self.cur.execute.call_args.args[0])
        self.assertTrue(self.cur.execute.call_args.args[1][0])

    def test_retry_is_bounded_and_never_changes_page(self):
        self.store().fail()
        sql, params = self.cur.execute.call_args.args
        self.assertIn('attempts>=2', sql); self.assertNotIn('next_page=', sql)
        self.assertEqual(params, (False,'company-a','leads','run-1'))

    def test_concurrent_enqueue_returns_existing_job_without_reset(self):
        self.cur.fetchone.side_effect = [{'account_domain':'example.kommo.com','connected_at':'connected'},
            {**self.row, 'status':'running','pages_done':3}]
        result = self.module.enqueue('company-a','leads')
        self.assertEqual(result, {'resource':'leads','status':'running','pages_done':3})
        self.assertFalse(any('INSERT INTO' in call.args[0] for call in self.cur.execute.call_args_list))

    def test_no_integration_no_enqueue(self):
        self.cur.fetchone.return_value = None
        with self.assertRaises(RuntimeError): self.module.enqueue('company-a','tasks')
        self.assertFalse(any('INSERT INTO' in call.args[0] for call in self.cur.execute.call_args_list))

    def test_public_status_never_contains_connection_or_ids_or_total_records(self):
        row = {**self.row, 'records_saved':99, 'status':'queued', 'pages_done':1}
        self.assertEqual(self.module.public_job(row), {'resource':'leads','status':'queued','pages_done':1})

    def test_claim_uses_session_lock_and_releases_on_close(self):
        self.cur.fetchall.return_value = [self.row]
        self.cur.fetchone.side_effect = [{'locked':True}, self.row]
        store = self.module.SyncPageStore.claim()
        self.assertEqual(store.row, self.row)
        self.assertTrue(any('pg_try_advisory_lock' in call.args[0] for call in self.cur.execute.call_args_list))
        self.conn.commit.assert_called_once()
        store.close(); self.conn.close.assert_called_once()

    def test_locked_job_is_not_stolen(self):
        self.cur.fetchall.return_value = [self.row]
        self.cur.fetchone.return_value = {'locked':False}
        self.assertIsNone(self.module.SyncPageStore.claim())
        self.conn.close.assert_called_once()
        self.assertFalse(any("SET status='running'" in call.args[0] for call in self.cur.execute.call_args_list))

    def test_failed_job_restarts_at_same_page_instead_of_page_one(self):
        self.cur.fetchone.side_effect = [{'account_domain':'example.kommo.com','connected_at':'connected'},
            {**self.row,'status':'failed'}, {**self.row,'status':'queued'}]
        self.module.enqueue('company-a','leads')
        update = self.cur.execute.call_args.args[0]
        self.assertIn("status='queued'", update)
        self.assertNotIn('next_page=1', update)


class SchemaTests(unittest.TestCase):
    def test_additive_schema_and_index_are_initialized_once_after_commit(self):
        module = load_module('tenant_crm_sync_schema', {'_ensure_schema':Mock()}, {'tenant_platform'})
        conn = MagicMock()
        module.ensure_sync_schema(conn); module.ensure_sync_schema(conn)
        conn.commit.assert_called_once()
        cur = conn.cursor.return_value.__enter__.return_value
        sql = ' '.join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn('CREATE TABLE IF NOT EXISTS saas_crm_sync_jobs', sql)
        self.assertIn('PRIMARY KEY (tenant_id, resource)', sql)
        self.assertIn('REFERENCES saas_tenants', sql)
        self.assertIn('CREATE INDEX IF NOT EXISTS', sql)
        self.assertNotIn('DROP ', sql)

    def test_schema_commit_failure_does_not_mark_migration_ready(self):
        module = load_module('tenant_crm_sync_schema', {'_ensure_schema':Mock()}, {'tenant_platform'})
        conn = MagicMock(); conn.commit.side_effect = RuntimeError('commit failed')
        with self.assertRaises(RuntimeError): module.ensure_sync_schema(conn)
        self.assertFalse(module.ensure_sync_schema.__globals__['_ready'])


class TokenRefreshTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT/'tenant_platform.py').read_text(encoding='utf-8-sig'))
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'save_kommo_oauth_tokens','replace_kommo_tokens'}]
        self.conn = MagicMock(); self.cur = self.conn.cursor.return_value.__enter__.return_value
        connect = MagicMock(); connect.return_value.__enter__.return_value = self.conn
        self.cur.fetchone.return_value = {'status':'connected'}
        self.ns = {'_connect':connect,'_ensure_schema':Mock(),'TenantPlatformError':RuntimeError,
            '_kommo_domain':lambda domain:domain,'_fernet':MagicMock(),'_json':json.dumps,
            'datetime':datetime,'timedelta':timedelta,'timezone':timezone}
        exec(compile(tree,'<sync-token-refresh>','exec'),self.ns)
        self.args = {'tenant_id':'company-a','account_domain':'example.kommo.com',
                     'token_payload':{'access_token':'test','refresh_token':'test','expires_in':3600}}

    def test_regular_refresh_preserves_connection_identity_and_requires_same_active_account(self):
        self.ns['replace_kommo_tokens'](**self.args)
        sql, values = next(call.args for call in self.cur.execute.call_args_list
                           if 'CASE WHEN %s THEN connected_at ELSE now() END' in call.args[0])
        self.assertIn('CASE WHEN %s THEN connected_at ELSE now() END', sql)
        self.assertIn("status = 'connected' AND account_domain = %s", sql)
        self.assertTrue(values[3]);self.assertTrue(values[5])
        self.assertEqual(values[4], 'company-a');self.assertEqual(values[6], 'example.kommo.com')

    def test_new_oauth_connection_gets_new_identity(self):
        self.ns['save_kommo_oauth_tokens'](**self.args)
        values = next(call.args[1] for call in self.cur.execute.call_args_list
                      if 'CASE WHEN %s THEN connected_at ELSE now() END' in call.args[0])
        self.assertFalse(values[3]);self.assertFalse(values[5])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = {'tenant_id':'company-a','telegram_id':20,'active':True,'role':'worker',
                        'permissions':['tasks'],'modules':{'tasks':True}}
        tree = ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name in {'_sync_tenant_crm','handle_platform_crm_sync_status'}]
        self.queue = Mock(return_value={'status':'queued'})
        self.status = Mock(return_value=[])
        self.ns = {'asyncio':asyncio, 'TenantPolicy':TenantPolicy, 'logger':Mock(),
            'web':SimpleNamespace(Request=dict,Response=dict,json_response=lambda data,status=200:{'data':data,'status':status}),
            '_tenant_member_from_request':lambda request:self.profile, 'enqueue_tenant_crm_sync':self.queue,
            'tenant_crm_sync_status':self.status}
        exec(compile(tree,'<sync-api>','exec'), self.ns)

    async def test_worker_queues_tasks_only_without_provider_calls(self):
        result = await self.ns['_sync_tenant_crm'](self.profile, include_tasks=True)
        self.queue.assert_called_once_with('company-a','tasks')
        self.assertEqual(result, {'jobs':[{'status':'queued'}]})

    async def test_manager_queues_deals_and_tasks(self):
        self.profile.update(role='manager',permissions=['tasks','deals'],modules={'tasks':True,'deals':True})
        await self.ns['_sync_tenant_crm'](self.profile, include_tasks=True)
        self.assertEqual([call.args for call in self.queue.call_args_list], [('company-a','leads'),('company-a','tasks')])

    async def test_status_returns_only_allowed_resources_in_current_company(self):
        response = await self.ns['handle_platform_crm_sync_status']({})
        self.assertEqual(response['status'], 200)
        self.status.assert_called_once_with('company-a',['tasks'])

    async def test_pipeline_task_visibility_waits_for_deal_dependency(self):
        self.profile['role'] = 'manager'
        response = await self.ns['handle_platform_crm_sync_status']({})
        self.assertEqual(response['status'],200)
        self.status.assert_called_once_with('company-a',['leads','tasks'])

    async def test_inactive_and_unauthorized_users_cannot_read_status(self):
        self.profile['active'] = False
        self.assertEqual((await self.ns['handle_platform_crm_sync_status']({}))['status'],403)
        self.profile['active'] = True; self.profile['permissions'] = []
        self.assertEqual((await self.ns['handle_platform_crm_sync_status']({}))['status'],403)
        self.status.assert_not_called()

    async def test_status_storage_error_is_generic_not_database_details(self):
        self.status.side_effect = RuntimeError('postgres password internal')
        response = await self.ns['handle_platform_crm_sync_status']({})
        self.assertEqual(response['status'],503)
        self.assertNotIn('password', str(response))


if __name__ == '__main__': unittest.main()
