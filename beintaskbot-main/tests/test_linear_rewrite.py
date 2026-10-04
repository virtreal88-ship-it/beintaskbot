"""Verify the editor instruction and response handling without paid API calls."""
import ast
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


class LinearRewriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8-sig"))
        cls.code = compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_linear_ai_rewrite"], type_ignores=[]), "<rewrite>", "exec")

    def setUp(self):
        self.create = Mock(return_value=types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="  Clear task text  "))]))
        self.ns = {"LLM_MODEL": "configured-model", "llm_client": types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create)))}
        exec(self.code, self.ns)

    def test_draft_is_rewritten_not_answered(self):
        self.assertEqual(self.ns["_linear_ai_rewrite"]("Почему кнопка не работает?", "Button"), "Clear task text")
        messages = self.create.call_args.kwargs["messages"]
        instruction = messages[0]["content"]
        for rule in ("proqramçı", "tərcümə etmə", "suallara cavab vermə", "uydurma", "kod yazma"):
            self.assertIn(rule, instruction)
        self.assertIn("Почему кнопка не работает?", messages[1]["content"])

    def test_empty_input_does_not_call_api(self):
        with self.assertRaises(RuntimeError):
            self.ns["_linear_ai_rewrite"](" ")
        self.create.assert_not_called()

    def test_empty_response_fails(self):
        self.create.return_value = types.SimpleNamespace(choices=[])
        with self.assertRaises(RuntimeError):
            self.ns["_linear_ai_rewrite"]("Draft")


if __name__ == "__main__":
    unittest.main()
