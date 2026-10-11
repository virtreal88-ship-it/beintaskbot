"""Approved AI reply example storage and selection contracts."""

import ast
import unittest
from pathlib import Path

import legacy_chat_ai_examples as examples


ROOT = Path(__file__).resolve().parents[1]


class ReplyExamplesTests(unittest.TestCase):
    def setUp(self):
        self.rows = []

        def read_json(_name):
            return list(self.rows)

        def write_json(_name, value):
            self.rows = list(value)
            return True

        examples.configure_ai_reply_examples(read_json=read_json, write_json=write_json)

    def test_save_is_idempotent_for_same_lead_and_reply(self):
        row = {"lead_id": 7, "reply": "Cavab", "context": "Qiymət soruşdu"}
        self.assertEqual(examples.save_ai_reply_example(row), (True, False))
        self.assertEqual(examples.save_ai_reply_example(row), (False, True))
        self.assertEqual(len(self.rows), 1)

    def test_selection_prefers_same_pipeline_and_stage(self):
        self.rows = [
            {
                "lead_id": 1, "scope": "5:6", "context": "anbar qiyməti haqqında sual",
                "reply": "Uyğun cavab", "saved_at": 10,
            },
            {
                "lead_id": 2, "scope": "1:2", "context": "anbar qiyməti haqqında sual",
                "reply": "Başqa cavab", "saved_at": 20,
            },
        ]
        selected = examples._select_ai_reply_examples(
            {"pipeline_id": 5, "status_id": 6}, "Müştəri anbar qiyməti soruşur",
        )
        self.assertEqual(selected[0]["reply"], "Uyğun cavab")

    def test_unrelated_examples_are_not_returned(self):
        self.rows = [{"context": "restoran menyusu", "reply": "Cavab", "scope": "0:0"}]
        self.assertEqual(examples._select_ai_reply_examples({}, "anbar qalığı"), [])


class ReplyExamplesArchitectureTests(unittest.TestCase):
    def test_module_is_bounded_and_functions_moved(self):
        source = (ROOT / "legacy_chat_ai_examples.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertFalse({"_ai_reply_example_scope", "_ai_reply_tokens", "_select_ai_reply_examples"} & definitions)


if __name__ == "__main__":
    unittest.main()
