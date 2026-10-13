"""Unit checks for the clone runner's source/target separation."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.railway_ai_agents_clone_check.run_clone_check import (
    SafeStop,
    clean_postgres_environment,
    parse_source_url,
    test_only_environment,
    validate_test_database_url,
)


class CloneRunnerSafetyTests(unittest.TestCase):
    def test_migration_harness_environment_contains_only_loopback_target(self):
        source_url = 'postgresql://readonly:placeholder@postgres.railway.internal:5432/prod'
        target_url = 'postgresql://ai_agents_test@127.0.0.1:55432/ai_agents_test_unit'
        with patch.dict(os.environ, {'SOURCE_READONLY_DATABASE_URL': source_url,
                                     'DATABASE_URL': 'must-not-be-forwarded'}, clear=True):
            env = test_only_environment(target_url)
        self.assertEqual(set(env), {'PATH', 'AI_AGENTS_TEST_DATABASE_URL', 'TENANT_ENCRYPTION_KEY'})
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


if __name__ == '__main__':
    unittest.main(verbosity=2)

