import unittest
from tenant_policy import TenantPolicy


class BoardTests(unittest.TestCase):
    def test_only_assigned_visible_metadata_is_exposed(self):
        profile = {'role': 'manager', 'telegram_id': 7, 'active': True,
                   'permissions': ['deals'], 'modules': {'deals': True}, 'workflow': {
            'pipelines': [{'pipeline_id': 1, 'name': 'Mine', 'owner_telegram_id': 7},
                          {'pipeline_id': 2, 'name': 'Private', 'owner_telegram_id': 8}],
            'stages': [{'pipeline_id': 1, 'stage_id': 10, 'name': 'Visible', 'sort_order': 2},
                       {'pipeline_id': 1, 'stage_id': 11, 'name': 'Hidden', 'settings': {'visible': False}},
                       {'pipeline_id': 2, 'stage_id': 20, 'name': 'Private'}]}}
        self.assertEqual(TenantPolicy(profile).deal_board(), [
            {'pipeline_id': 1, 'name': 'Mine', 'stages': [{'stage_id': 10, 'name': 'Visible'}]}])
        profile['active'] = False
        self.assertEqual(TenantPolicy(profile).deal_board(), [])

    def test_worker_does_not_get_board_metadata(self):
        self.assertEqual(TenantPolicy({'role': 'worker'}).deal_board(), [])
