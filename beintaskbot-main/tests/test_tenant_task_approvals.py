"""Review decisions and tenant-scoped queue queries without production writes."""
import copy
import ast
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
import unittest
import uuid
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from test_tenant_tasks import load_service, commands, person, MemoryStore, TenantPlatformError, service
normalize_task_input = service.normalize_task_input

review = load_service('tenant_task_approvals', {
    'TenantPlatformError': TenantPlatformError, 'member': Mock(),
    'approval_command': Mock(), 'TaskCommandStore': MemoryStore,
    'create_task': AsyncMock(), 'normalize_task_input': normalize_task_input,
    'KommoRequest': object,
})


class ApprovalDecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MemoryStore.rows = {}
        self.admin = person('owner', 1)
        self.creator = person()
        self.data = {'creator_id': 20, 'request_id': str(uuid.uuid4()), 'action': 'approve'}
        self.task = {'request_id': self.data['request_id'], 'text': 'Call client',
                     'executor_id': 20, 'due_at': '2030-01-01T12:00:00+00:00'}
        self.state = {'step': 'waiting_approval', 'approval': {'status': 'pending'}, 'task_input': self.task}
        self.member = Mock(return_value=self.creator)
        self.get = Mock(return_value=copy.deepcopy(self.state))
        self.create = AsyncMock(return_value={'task_id': 9})
        for name, value in [('member', self.member), ('approval_command', self.get), ('create_task', self.create)]:
            p = patch.object(review, name, value); p.start(); self.addCleanup(p.stop)
        self.request = AsyncMock()

    async def test_approve_loads_current_creator_and_passes_server_reviewer(self):
        self.assertEqual(await review.decide_task_approval(self.admin, self.data, self.request), {'task_id': 9})
        self.get.assert_called_once_with(self.admin, 20, self.data['request_id'])
        self.member.assert_called_once_with('company-a', 20)
        self.create.assert_awaited_once_with(self.creator, self.task, self.request, reviewer=self.admin)

    async def test_worker_cannot_review_and_deactivated_creator_cannot_be_approved(self):
        with self.assertRaises(TenantPlatformError):
            await review.decide_task_approval(person(), self.data, self.request)
        self.get.assert_not_called()
        self.creator['active'] = False
        with self.assertRaises(TenantPlatformError):
            await review.decide_task_approval(self.admin, self.data, self.request)
        self.create.assert_not_awaited()

    async def test_rejection_is_durable_and_idempotent_without_provider_writes(self):
        import hashlib, json
        payload = normalize_task_input(self.task, 20)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = ('company-a', 20, self.data['request_id'])
        MemoryStore.rows[key] = (fingerprint, copy.deepcopy(self.state))
        data = {**self.data, 'action': 'reject'}
        for _ in range(2):
            self.assertEqual(await review.decide_task_approval(self.admin, data, self.request), {'rejected': True})
        state = MemoryStore.rows[key][1]
        self.assertEqual(state['step'], 'rejected'); self.assertEqual(state['approval']['reviewer_id'], 1)
        self.assertEqual(state['task_input'], self.task)
        self.create.assert_not_awaited(); self.request.assert_not_awaited()

    async def test_rejection_cannot_cancel_a_started_provider_command(self):
        import hashlib, json
        payload = normalize_task_input(self.task, 20)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = ('company-a', 20, self.data['request_id'])
        MemoryStore.rows[key] = (fingerprint, {**self.state, 'step': 'task_creating'})
        with self.assertRaises(TenantPlatformError):
            await review.decide_task_approval(self.admin, {**self.data, 'action': 'reject'}, self.request)
        self.assertEqual(MemoryStore.rows[key][1]['step'], 'task_creating')


class ApprovalStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.connect = MagicMock(); self.connect.return_value.__enter__.return_value = self.conn
        p = patch.object(commands, '_connect', self.connect); p.start(); self.addCleanup(p.stop)

    def test_queue_filters_company_before_limit_and_joins_names_in_one_query(self):
        self.cur.fetchall.return_value = [{'actor_id': 20, 'request_id': uuid.uuid4(),
            'state': {'step':'waiting_approval','task_input':{'text':'Task'}},
            'created_at': datetime.now(timezone.utc), 'creator_name':'Employee', 'executor_name':'Employee'}]
        rows = commands.pending_task_approvals(person('owner',1))
        self.cur.execute.assert_called_once()
        sql, params = self.cur.execute.call_args.args
        self.assertIn('c.tenant_id=%s::uuid',sql);self.assertEqual(params,('company-a',201))
        self.assertIn("NOT IN ('done', 'rejected')",sql)
        self.assertIn('e.tenant_id=c.tenant_id',sql)
        self.assertEqual(rows[0]['executor_name'],'Employee')

    def test_single_review_query_is_scoped_to_tenant_actor_and_request(self):
        self.cur.fetchone.return_value = {'state':{'approval':{'status':'pending'}}}
        commands.approval_command(person('owner',1),20,'request')
        sql, params = self.cur.execute.call_args.args
        self.assertIn('tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid',sql)
        self.assertEqual(params,('company-a',20,'request'))

    def test_cursor_paging_keeps_ties_and_does_not_use_offset(self):
        created = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        rows = [{'actor_id': 20, 'request_id': uuid.UUID(int=i), 'created_at': created,
                 'state': {'step': 'waiting_approval'}, 'creator_name': 'Worker', 'executor_name': 'Worker'}
                for i in (1, 2, 3)]
        self.cur.fetchall.return_value = rows
        first = commands.pending_task_approval_page(person('owner', 1), limit=2)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['approvals']), 2)
        self.assertEqual(self.cur.execute.call_args.args[1], ('company-a', 3))
        # After both earlier tasks leave the queue, the third must not be skipped.
        self.cur.fetchall.return_value = rows[2:]
        second = commands.pending_task_approval_page(person('owner', 1), limit=2, cursor=first['next_cursor'])
        sql, params = self.cur.execute.call_args.args
        self.assertNotIn('OFFSET', sql)
        self.assertIn('(c.created_at, c.actor_id, c.request_id) >', sql)
        self.assertIn('c.created_at ASC, c.actor_id ASC, c.request_id ASC', sql)
        self.assertEqual(params, ('company-a', created, 20, str(uuid.UUID(int=2)), 3))
        self.assertEqual(second['approvals'][0]['request_id'], str(uuid.UUID(int=3)))
        self.assertFalse(second['has_more']); self.assertIsNone(second['next_cursor'])

    def test_invalid_cursor_or_limit_never_opens_database(self):
        with self.assertRaises(ValueError):
            commands.pending_task_approval_page(person('owner', 1), cursor='bad!')
        with self.assertRaises(ValueError):
            commands.pending_task_approval_page(person('owner', 1), limit=201)
        self.connect.assert_not_called()

    def test_queue_larger_than_200_is_read_in_bounded_pages(self):
        created = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        rows = [{'actor_id': 20, 'request_id': uuid.UUID(int=i), 'created_at': created,
                 'state': {'step': 'waiting_approval'}, 'creator_name': 'Worker', 'executor_name': 'Worker'}
                for i in range(1, 251)]
        def fetch_page():
            params = self.cur.execute.call_args.args[1]
            after = uuid.UUID(params[3]).int if len(params) > 2 else 0
            return [row for row in rows if row['request_id'].int > after][:params[-1]]
        self.cur.fetchall.side_effect = fetch_page
        seen, cursor = [], ''
        while True:
            page = commands.pending_task_approval_page(person('owner', 1), cursor=cursor)
            self.assertLessEqual(len(page['approvals']), 50)
            seen.extend(row['request_id'] for row in page['approvals'])
            if not page['has_more']:
                break
            cursor = page['next_cursor']
        self.assertEqual(seen, [str(row['request_id']) for row in rows])
        self.assertEqual(self.cur.execute.call_count, 5)

    def test_completion_queue_preserves_kind_result_and_employee_context(self):
        self.cur.fetchall.return_value = [{'actor_id': 20, 'request_id': uuid.uuid4(),
            'state': {'step': 'waiting_approval', 'completion_input': {'task_id': 9, 'text': 'Call client',
                      'result_text': 'Reached client', 'executor_id': 20}},
            'created_at': datetime.now(timezone.utc), 'creator_name': 'Worker', 'executor_name': 'Worker'}]
        page = commands.pending_task_approval_page(person('owner', 1))
        self.assertEqual(page['approvals'][0]['kind'], 'completion')
        self.assertEqual(page['approvals'][0]['task']['result_text'], 'Reached client')
        self.assertIn("c.state->'completion_input'->>'executor_id'", self.cur.execute.call_args.args[0])

    def test_denied_reader_does_not_open_database(self):
        with self.assertRaises(TenantPlatformError):commands.pending_task_approvals(person())
        with self.assertRaises(TenantPlatformError):commands.approval_command(person(),20,'request')
        self.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
