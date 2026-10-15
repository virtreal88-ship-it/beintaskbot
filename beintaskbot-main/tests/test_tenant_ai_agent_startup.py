import unittest

from tenant_ai_agent_startup import _prepare_database_with_retry


class _Deadlock(Exception):
    sqlstate = "40P01"


class _InvalidMigration(Exception):
    sqlstate = "42601"


class TenantAiAgentStartupTests(unittest.TestCase):
    def test_transient_postgres_deadlock_retries_and_recovers(self):
        calls = 0

        def prepare():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _Deadlock()

        _prepare_database_with_retry(prepare, delay_seconds=0)
        self.assertEqual(calls, 2)

    def test_non_transient_migration_error_is_not_retried(self):
        calls = 0

        def prepare():
            nonlocal calls
            calls += 1
            raise _InvalidMigration()

        with self.assertRaises(_InvalidMigration):
            _prepare_database_with_retry(prepare, delay_seconds=0)
        self.assertEqual(calls, 1)

    def test_repeated_deadlocks_eventually_surface(self):
        calls = 0

        def prepare():
            nonlocal calls
            calls += 1
            raise _Deadlock()

        with self.assertRaises(_Deadlock):
            _prepare_database_with_retry(prepare, attempts=3, delay_seconds=0)
        self.assertEqual(calls, 3)


if __name__ == "__main__":
    unittest.main()
