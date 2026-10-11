"""Architecture checks for extracted legacy runtime configuration."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LegacyRuntimeConfigTests(unittest.TestCase):
    def test_config_has_no_network_clients_or_mutable_process_caches(self):
        source = (ROOT / "legacy_runtime_config.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertNotIn("openai", imports)
        self.assertNotIn("requests", names)
        self.assertNotIn("llm_client", names)
        self.assertFalse(any(name.endswith("_CACHE") for name in names))

    def test_bot_composes_config_and_transport_instead_of_implementing_them(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        definitions = {node.name for node in tree.body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        self.assertNotIn("_required_env", definitions)
        self.assertNotIn("_kommo_wait", definitions)
        self.assertNotIn("_paced_session_request", definitions)
        self.assertIn("install_legacy_kommo_transport(", source)


if __name__ == "__main__":
    unittest.main()
