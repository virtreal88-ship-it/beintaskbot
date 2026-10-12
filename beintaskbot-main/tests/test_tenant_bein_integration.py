import json
import unittest
from unittest.mock import MagicMock, Mock

from cryptography.fernet import Fernet
import test_tenant_hot_orders as loaders


TENANT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
PROFILE = {"tenant_id": TENANT, "telegram_id": 42, "role": "owner", "active": True}
TOKEN = "bei_int_public.secret-value-long-enough"


class BeinIntegrationStoreTests(unittest.TestCase):
    def setUp(self):
        self.connect = MagicMock()
        self.conn = self.connect.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cipher = Fernet(Fernet.generate_key())
        class PlatformError(RuntimeError):
            pass
        self.PlatformError = PlatformError
        self.service = loaders.load("tenant_bein_integration", {
            "TenantPlatformError": PlatformError, "_connect": self.connect,
            "_ensure_schema": Mock(), "_fernet": Mock(return_value=self.cipher),
            "_json": lambda value: json.dumps(value),
        }, {"tenant_platform_core"})

    def test_save_encrypts_tenant_bound_token(self):
        self.cur.fetchone.side_effect = [
            {"role": "owner", "active": True},
            {"status": "connected", "account_domain": "https://erp.example/api/v1",
             "metadata": {"branch_id": "branch", "primary": True},
             "connected_at": None, "updated_at": None},
        ]
        result = self.service.save(PROFILE, base_url="https://erp.example", token=TOKEN,
                                   branch_id="branch", primary=True)
        insert = next(call for call in self.cur.execute.call_args_list
                      if "INSERT INTO saas_tenant_integrations" in call.args[0])
        encrypted = insert.args[1][4]
        secret = json.loads(self.cipher.decrypt(encrypted))
        self.assertEqual(secret, {"tenant_id": TENANT, "provider": "beinsystems", "token": TOKEN})
        self.assertNotIn(TOKEN, str(result))
        self.assertTrue(result["primary"])
        self.conn.commit.assert_called_once()

    def test_credential_rejects_secret_from_another_company(self):
        encrypted = self.cipher.encrypt(json.dumps({
            "tenant_id": "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
            "provider": "beinsystems", "token": TOKEN,
        }).encode())
        self.cur.fetchone.return_value = {
            "status": "connected", "account_domain": "https://erp.example/api/v1",
            "metadata": {"branch_id": "branch"}, "secrets": encrypted,
        }
        with self.assertRaises(self.PlatformError):
            self.service.credential(TENANT)

    def test_non_owner_is_rejected_before_database(self):
        with self.assertRaises(self.PlatformError):
            self.service.save({**PROFILE, "role": "manager"}, base_url="https://erp.example",
                              token=TOKEN, branch_id="branch")
        self.connect.assert_not_called()

    def test_token_can_be_saved_before_api_host_is_available(self):
        self.cur.fetchone.side_effect = [
            {"role": "owner", "active": True},
            {"status": "connected", "account_domain": "",
             "metadata": {"branch_id": "", "primary": False, "api_ready": False},
             "connected_at": None, "updated_at": None},
        ]
        result = self.service.save(PROFILE, token=TOKEN)
        insert = next(call for call in self.cur.execute.call_args_list
                      if "INSERT INTO saas_tenant_integrations" in call.args[0])
        self.assertEqual(insert.args[1][2], "")
        self.assertFalse(result["api_ready"])
        secret = json.loads(self.cipher.decrypt(insert.args[1][4]))
        self.assertEqual(secret["token"], TOKEN)

    def test_primary_provider_defaults_to_kommo(self):
        self.cur.fetchone.return_value = None
        self.assertEqual(self.service.primary_provider(TENANT), "kommo")


if __name__ == "__main__":
    unittest.main()
