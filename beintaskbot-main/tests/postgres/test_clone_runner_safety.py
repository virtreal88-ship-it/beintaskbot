"""Unit checks for the clone runner's source/target separation."""
import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.railway_ai_agents_clone_check.run_clone_check import (
    SafeStop,
    clean_postgres_environment,
    parse_source_url,
    test_only_environment,
    validate_test_database_url,
    validate_backup_role_policy,
    validate_volume_capacity,
)
from scripts.railway_ai_agents_clone_check import run_clone_check


class CloneRunnerSafetyTests(unittest.TestCase):
    def test_migration_harness_environment_contains_only_loopback_target(self):
        source_url = 'postgresql://readonly:placeholder@postgres.railway.internal:5432/prod'
        target_url = 'postgresql://ai_agents_test@127.0.0.1:55432/ai_agents_test_unit'
        with patch.dict(os.environ, {'SOURCE_READONLY_DATABASE_URL': source_url,
                                     'DATABASE_URL': 'must-not-be-forwarded'}, clear=True):
            env = test_only_environment(target_url)
        self.assertEqual(set(env), {'PATH', 'PYTHONPATH', 'AI_AGENTS_TEST_DATABASE_URL', 'TENANT_ENCRYPTION_KEY'})
        self.assertEqual(env['PYTHONPATH'], '/opt/app')
        self.assertEqual(env['AI_AGENTS_TEST_DATABASE_URL'], target_url)
        self.assertNotIn(source_url, env.values())
        self.assertNotIn('DATABASE_URL', env)

    def test_source_requires_railway_private_hostname_and_non_admin_login(self):
        with self.assertRaises(SafeStop):
            parse_source_url('postgresql://readonly:placeholder@db.example.net:5432/prod')
        with self.assertRaises(SafeStop):
            parse_source_url('postgresql://postgres:placeholder@postgres.railway.internal:5432/prod')

    def test_runner_refuses_inherited_production_database_url(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://admin@prod.invalid/db'}, clear=True):
            with self.assertRaises(SafeStop):
                clean_postgres_environment()

    def test_migration_target_refuses_non_loopback_or_non_test_database(self):
        validate_test_database_url('postgresql://ai_agents_test@127.0.0.1:55432/ai_agents_test_unit')
        with self.assertRaises(SafeStop):
            validate_test_database_url('postgresql://readonly@postgres.railway.internal:5432/prod')
        with self.assertRaises(SafeStop):
            validate_test_database_url('postgresql://ai_agents_test@127.0.0.1:55432/production')

    def test_runner_accepts_existing_five_gib_volume_and_rejects_larger(self):
        validate_volume_capacity(5 * 1024 * 1024 * 1024, mounted=True)
        with self.assertRaises(SafeStop):
            validate_volume_capacity(5 * 1024 * 1024 * 1024 + 1, mounted=True)

    def test_runner_requires_a_mounted_volume_before_source_connection(self):
        with self.assertRaises(SafeStop):
            validate_volume_capacity(5 * 1024 * 1024 * 1024, mounted=False)

    def test_oversized_volume_stops_before_any_source_connection(self):
        with patch.dict(os.environ, {'SOURCE_READONLY_DATABASE_URL':
                                     'postgresql://readonly:placeholder@postgres.railway.internal:5432/railway'},
                        clear=True), \
             patch('scripts.railway_ai_agents_clone_check.run_clone_check.shutil.disk_usage',
                   return_value=SimpleNamespace(total=5 * 1024 * 1024 * 1024, free=5 * 1024 * 1024 * 1024)), \
             patch('scripts.railway_ai_agents_clone_check.run_clone_check.os.path.ismount', return_value=False), \
             patch('scripts.railway_ai_agents_clone_check.run_clone_check.source_readonly_audit') as source_audit:
            with self.assertRaises(SafeStop):
                run_clone_check.run_check()
        source_audit.assert_not_called()

    def test_backup_role_policy_allows_only_temp_inherited_from_public(self):
        attrs = (False, False, False, False, False, True, True)
        validate_backup_role_policy(attrs, [], False, True, True, False, True, (0,) * 9)
        validate_backup_role_policy(attrs, [], False, False, False, False, True, (0,) * 9)
        with self.assertRaises(SafeStop):
            validate_backup_role_policy(attrs, ['pg_read_all_data'], False, True, True, False, True, (0,) * 9)
        with self.assertRaises(SafeStop):
            validate_backup_role_policy(attrs, [], False, True, True, True, True, (0,) * 9)
        with self.assertRaises(SafeStop):
            validate_backup_role_policy(attrs, [], False, True, False, False, True, (0,) * 9)

    def test_backup_role_policy_rejects_write_or_ownership_rights(self):
        attrs = (False, False, False, False, False, True, True)
        with self.assertRaises(SafeStop):
            validate_backup_role_policy(attrs, [], False, False, False, False, True, (0, 0, 1))


if __name__ == '__main__':
    unittest.main(verbosity=2)
