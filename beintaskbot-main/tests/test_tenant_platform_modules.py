"""Architecture checks for the split tenant persistence facade."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_MODULES = (
    "tenant_crm_store.py",
    "tenant_integration_store.py",
    "tenant_invite_store.py",
    "tenant_linear_account_store.py",
    "tenant_linear_news_store.py",
    "tenant_linear_news_publication_store.py",
    "tenant_member_store.py",
    "tenant_platform_common.py",
    "tenant_platform_core.py",
    "tenant_workflow_store.py",
)
SCHEMA_MODULES = (
    "tenant_platform_schema.py",
    "tenant_platform_schema_identity.py",
    "tenant_platform_schema_operations.py",
    "tenant_platform_schema_crm.py",
)


class TenantPlatformModuleTests(unittest.TestCase):
    def test_platform_is_import_only_compatibility_facade(self):
        tree = ast.parse((ROOT / "tenant_platform.py").read_text(encoding="utf-8-sig"))
        definitions = [node.name for node in tree.body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.assertEqual(definitions, [])

    def test_domain_modules_do_not_import_compatibility_facade(self):
        for filename in DOMAIN_MODULES:
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
                imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
                self.assertNotIn("tenant_platform", imports)

    def test_focused_domain_modules_remain_small(self):
        for filename in DOMAIN_MODULES:
            with self.subTest(filename=filename):
                lines = (ROOT / filename).read_text(encoding="utf-8-sig").splitlines()
                self.assertLessEqual(len(lines), 250)

    def test_schema_remains_additive_and_separate(self):
        source = "\n".join(
            (ROOT / filename).read_text(encoding="utf-8-sig")
            for filename in SCHEMA_MODULES
        )
        self.assertIn("CREATE TABLE IF NOT EXISTS", source)
        self.assertNotIn("DROP TABLE", source.upper())
        self.assertNotIn("TRUNCATE ", source.upper())

    def test_schema_slices_remain_small_and_share_one_commit(self):
        for filename in SCHEMA_MODULES:
            with self.subTest(filename=filename):
                lines = (ROOT / filename).read_text(encoding="utf-8-sig").splitlines()
                self.assertLessEqual(len(lines), 250)
        root_source = (ROOT / "tenant_platform_schema.py").read_text(encoding="utf-8-sig")
        self.assertEqual(root_source.count("conn.commit()"), 1)


if __name__ == "__main__":
    unittest.main()
