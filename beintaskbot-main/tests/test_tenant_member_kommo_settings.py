import unittest
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from tenant_member_kommo_settings import apply_patch, snapshot

class MemberCardError(ValueError):
    def __init__(self, code, status=400, *, fields=None):
        self.code, self.status, self.fields = code, status, fields or {}


WORKFLOW = {
    'pipelines': [
        {'pipeline_id': '10', 'name': 'Sales', 'active': True, 'settings': {}},
        {'pipeline_id': '20', 'name': 'Internal', 'active': True, 'settings': {'provider': 'beintaskbot'}},
        {'pipeline_id': '30', 'name': 'Disabled', 'active': False, 'settings': {}},
    ],
    'policies': [{'policy_key': 'members', 'value': {'22': {
        'pipeline_ids': ['10'], 'kommo_user_id': '501', 'ai_enabled': True,
    }}}],
}


class Cursor:
    def __init__(self): self.calls = []
    def execute(self, sql, args): self.calls.append((sql, args))


class KommoMemberSettingsTests(unittest.TestCase):
    def test_snapshot_lists_only_active_kommo_pipelines_and_preserves_scope(self):
        result = snapshot(WORKFLOW, {'telegram_id': 22})
        self.assertEqual(result['pipelines'], [{'pipeline_id': '10', 'name': 'Sales'}])
        self.assertEqual(result['values'], {'pipeline_ids': ['10'], 'kommo_user_id': '501'})
        self.assertEqual(len(result['version']), 64)

    def test_sparse_update_preserves_other_member_settings(self):
        before = snapshot(WORKFLOW, {'telegram_id': 22})
        cursor = Cursor()
        with patch.dict(sys.modules, {'tenant_member_store': SimpleNamespace(MemberCardError=MemberCardError),
                                      'tenant_platform_core': SimpleNamespace(_json=json.dumps)}):
            apply_patch(cursor, tenant_id='tenant', owner_id=1, target={'telegram_id': 22}, workflow=WORKFLOW,
                patch={'expected_version': before['version'], 'changes': {'pipeline_ids': []}})
        self.assertEqual(len(cursor.calls), 1)
        self.assertIn("jsonb_set", cursor.calls[0][0])
        self.assertIn('tenant', cursor.calls[0][1])

    def test_stale_version_and_foreign_or_native_pipeline_ids_are_rejected(self):
        cursor = Cursor()
        before = snapshot(WORKFLOW, {'telegram_id': 22})
        with patch.dict(sys.modules, {'tenant_member_store': SimpleNamespace(MemberCardError=MemberCardError),
                                      'tenant_platform_core': SimpleNamespace(_json=json.dumps)}):
            with self.assertRaises(MemberCardError):
                apply_patch(cursor, tenant_id='tenant', owner_id=1, target={'telegram_id': 22}, workflow=WORKFLOW,
                    patch={'expected_version': 'stale', 'changes': {'pipeline_ids': ['10']}})
            for pipeline in ('20', '99'):
                with self.subTest(pipeline=pipeline), self.assertRaises(MemberCardError):
                    apply_patch(cursor, tenant_id='tenant', owner_id=1, target={'telegram_id': 22}, workflow=WORKFLOW,
                        patch={'expected_version': before['version'], 'changes': {'pipeline_ids': [pipeline]}})
        self.assertEqual(cursor.calls, [])


if __name__ == '__main__':
    unittest.main()
