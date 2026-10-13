from pathlib import Path
import ast
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "railway_ai_agents_clone_check" / "tenant_audit.py"


class TenantAuditSafetyTests(unittest.TestCase):
    def test_audit_has_no_dump_restore_or_migration_path(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertNotIn("subprocess", imported)
        called_names = {
            node.func.id if isinstance(node.func, ast.Name)
            else node.func.attr if isinstance(node.func, ast.Attribute)
            else ""
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        self.assertFalse(called_names.intersection({"pg_dump", "pg_restore", "migrate", "ensure_schema"}))

    def test_database_commands_are_only_select_and_read_only_guard(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        sql_literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"execute", "executemany"} and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        sql_literals.append(" ".join(arg.value.split()).casefold())
                if node.func.attr == "SQL" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        sql_literals.append(" ".join(arg.value.split()).casefold())
        self.assertTrue(sql_literals)
        self.assertTrue(any(query.startswith("set transaction read only") for query in sql_literals))
        for query in sql_literals:
            self.assertTrue(query.startswith(("select ", "set transaction read only")), query)

    def test_source_identity_is_pinned_to_readonly_role_and_railway_database(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('EXPECTED_DATABASE = "railway"', source)
        self.assertIn('EXPECTED_ROLE = "ai_agents_backup_ro"', source)
        self.assertIn('"-c default_transaction_read_only=on"', source)

    def test_failure_diagnostics_are_stage_and_sqlstate_only(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('stage={CURRENT_STAGE}', source)
        self.assertIn('error={type(exc).__name__}, sqlstate={state}', source)
        self.assertIn('details suppressed', source)
        self.assertIn('authentication_failed', source)
        self.assertIn('dns_resolution_failed', source)
        self.assertIn('connection_timeout', source)
        self.assertNotIn('print(str(exc))', source)


if __name__ == "__main__":
    unittest.main()
