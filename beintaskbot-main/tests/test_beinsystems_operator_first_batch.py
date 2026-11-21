"""Synthetic only; no Operator API or production database access."""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

import tenant_beinsystems_operator_company_import as batch


def response(*names):
    return {"status": "success", "data": [{"account": name} for name in names]}


class FirstBatchTests(unittest.TestCase):
    def test_strict_raw_limit_and_ids(self):
        self.assertEqual(len(batch._first_batch_payload(response(*(str(i) for i in range(100))))), 100)
        for malformed in (response(*(str(i) for i in range(101))), response(" A"),
                          response("a", "A"), response("a\x7f"), {"status": "error", "data": []},
                          {"status": "success", "data": [], "unknown": True}):
            with self.subTest(malformed=malformed), self.assertRaises(batch.operator.OperatorError):
                batch._first_batch_payload(malformed)

    def test_disabled_gate_no_source_opened(self):
        with patch.object(batch.operator, "first_company_batch_preview_enabled", return_value=False), \
                patch.object(batch, "_selected_source_credentials") as source:
            with self.assertRaises(batch.operator.OperatorError):
                batch.first_company_batch({})
            source.assert_not_called()

    def test_preview_flag_never_admits_confirmation(self):
        with patch.dict(batch.operator.os.environ, {
            "BEINSYSTEMS_OPERATOR_FIRST_COMPANY_BATCH_PREVIEW": "1",
            "BEINSYSTEMS_OPERATOR_FIRST_COMPANY_BATCH_CONFIRM": "0",
        }), patch.object(batch, "_selected_source_credentials") as source:
            self.assertTrue(batch.operator.first_company_batch_preview_enabled())
            self.assertFalse(batch.operator.first_company_batch_confirm_enabled())
            with self.assertRaises(batch.operator.OperatorError) as err:
                batch.first_company_batch({}, receipt="opaque")
            self.assertEqual(err.exception.status, 403)
            source.assert_not_called()
        with patch.dict(batch.operator.os.environ, {
            "BEINSYSTEMS_OPERATOR_FIRST_COMPANY_BATCH_PREVIEW": "0",
            "BEINSYSTEMS_OPERATOR_FIRST_COMPANY_BATCH_CONFIRM": "1",
        }):
            self.assertFalse(batch.operator.first_company_batch_confirm_enabled())

    def test_only_verified_source_then_exact_documented_read(self):
        ident = "owner@example"
        cred = {"token": "mock", "base_url": "https://example.invalid", "received_at": "rev",
                "generation": "new", "connection_instance_id": "instance",
                "identity_fingerprint": batch._identifier_fingerprint(ident)}
        valid = {"status": "success", "data": {"identifier": ident, "role": "unlimited"}}
        with patch.object(batch.operator, "first_company_batch_preview_enabled", return_value=True), \
                patch.object(batch, "_selected_source_credentials", return_value=("t", 1, "accounts_only", cred)), \
                patch.object(batch, "_selected_source_fence") as fence, \
                patch.object(batch.operator, "_record_operator_read"), \
                patch.object(batch.operator, "_request_operator", side_effect=[valid, response("b", "a")]) as read, \
                patch.object(batch, "_apply_company_import") as apply:
            batch.first_company_batch({})
            self.assertEqual(read.call_args.args, ("accounts", {"search": "", "limit": 100}))
            self.assertEqual(fence.call_count, 4)
            self.assertEqual(apply.call_args.args[3], [{"account": "a"}, {"account": "b"}])
            self.assertNotEqual(batch._first_batch_scope("t", 1, cred),
                                batch._first_batch_scope("other-tenant", 1, cred))
            self.assertNotEqual(batch._first_batch_scope("t", 1, cred),
                                batch._first_batch_scope("t", 1, {**cred, "token": "rotated"}))

    def test_actual_transport_preserves_explicit_empty_batch_search_only(self):
        with patch.object(batch.operator, "api_base_url", return_value=batch.operator.OPERATOR_API_BASE_URL),                 patch.object(batch.operator, "_request_accounts_with_requests",
                             return_value=b'{"status":"success","data":[]}') as transport:
            batch.operator._request_operator("accounts", {"search": "", "limit": 100},
                token="mock", first_batch_accounts=True)
            self.assertEqual(transport.call_args.args[1], {"search": "", "limit": 100})
            batch.operator._request_operator("accounts", {"search": "", "limit": 1}, token="mock")
            self.assertEqual(transport.call_args.args[1], {"limit": 1})
            with self.assertRaises(batch.operator.OperatorError):
                batch.operator._request_operator("accounts", {"search": "", "limit": 5},
                    token="mock", first_batch_accounts=True)

    def test_preview_validates_again_and_consumes_nonce_before_company_write(self):
        profile = {"role": "owner"}
        credentials = {"connection_instance_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                       "generation": "1"}
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        cur.fetchone.return_value = {"id": "ok"}
        with ExitStack() as stack:
            for name in ("_ensure_schema", "_lock_accounts_capability_scope"):
                stack.enter_context(patch.object(batch.operator, name))
            stack.enter_context(patch.object(batch.operator, "_connect", return_value=conn))
            stack.enter_context(patch.object(batch.operator, "_current_profile", return_value=profile))
            stack.enter_context(patch.object(batch.operator, "_fernet", return_value=Fernet(Fernet.generate_key())))
            stack.enter_context(patch.object(batch, "_assert_first_batch_not_expired"))
            gate = stack.enter_context(patch.object(batch.operator, "first_company_batch_confirm_enabled",
                                                    return_value=False))
            locked = stack.enter_context(patch.object(batch, "_lock_and_check_selected_source"))
            stack.enter_context(patch.object(batch, "current_access", return_value={}))
            stack.enter_context(patch.object(batch, "TenantPolicy")).return_value.has_scoped_access.return_value = False
            stack.enter_context(patch.object(batch, "authorize_create"))
            mapping = stack.enter_context(patch.object(batch, "_put_mapping", return_value=True))
            def run(names, receipt=None, scope="scope"):
                return batch._apply_company_import(profile, "t", 1, batch._first_batch_payload(response(*names)),
                    credentials, first_batch=True, preview_receipt=receipt, preview_scope=scope)
            def mutations():
                return [call.args[0] for call in cur.execute.call_args_list
                        if "INSERT INTO" in call.args[0]]
            preview_only = run(["a"])
            self.assertNotIn("receipt", preview_only)
            self.assertEqual(preview_only["rows"], [{"account": "a", "action": "create"}])
            gate.return_value = True
            preview = run(["a"])
            self.assertEqual(preview["rows"], [{"account": "a", "action": "create"}])
            self.assertTrue(preview["partial"])
            self.assertFalse(mutations())
            conn.commit.assert_not_called()
            with self.assertRaises(batch.operator.OperatorError):
                run(["b"], preview["receipt"])
            with self.assertRaises(batch.operator.OperatorError):
                run(["a"], preview["receipt"], scope="other")
            self.assertFalse(mutations())
            result = run(["a"], preview["receipt"])
            self.assertEqual(result["created"], 1)
            self.assertIn("ON CONFLICT(id) DO NOTHING", mutations()[0])
            self.assertIn("INSERT INTO saas_crm_native_companies", mutations()[1])
            mapping.assert_called_once()
            self.assertEqual(locked.call_count, 5)
            conn.commit.assert_called_once()
            cur.fetchone.return_value = None
            before = len(mutations())
            with self.assertRaises(batch.operator.OperatorError):
                run(["a"], preview["receipt"])
            self.assertEqual(len(mutations()) - before, 1)
            conn.commit.assert_called_once()

    def test_receipt_wrong_purpose_expired_and_tampered(self):
        key = Fernet(Fernet.generate_key())
        signed = {"purpose": batch._FIRST_BATCH_PURPOSE, "scope": "scope", "digest": "digest",
                  "nonce": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"}
        with patch.object(batch.operator, "_fernet", return_value=key):
            valid = key.encrypt(json.dumps(signed).encode()).decode()
            self.assertEqual(batch._decode_first_batch_receipt(valid), signed)
            for invalid in (key.encrypt_at_time(json.dumps(signed).encode(), 1).decode(),
                            key.encrypt(json.dumps({**signed, "purpose": "other"}).encode()).decode(),
                            "X" + valid):
                with self.assertRaises(batch.operator.OperatorError):
                    batch._decode_first_batch_receipt(invalid)


if __name__ == "__main__":
    unittest.main()
