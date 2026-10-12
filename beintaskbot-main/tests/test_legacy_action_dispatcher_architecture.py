"""Prevent the legacy action dispatcher from becoming a monolith again."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LegacyActionDispatcherArchitectureTests(unittest.TestCase):
    def test_dispatcher_is_small_and_large_actions_are_named_handlers(self):
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        dispatcher = functions["handle_api_action"]
        self.assertLessEqual(dispatcher.end_lineno - dispatcher.lineno + 1, 150)
        expected = {
            "_handle_legacy_add_note",
            "_handle_legacy_task",
            "_handle_legacy_stage",
            "_handle_legacy_create_deal",
            "_handle_legacy_update_task",
            "_handle_legacy_complete_task",
        }
        self.assertTrue(expected.issubset(functions))
        source = ast.get_source_segment(
            (ROOT / "bot.py").read_text(encoding="utf-8-sig"), dispatcher
        )
        for name in expected:
            self.assertIn(f"await {name}(", source)


if __name__ == "__main__":
    unittest.main()
