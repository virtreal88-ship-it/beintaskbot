import copy
import unittest

from tenant_notifications import notification_channels, available_notification_events, notification_catalog
from tenant_policy import TenantPolicy, validate_workflow_patch


def person(role='manager', tenant='company-a'):
    return {'tenant_id': tenant, 'telegram_id': 20, 'role': role, 'active': True,
            'permissions': ['tasks', 'deals', 'customers', 'hot_orders', 'linear'],
            'modules': dict.fromkeys(['tasks', 'deals', 'customers', 'hot_orders', 'linear'], True),
            'workflow': {'policies': {'members': {'20': {'notifications': {
                'task_assigned': {'telegram': True, 'push': False},
                'task_completion_requested': {'telegram': True, 'push': True},
                'hot_order_available': {'telegram': True, 'push': True},
            }}}}}}


class NotificationPreferencesTests(unittest.TestCase):
    def channels(self, profile, event='task_assigned', tenant='company-a'):
        return notification_channels(TenantPolicy(profile), event=event, tenant_id=tenant)

    def test_channels_are_independent_and_explicit(self):
        profile=person()
        self.assertEqual(self.channels(profile), ['telegram'])
        profile['workflow']['policies']['members']['20']['notifications']['task_assigned']['push']=True
        self.assertEqual(self.channels(profile), ['telegram', 'push'])
        profile['workflow']['policies']['members']['20']['notifications']['task_assigned']['telegram']=False
        self.assertEqual(self.channels(profile), ['push'])

    def test_unconfigured_employee_is_not_implicitly_subscribed(self):
        profile=person(); profile['workflow']={}
        profile['notification_rules']={'task_assigned':True}
        self.assertEqual(self.channels(profile), [])

    def test_admin_review_events_require_role_and_tasks_permission(self):
        for role in ('manager', 'worker', 'master'):
            self.assertEqual(self.channels(person(role), 'task_completion_requested'), [])
        profile=person('admin')
        self.assertEqual(self.channels(profile, 'task_completion_requested'), ['telegram', 'push'])
        profile['permissions'].remove('tasks')
        self.assertEqual(self.channels(profile, 'task_completion_requested'), [])

    def test_worker_and_master_have_different_event_catalogs(self):
        self.assertEqual(available_notification_events(TenantPolicy(person('master'))), ['hot_order_available'])
        events=available_notification_events(TenantPolicy(person('worker')))
        self.assertIn('task_assigned', events)
        self.assertNotIn('incoming_message', events)
        self.assertNotIn('task_completion_requested', events)

    def test_inactive_member_denied_including_owner(self):
        for role in ('owner', 'admin', 'manager', 'worker', 'master'):
            profile=person(role); profile['active']=False
            self.assertEqual(self.channels(profile, 'hot_order_available'), [])

    def test_cross_company_and_unknown_events_fail_closed(self):
        self.assertEqual(self.channels(person(), tenant='company-b'), [])
        self.assertEqual(self.channels(person(), tenant=''), [])
        self.assertEqual(self.channels(person(), event='unknown'), [])

    def test_global_event_and_channel_kill_switch(self):
        profile=person(); profile['workflow']['policies']['notifications']={'telegram':False}
        self.assertEqual(self.channels(profile), [])
        profile['workflow']['policies']['notifications']={'task_assigned':False}
        self.assertEqual(self.channels(profile), [])
        profile['notification_rules']={'task_assigned':False}
        profile['workflow']['policies']['notifications']={'task_assigned':True}
        self.assertEqual(self.channels(profile), ['telegram'])

    def test_module_disabling_overrides_saved_preference(self):
        profile=person(); profile['workflow']['policies']['modules']={'tasks':False}
        self.assertEqual(self.channels(profile), [])

    def test_same_telegram_user_has_independent_company_preferences(self):
        a=person(); b=person(tenant='company-b')
        b['workflow']['policies']['members']['20']['notifications']={}
        self.assertEqual(self.channels(a), ['telegram'])
        self.assertEqual(self.channels(b, tenant='company-b'), [])

    def test_invalid_nested_preferences_rejected(self):
        for value in ([], None, {'unknown':{}}, {'task_assigned':[]},
                      {'task_assigned':{'email':True}}, {'task_assigned':{'telegram':'false'}},
                      {'task_assigned':{'push':1}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_workflow_patch([], [], {'members':{'20':{'notifications':value}}})
        validate_workflow_patch([], [], person()['workflow']['policies'])

    def test_legacy_settings_remain_valid(self):
        validate_workflow_patch([], [], {'members': {'20': {'completion_requires_admin':True}},
                                        'notifications': {'task_assigned':True}})

    def test_catalog_returns_fresh_lists_and_is_exposed_in_capabilities(self):
        catalog=notification_catalog(); original=copy.deepcopy(catalog)
        catalog[0]['roles'].clear(); catalog[0]['channels'].clear()
        self.assertEqual(notification_catalog(), original)
        self.assertEqual(TenantPolicy(person()).public_capabilities()['notification_events'], original)


if __name__ == '__main__':
    unittest.main()
