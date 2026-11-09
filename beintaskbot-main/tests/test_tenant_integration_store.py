"""Tenant-scoped encrypted storage and OAuth precedence for manual Kommo tokens."""
import json
import unittest
from unittest.mock import MagicMock, Mock, patch
from cryptography.fernet import Fernet

import tenant_integration_store as store
from tenant_platform_core import TenantPlatformError


TENANT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
OWNER = {"tenant_id": TENANT, "telegram_id": 20, "role": "owner"}


class KommoManualStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.conn = self.connection.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cipher = Fernet(Fernet.generate_key())
        self.patches = [
            patch.object(store, "_connect", self.connection),
            patch.object(store, "_ensure_schema"),
            patch.object(store, "_fernet", return_value=self.cipher),
            patch.object(store, "member", return_value=OWNER),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_manual_token_is_owner_scoped_encrypted_and_separate_from_oauth(self):
        saved = {"provider": "kommo_manual", "status": "connected", "account_domain": "demo.kommo.com",
                 "metadata": {"auth_mode": "manual"}, "connected_at": None, "updated_at": None}
        self.cur.fetchone.side_effect = [None, saved]
        result = store.save_kommo_manual_token(
            tenant_id=TENANT, owner_id=20, account_domain="demo.kommo.com", access_token="private-token")
        insert = self.cur.execute.call_args_list[1]
        encrypted = insert.args[1][3]
        self.assertNotIn(b"private-token", encrypted)
        payload = json.loads(self.cipher.decrypt(encrypted))
        self.assertEqual(payload, {"tenant_id": TENANT, "provider": "kommo_manual", "access_token": "private-token"})
        self.assertEqual(result["provider"], "kommo_manual")
        self.assertNotIn("private-token", str(result))
        self.assertIn("'kommo_manual'", insert.args[0])
        self.conn.commit.assert_called_once()

    def test_official_oauth_connection_cannot_be_overwritten_by_manual_token(self):
        self.cur.fetchone.return_value = {"status": "connected"}
        with self.assertRaises(TenantPlatformError):
            store.save_kommo_manual_token(
                tenant_id=TENANT, owner_id=20, account_domain="demo.kommo.com", access_token="private-token")
        self.assertEqual(self.cur.execute.call_count, 1)
        self.conn.commit.assert_not_called()

    def test_credentials_prefer_oauth_and_fall_back_to_company_manual_token(self):
        oauth = self.cipher.encrypt(json.dumps({
            "access_token": "oauth-access", "refresh_token": "oauth-refresh"}).encode())
        manual = self.cipher.encrypt(json.dumps({
            "tenant_id": TENANT, "provider": "kommo_manual", "access_token": "manual-access"}).encode())
        self.cur.fetchall.return_value = [
            {"provider": "kommo", "status": "connected", "account_domain": "official.kommo.com",
             "metadata": {}, "secrets": oauth},
            {"provider": "kommo_manual", "status": "connected", "account_domain": "manual.kommo.com",
             "metadata": {"auth_mode": "manual"}, "secrets": manual},
        ]
        active = store.kommo_credentials(tenant_id=TENANT)
        self.assertEqual(active["auth_mode"], "oauth")
        self.assertEqual(active["access_token"], "oauth-access")

        self.cur.fetchall.return_value[0]["status"] = "awaiting_oauth"
        active = store.kommo_credentials(tenant_id=TENANT)
        self.assertEqual(active["auth_mode"], "manual")
        self.assertEqual(active["account_domain"], "manual.kommo.com")
        self.assertEqual(active["access_token"], "manual-access")

    def test_manual_credentials_reject_a_different_tenant_payload(self):
        encrypted = self.cipher.encrypt(json.dumps({
            "tenant_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "provider": "kommo_manual", "access_token": "private-token"}).encode())
        self.cur.fetchall.return_value = [
            {"provider": "kommo_manual", "status": "connected", "account_domain": "demo.kommo.com",
             "metadata": {"auth_mode": "manual"}, "secrets": encrypted},
        ]
        with self.assertRaises(TenantPlatformError):
            store.kommo_credentials(tenant_id=TENANT)

    def test_integration_list_aliases_one_manual_row_without_credentials(self):
        self.cur.fetchall.return_value = [
            {"provider": "kommo_manual", "status": "connected", "account_domain": "demo.kommo.com",
             "metadata": {"auth_mode": "manual"}, "connected_at": None, "updated_at": None},
        ]
        result = store.list_integrations(tenant_id=TENANT, telegram_id=20)
        kommo = next(item for item in result if item["provider"] == "kommo")
        self.assertEqual(kommo["status"], "connected")
        self.assertEqual(kommo["metadata"]["auth_mode"], "manual")
        self.assertNotIn("secrets", kommo)

    def test_kommo_disconnect_clears_manual_secret_for_that_tenant(self):
        self.cur.fetchone.return_value = {
            "provider": "kommo", "status": "not_connected", "account_domain": "",
            "metadata": {}, "connected_at": None, "updated_at": None}
        store.disconnect_integration(tenant_id=TENANT, owner_id=20, provider="kommo")
        self.assertIn("provider = 'kommo_manual'", self.cur.execute.call_args_list[1].args[0])
        self.assertEqual(self.cur.execute.call_args_list[1].args[1], (TENANT,))
        self.conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
