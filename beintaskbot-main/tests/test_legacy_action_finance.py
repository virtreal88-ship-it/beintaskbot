"""Tests for the legacy payout action."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from legacy_action_finance import PayoutContext, handle_payout


class PayoutActionTests(unittest.TestCase):
    def setUp(self):
        self.add = Mock(return_value=True)
        self.context = PayoutContext(
            get_kommo_user_id_for_chat=lambda chat: 10932455 if chat == 20 else 7,
            add_balance_transaction=self.add,
            get_employee_name=lambda employee, fallback: "Rüfət",
        )

    def test_admin_records_negative_confirmed_ledger_entry(self):
        result = handle_payout(
            {"employee_id": 30, "amount": "100", "note": "Ödəniş"},
            20,
            self.context,
        )
        self.assertTrue(result.payload["success"])
        self.assertEqual(self.add.call_args.args[:4], (30, 0, -100.0, "Ödəniş"))
        self.assertEqual(self.add.call_args.kwargs["transaction_type"], "məxaric")

    def test_non_admin_and_invalid_amount_are_rejected(self):
        denied = handle_payout({"employee_id": 30, "amount": 100}, 21, self.context)
        self.assertFalse(denied.payload["success"])
        invalid = handle_payout({"employee_id": 30, "amount": "bad"}, 20, self.context)
        self.assertFalse(invalid.payload["success"])
        self.add.assert_not_called()

    def test_storage_failure_is_not_reported_as_paid(self):
        self.add.return_value = False
        result = handle_payout({"employee_id": 30, "amount": 100}, 20, self.context)
        self.assertEqual(result.status, 502)
        self.assertFalse(result.payload["success"])


if __name__ == "__main__":
    unittest.main()
