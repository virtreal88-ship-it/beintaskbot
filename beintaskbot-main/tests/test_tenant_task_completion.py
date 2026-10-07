"""Completion and approvals against simulated Kommo; no real task writes."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from test_tenant_tasks import load_service, commands, person, MemoryStore, TenantPlatformError, service

completion = load_service('tenant_task_completion', {
    'TenantPlatformError': TenantPlatformError, 'get_crm_task': Mock(), 'get_crm_deal': Mock(),
    'upsert_crm_tasks': Mock(), 'append_audit_event': Mock(), 'TaskCommandStore': MemoryStore,
    'TaskCommandPending': commands.TaskCommandPending, 'in_scope': service.in_scope, 'KommoRequest': object})


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MemoryStore.rows = {}
        self.profile = person()
        self.data = {'request_id': str(uuid.uuid4()), 'task_id': 9, 'result_text': 'Reached client'}
        self.cached = {'tenant_id': 'company-a', 'kommo_task_id': 9, 'kommo_lead_id': 7, 'text': 'Call client'}
        self.live = {'id': 9, 'entity_id': 7, 'entity_type': 'leads', 'text': 'Call client', 'is_completed': False,
                     'complete_till': 1900000000, 'responsible_user_id': 999}
        self.lead = {'id': 7, 'pipeline_id': 10, 'status_id': 100}
        self.request = AsyncMock(side_effect=self.provider)
        self.save = Mock(); self.audit = Mock()
        for name, value in {'get_crm_task': Mock(side_effect=lambda **kwargs: copy.deepcopy(self.cached)),
                            'get_crm_deal': Mock(side_effect=lambda **kwargs: copy.deepcopy(self.lead)),
                            'upsert_crm_tasks': self.save, 'append_audit_event': self.audit}.items():
            p = patch.object(completion, name, value); p.start(); self.addCleanup(p.stop)
        self.lose_response = False

    async def provider(self, tenant, method, path, **kwargs):
        self.assertEqual(tenant, 'company-a')
        if method == 'GET' and path == 'tasks/9': return copy.deepcopy(self.live)
        if method == 'GET' and path == 'leads/7': return copy.deepcopy(self.lead)
        if method == 'PATCH' and path == 'tasks/9':
            self.live.update(kwargs['json_body'])
            if self.lose_response: raise TimeoutError('Response lost after completion')
            return {'id': 9, 'updated_at': 1}
        raise AssertionError((method, path, kwargs))

    def writes(self):
        return [call for call in self.request.await_args_list if call.args[1] != 'GET']

    async def test_manager_completes_own_task_and_retries_without_second_write(self):
        for _ in range(2):
            result = await completion.complete_task(self.profile, self.data, self.request)
            self.assertTrue(result['completed'])
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.writes()[0].kwargs['json_body'], {'is_completed': True, 'result': {'text': 'Reached client'}})
        self.assertTrue(self.save.call_args.kwargs['tasks'][0]['completed'])
        self.audit.assert_called_once()
        self.assertEqual(self.audit.call_args.kwargs['action'], 'task_completed')

    async def test_member_policy_queues_without_writing_then_reviewer_completes(self):
        self.profile['workflow']['policies'] = {'task_approval': {'completion_requires_admin': False},
                                               'members': {'20': {'completion_requires_admin': True}}}
        result = await completion.complete_task(self.profile, self.data, self.request)
        self.assertTrue(result['approval_pending']); self.assertEqual(self.writes(), [])
        key = ('company-a', 20, self.data['request_id'])
        self.assertEqual(MemoryStore.rows[key][1]['approval']['kind'], 'completion')
        # A duplicate click with a fresh UUID cannot create a second approval.
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, {**self.data, 'request_id': str(uuid.uuid4())}, self.request)
        result = await completion.complete_task(self.profile, self.data, self.request, reviewer=person('owner', 1))
        self.assertTrue(result['completed']); self.assertEqual(len(self.writes()), 1)
        self.assertEqual(MemoryStore.rows[key][1]['approval']['reviewer_id'], 1)
        self.assertEqual(MemoryStore.rows[key][1]['completion_input']['result_text'], 'Reached client')

    async def test_global_policy_and_member_exemption(self):
        self.profile['workflow']['policies'] = {'task_approval': {'completion_requires_admin': True},
                                               'members': {'20': {'completion_requires_admin': False}}}
        self.assertTrue((await completion.complete_task(self.profile, self.data, self.request))['completed'])

    async def test_admin_bypasses_review_but_not_task_module_permission(self):
        self.profile = person('admin', 1)
        self.profile['workflow']['policies'] = {'task_approval': {'completion_requires_admin': True}}
        self.assertTrue((await completion.complete_task(self.profile, self.data, self.request))['completed'])
        self.profile['permissions'] = []
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, self.data, self.request)

    async def test_worker_must_have_marker_in_both_cache_and_live_task(self):
        from tenant_policy import TenantPolicy
        self.profile = person('worker')
        marker = TenantPolicy(self.profile).task_marker()
        self.cached['text'] += '\n' + marker
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, self.data, self.request)
        self.assertEqual(self.writes(), [])
        self.live['text'] += '\n' + marker
        self.assertTrue((await completion.complete_task(self.profile, self.data, self.request))['completed'])

    async def test_live_deal_scope_is_rechecked_before_completion(self):
        self.lead['pipeline_id'] = 11
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, self.data, self.request)
        self.assertEqual(self.writes(), [])

    async def test_scope_change_between_cache_and_live_read_prevents_write(self):
        original = self.request.side_effect
        async def moved(tenant, method, path, **kwargs):
            if path == 'leads/7': return {'id': 7, 'pipeline_id': 11, 'status_id': 110}
            return await original(tenant, method, path, **kwargs)
        self.request.side_effect = moved
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, self.data, self.request)
        self.assertEqual(self.writes(), [])

    async def test_foreign_cached_task_and_foreign_reviewer_are_denied(self):
        self.cached['tenant_id'] = 'company-b'
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, self.data, self.request)
        self.request.assert_not_awaited()
        self.cached['tenant_id'] = 'company-a'
        reviewer = person('owner', 1); reviewer['tenant_id'] = 'company-b'
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, self.data, self.request, reviewer=reviewer)

    async def test_response_loss_never_replays_patch_even_with_fresh_request(self):
        self.lose_response = True
        with self.assertRaises(commands.TaskCommandPending):
            await completion.complete_task(self.profile, self.data, self.request)
        with self.assertRaises(commands.TaskCommandPending):
            await completion.complete_task(self.profile, self.data, self.request)
        self.assertEqual(len(self.writes()), 1)
        # If the provider still reports open, a new UUID cannot bypass the unknown checkpoint.
        self.live['is_completed'] = False
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, {**self.data, 'request_id': str(uuid.uuid4())}, self.request)
        self.assertEqual(len(self.writes()), 1)

    async def test_already_completed_provider_task_is_read_only(self):
        self.live['is_completed'] = True
        self.assertTrue((await completion.complete_task(self.profile, self.data, self.request))['already_completed'])
        self.assertEqual(self.writes(), [])

    async def test_cache_failure_after_provider_success_does_not_invite_duplicate(self):
        self.save.side_effect = RuntimeError('Cache unavailable')
        with patch.object(completion.logging, 'getLogger'):
            result = await completion.complete_task(self.profile, self.data, self.request)
        self.assertTrue(result['completed']); self.assertIn('warning', result)
        await completion.complete_task(self.profile, self.data, self.request)
        self.assertEqual(len(self.writes()), 1)

    async def test_validation_and_changed_payload_do_not_write(self):
        for patch in ({'result_text': ''}, {'result_text': 'x'*3501}, {'task_id': 0}, {'task_id': 2**63}, {'request_id': 'bad'}):
            with self.assertRaises(TenantPlatformError):
                await completion.complete_task(self.profile, {**self.data, **patch}, self.request)
        self.assertEqual(self.writes(), [])
        self.profile['workflow']['policies'] = {'task_approval': {'completion_requires_admin': True}}
        await completion.complete_task(self.profile, self.data, self.request)
        with self.assertRaises(TenantPlatformError):
            await completion.complete_task(self.profile, {**self.data, 'result_text': 'Changed'}, self.request)
        self.assertEqual(self.writes(), [])

    async def test_completion_approval_dispatch_and_rejection_are_durable(self):
        from test_tenant_task_approvals import review
        self.profile['workflow']['policies'] = {'task_approval': {'completion_requires_admin': True}}
        await completion.complete_task(self.profile, self.data, self.request)
        key = ('company-a', 20, self.data['request_id'])
        def current_state(*args): return copy.deepcopy(MemoryStore.rows[key][1])
        complete = AsyncMock(wraps=completion.complete_task)
        with patch.object(review, 'approval_command', side_effect=current_state), \
             patch.object(review, 'member', return_value=self.profile), \
             patch.object(review, 'normalize_completion_input', completion.normalize_completion_input, create=True), \
             patch.object(review, 'complete_task', complete, create=True):
            decision = {'creator_id': 20, 'request_id': self.data['request_id'], 'action': 'reject'}
            for _ in range(2):
                self.assertTrue((await review.decide_task_approval(person('owner', 1), decision, self.request))['rejected'])
            self.assertEqual(MemoryStore.rows[key][1]['approval']['kind'], 'completion')
            self.assertIn('completion_input', MemoryStore.rows[key][1])
            self.assertEqual(self.writes(), []); complete.assert_not_awaited()
            with self.assertRaises(TenantPlatformError):
                await review.decide_task_approval(person('owner', 1), {**decision, 'action': 'approve'}, self.request)
            # Explicit new completion request after rejection is reviewable.
            self.data['request_id'] = str(uuid.uuid4()); key = ('company-a', 20, self.data['request_id'])
            await completion.complete_task(self.profile, self.data, self.request)
            decision.update(request_id=self.data['request_id'], action='approve')
            self.assertTrue((await review.decide_task_approval(person('owner', 1), decision, self.request))['completed'])
            self.assertEqual(len(self.writes()), 1)
class CompletionStorageTests(unittest.TestCase):
    def setUp(self):
        self.store = commands.TaskCommandStore.__new__(commands.TaskCommandStore)
        self.store.key = ('company-a', 20, str(uuid.uuid4()))
        self.store.conn = MagicMock()
        self.cur = self.store.conn.cursor.return_value.__enter__.return_value
        self.store.state = {'approval': {'kind': 'completion'}, 'completion_input': {'task_id': 9, 'result_text': 'Done'}}

    def test_checkpoint_keeps_completion_input_and_decision(self):
        self.store.save({'step': 'task_completing'})
        self.assertEqual(self.store.state['completion_input']['result_text'], 'Done')
        self.assertEqual(self.store.state['approval']['kind'], 'completion')
        self.store.conn.commit.assert_called_once()

    def test_other_request_check_is_scoped_and_does_not_allow_bypass(self):
        self.cur.fetchone.return_value = None
        self.store.check_task_completion(9)
        sql, params = self.cur.execute.call_args.args
        self.assertIn('tenant_id=%s::uuid', sql)
        self.assertIn("NOT IN ('done', 'rejected')", sql)
        self.assertEqual(params, ('company-a', '9', 20, self.store.key[2]))
        self.cur.fetchone.return_value = {'request_id': uuid.uuid4()}
        with self.assertRaises(TenantPlatformError): self.store.check_task_completion(9)

    def test_cross_process_task_lock_is_company_bound(self):
        self.cur.fetchone.return_value = {'locked': False}
        with self.assertRaises(commands.TaskCommandPending): self.store.lock_task(9)
        self.assertEqual(self.cur.execute.call_args.args[1], ('task-complete:company-a:9',))
        self.store.conn.commit.assert_not_called()


class CompletionEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8-sig'))
        nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'handle_platform_task_complete']
        self.profile = person()
        self.complete = AsyncMock(return_value={'completed': True})
        import asyncio
        from tenant_policy import TenantPolicy
        ns = {'web': SimpleNamespace(Request=dict, Response=dict, json_response=lambda data,status=200:(status,data)),
              '_tenant_member_from_request': lambda _: self.profile, 'TenantPolicy': TenantPolicy,
              'complete_tenant_task': self.complete, '_tenant_kommo_request': Mock(), 'asyncio': asyncio,
              'TenantPlatformError': TenantPlatformError, 'TaskCommandPending': commands.TaskCommandPending,
              'CANONICAL_WEB_ORIGIN': 'https://crm.pro.az', 'logger': Mock()}
        exec(compile(ast.Module(body=nodes,type_ignores=[]), '<completion-api>', 'exec'), ns)
        self.endpoint = ns['handle_platform_task_complete']
        self.request = SimpleNamespace(content_type='application/json', headers={'Origin':'https://crm.pro.az'},
                                       json=AsyncMock(return_value={'task_id': 9}))

    async def test_permissions_origin_and_payload_fail_before_service(self):
        self.request.headers['Origin']='https://other.example'
        self.assertEqual((await self.endpoint(self.request))[0],403)
        self.request.headers['Origin']='https://crm.pro.az'; self.request.json.return_value=[]
        self.assertEqual((await self.endpoint(self.request))[0],400)
        self.profile['active']=False
        self.assertEqual((await self.endpoint(self.request))[0],403)
        self.complete.assert_not_awaited()

    async def test_pending_write_reports_conflict_without_retry(self):
        self.complete.side_effect=commands.TaskCommandPending('Check request')
        status, body=await self.endpoint(self.request)
        self.assertEqual(status,409); self.assertTrue(body['retry_same_request'])
        self.assertEqual(self.complete.await_count,1)
