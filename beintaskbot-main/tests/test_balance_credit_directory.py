"""Isolated finance regressions: never connect to live storage or Telegram."""
import ast
import asyncio
import json
import math
import threading
import types
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def extract(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    exec(compile(ast.Module(body=body, type_ignores=[]), filename, "exec"), namespace)


class BalanceCreditTests(unittest.TestCase):
    def setUp(self):
        self.saved = Mock(return_value=True)
        self.ns = {"_cache": {"balance.json": {"99": {"transactions": [{"amount": 20}]},
                                               "3": {"transactions": [{"amount": 7}]}}},
                   "_lock": threading.RLock(), "_load_file": Mock(), "_save_file": self.saved,
                   "uuid": uuid, "datetime": datetime}
        extract("gh_storage.py", {"_transaction_status", "_transaction_total", "_ensure_balance_account", "add_balance_transaction"}, self.ns)
        self.ns.update({"json": json, "math": math,
            "web": types.SimpleNamespace(Request=object, Response=object,
                json_response=lambda data, status=200: (status, data)),
            "_balance_admin_chat_id": lambda request, data: 1,
            "_employee_directory_rows": lambda: [{"chat_id": 99, "name": "Rüfət", "active": True},
                                                  {"chat_id": 2, "name": "Old Rüfət", "active": False}],
            "get_balance": lambda employee_id: self.ns["_cache"]["balance.json"][str(employee_id)]["balance"],
            "get_pending_balance": lambda employee_id: 0})
        extract("bot.py", {"handle_api_balance_credit"}, self.ns)

    def credit(self, employee_id=99, amount=100):
        async def payload():
            return {"employee_id": employee_id, "amount": amount, "description": "Mədaxil"}
        return asyncio.run(self.ns["handle_api_balance_credit"](types.SimpleNamespace(json=payload)))

    def test_100_increases_current_balance_only(self):
        status, data = self.credit()
        self.assertEqual(status, 200)
        self.assertEqual(data["balance"], 120)
        ledger = self.ns["_cache"]["balance.json"]
        self.assertEqual(ledger["3"]["transactions"], [{"amount": 7}])
        self.assertEqual(ledger["99"]["transactions"][-1]["executor"], "Rüfət")

    def test_old_missing_and_negative_ids_never_write(self):
        for employee_id in (2, 6824377548, -1, 0):
            with self.subTest(employee_id=employee_id):
                self.assertIn(self.credit(employee_id)[0], (400, 409))
        self.saved.assert_not_called()

    def test_nonfinite_and_negative_amount_never_write(self):
        for amount in (float("nan"), float("inf"), -100, 0):
            self.assertEqual(self.credit(amount=amount)[0], 400)
        self.saved.assert_not_called()

    def test_persistence_failure_rolls_back_credit(self):
        self.saved.return_value = False
        self.assertEqual(self.credit()[0], 500)
        ledger = self.ns["_cache"]["balance.json"]["99"]
        self.assertEqual(ledger["balance"], 20)
        self.assertEqual(len(ledger["transactions"]), 1)

    def test_nonadmin_never_writes(self):
        self.ns["_balance_admin_chat_id"] = lambda request, data: None
        self.assertEqual(self.credit()[0], 403)
        self.saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
