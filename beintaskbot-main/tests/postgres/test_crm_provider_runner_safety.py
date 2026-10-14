"""Safety checks for the disposable provider-plane PostgreSQL runner."""
import os
from unittest import TestCase
from unittest.mock import patch

from verify_crm_provider import test_url


class ProviderRunnerSafetyTests(TestCase):
    def test_accepts_only_loopback_disposable_database(self):
        safe = "postgresql://tester:secret@127.0.0.1:55432/crm_provider_test_local"
        with patch.dict(os.environ, {"CRM_PROVIDER_TEST_DATABASE_URL": safe}, clear=True):
            self.assertEqual(test_url(), safe)

    def test_rejects_production_like_url(self):
        unsafe = "postgresql://owner:secret@postgres.railway.internal:5432/production"
        with patch.dict(os.environ, {"CRM_PROVIDER_TEST_DATABASE_URL": unsafe}, clear=True):
            with self.assertRaises(SystemExit):
                test_url()
