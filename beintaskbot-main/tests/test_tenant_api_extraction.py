"""Architectural guardrails for tenant HTTP boundary extraction."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TenantApiExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        cls.tree = ast.parse(cls.source)

    def test_bot_has_no_inline_platform_http_handlers(self):
        inline_handlers = [
            node.name for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("handle_platform_")
        ]
        self.assertEqual(inline_handlers, [])

    def test_remaining_apis_are_composed_once_at_startup(self):
        self.assertIn("hot_order_api = tenant_hot_order_handler(", self.source)
        self.assertIn("push_api = tenant_push_handler(", self.source)
        self.assertIn("add_get('/api/platform/hot-orders', hot_order_api)", self.source)
        self.assertIn("add_post('/api/platform/push/devices', push_api)", self.source)


if __name__ == "__main__":
    unittest.main()
