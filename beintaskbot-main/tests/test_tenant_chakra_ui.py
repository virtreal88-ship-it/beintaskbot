import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TenantChakraUiTests(unittest.TestCase):
    def test_app_loads_local_chakra_bundle_without_cdn(self):
        page = (ROOT / "docs" / "tenant-app.html").read_text(encoding="utf-8")
        self.assertIn('<script type="module" src="/assets/tenant-chakra-ui.js"></script>', page)
        self.assertNotIn("unpkg.com", page)
        self.assertNotIn("cdn.jsdelivr.net", page)

    def test_chakra_source_covers_core_crm_surfaces(self):
        source = (ROOT / "ui" / "src" / "main.jsx").read_text(encoding="utf-8")
        for component in ("ChakraProvider", "TasksTable", "DealKanban", "DealTable", "SettingsHub", "MemberDialog"):
            self.assertIn(component, source)
        self.assertIn('closeOnInteractOutside={false}', source)

    def test_prebuilt_asset_exists_for_python_only_deploy(self):
        bundle = ROOT / "docs" / "tenant-chakra-ui.js"
        self.assertTrue(bundle.is_file())
        self.assertGreater(bundle.stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
