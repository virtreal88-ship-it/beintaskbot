"""Architecture checks for the split legacy persistent storage."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "legacy_document_storage.py",
    "legacy_finance_ledger.py",
    "legacy_kpi_storage.py",
    "legacy_push_storage.py",
)


class LegacyStorageModuleTests(unittest.TestCase):
    def test_gh_storage_is_import_only_compatibility_facade(self):
        tree = ast.parse((ROOT / "gh_storage.py").read_text(encoding="utf-8-sig"))
        definitions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        self.assertEqual(definitions, [])

    def test_storage_modules_are_small_and_do_not_import_facade(self):
        for filename in MODULES:
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8-sig")
                self.assertLessEqual(len(source.splitlines()), 280)
                imports = {
                    node.module
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.ImportFrom)
                }
                self.assertNotIn("gh_storage", imports)

    def test_domain_modules_share_only_document_storage(self):
        for filename in MODULES[1:]:
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            self.assertIn("from legacy_document_storage import", source)


if __name__ == "__main__":
    unittest.main()
