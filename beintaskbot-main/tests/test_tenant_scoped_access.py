import unittest

from tenant_scoped_access import allows_assigned_task_exception, allows_owned_resource


class ScopedAccessTests(unittest.TestCase):
    def setUp(self):
        self.actor = {'tenant_id': 'one', 'member_id': 'a', 'department_id': 'sales', 'active': True}
        self.own = {'tenant_id': 'one', 'responsible_member_id': 'a', 'responsible_department_id': 'sales'}
        self.colleague = {'tenant_id': 'one', 'responsible_member_id': 'b', 'responsible_department_id': 'sales'}
        self.other_department = {'tenant_id': 'one', 'responsible_member_id': 'c', 'responsible_department_id': 'support'}
        self.other_tenant = {'tenant_id': 'two', 'responsible_member_id': 'a', 'responsible_department_id': 'sales'}

    def test_four_scopes_across_members_departments_and_tenants(self):
        expected = {
            'none': (False, False, False, False),
            'self': (True, False, False, False),
            'department': (True, True, False, False),
            'all': (True, True, True, False),
        }
        for scope, results in expected.items():
            with self.subTest(scope=scope):
                self.assertEqual(tuple(allows_owned_resource(self.actor, r, scope) for r in
                                       (self.own, self.colleague, self.other_department, self.other_tenant)), results)

    def test_missing_inactive_and_invalid_values_fail_closed(self):
        self.assertFalse(allows_owned_resource({**self.actor, 'active': False}, self.own, 'all'))
        self.assertFalse(allows_owned_resource({**self.actor, 'tenant_id': None}, self.own, 'all'))
        self.assertFalse(allows_owned_resource({**self.actor, 'member_id': None}, self.own, 'all'))
        self.assertFalse(allows_owned_resource(self.actor, self.own, 'everything'))
        self.assertFalse(allows_owned_resource({**self.actor, 'department_id': None},
                                                {**self.colleague, 'responsible_department_id': None}, 'department'))
        self.assertFalse(allows_owned_resource(self.actor, {**self.own, 'responsible_member_id': None}, 'department'))

    def test_assignee_exception_is_only_read_and_complete_and_never_cross_tenant(self):
        task = {**self.colleague, 'assignee_member_id': 'a'}
        for operation in ('read', 'complete'):
            self.assertTrue(allows_assigned_task_exception(self.actor, task, operation))
        for operation in ('create', 'update', 'delete', 'approve', 'reward'):
            self.assertFalse(allows_assigned_task_exception(self.actor, task, operation))
        self.assertFalse(allows_assigned_task_exception(self.actor, {**task, 'tenant_id': 'two'}, 'read'))
        self.assertFalse(allows_assigned_task_exception(self.actor, {**task, 'assignee_member_id': 'b'}, 'read'))
        self.assertFalse(allows_assigned_task_exception({**self.actor, 'active': False}, task, 'complete'))


if __name__ == '__main__':
    unittest.main()
