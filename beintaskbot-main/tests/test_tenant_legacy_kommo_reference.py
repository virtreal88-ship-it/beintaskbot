import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

try:
    import psycopg  # noqa: F401
except ModuleNotFoundError as error:
    if error.name != "psycopg":
        raise
    psycopg_stub = ModuleType("psycopg")
    psycopg_stub.connect = MagicMock()
    rows_stub = ModuleType("psycopg.rows")
    rows_stub.dict_row = object()
    sys.modules["psycopg"] = psycopg_stub
    sys.modules["psycopg.rows"] = rows_stub

from tenant_integration_store import kommo_credentials
from tenant_platform_core import TenantPlatformError


class LegacyKommoReferenceTests(unittest.TestCase):
    def setUp(self):
        self.cursor = MagicMock()
        self.cursor.fetchone.return_value = {
            "account_domain": "texnikidestek50.kommo.com",
            "status": "connected",
            "metadata": {
                "credential_source": "legacy_environment",
                "account_id": "32524359",
            },
            "secrets": None,
        }
        self.connection = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor

    @patch("tenant_integration_store._ensure_schema")
    @patch("tenant_integration_store._connect")
    def test_reference_reads_existing_environment_token_without_oauth_copy(self, connect, ensure_schema):
        connect.return_value.__enter__.return_value = self.connection
        with patch.dict(os.environ, {
            "KOMMO_TOKEN": "legacy-token-from-runtime",
            "KOMMO_ACCOUNT_ID": "32524359",
            "STAGING_MODE": "false",
        }, clear=True):
            credentials = kommo_credentials(tenant_id="tenant-a")

        self.assertEqual(credentials["credential_source"], "legacy_environment")
        self.assertEqual(credentials["access_token"], "legacy-token-from-runtime")
        self.assertEqual(credentials["expected_account_id"], "32524359")
        self.assertEqual(credentials["account_domain"], "texnikidestek50.kommo.com")
        self.assertEqual(credentials["refresh_token"], "")
        sql = self.cursor.execute.call_args.args[0]
        self.assertTrue(sql.lstrip().startswith("SELECT"))
        ensure_schema.assert_called_once_with(self.connection)

    @patch("tenant_integration_store._ensure_schema")
    @patch("tenant_integration_store._connect")
    def test_reference_fails_closed_without_exact_account_or_runtime_token(self, connect, _ensure_schema):
        connect.return_value.__enter__.return_value = self.connection
        cases = [
            {"KOMMO_TOKEN": "runtime-token", "KOMMO_ACCOUNT_ID": "32524360", "STAGING_MODE": "false"},
            {"KOMMO_TOKEN": "runtime-token", "KOMMO_ACCOUNT_ID": "", "STAGING_MODE": "false"},
            {"KOMMO_TOKEN": "", "KOMMO_ACCOUNT_ID": "32524359", "STAGING_MODE": "false"},
            {"KOMMO_TOKEN": "runtime-token", "KOMMO_ACCOUNT_ID": "32524359", "STAGING_MODE": "true"},
        ]
        for environment in cases:
            with self.subTest(environment={key: value for key, value in environment.items() if key != "KOMMO_TOKEN"}), \
                 patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(TenantPlatformError):
                    kommo_credentials(tenant_id="tenant-a")

    @patch("tenant_integration_store._ensure_schema")
    @patch("tenant_integration_store._connect")
    def test_legacy_reference_requires_connected_integration(self, connect, _ensure_schema):
        connect.return_value.__enter__.return_value = self.connection
        self.cursor.fetchone.return_value["status"] = "awaiting_oauth"
        with patch.dict(os.environ, {
            "KOMMO_TOKEN": "runtime-token", "KOMMO_ACCOUNT_ID": "32524359", "STAGING_MODE": "false",
        }, clear=True):
            with self.assertRaises(TenantPlatformError):
                kommo_credentials(tenant_id="tenant-a")


if __name__ == "__main__":
    unittest.main()
