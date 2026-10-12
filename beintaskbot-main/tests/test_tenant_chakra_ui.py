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
        for component in ("ChakraProvider", "TasksTable", "DealKanban", "DealTable", "SettingsHub", "MemberDialog", "IntegrationMarketplace"):
            self.assertIn(component, source)
        self.assertIn('closeOnInteractOutside={false}', source)

    def test_market_is_separate_navigation_surface_with_expected_catalog(self):
        page = (ROOT / "docs" / "tenant-app.html").read_text(encoding="utf-8")
        source = (ROOT / "ui" / "src" / "main.jsx").read_text(encoding="utf-8")
        self.assertIn('data-view="integrations"', page)
        self.assertIn('id="view-integrations"', page)
        for provider in ("Kommo", "Linear", "OpenAI", "Telegram", "Wappi", "BeinSystems ERP"):
            self.assertIn(provider, source)

    def test_integration_configuration_lives_in_market_dialog(self):
        source = (ROOT / "ui" / "src" / "main.jsx").read_text(encoding="utf-8")
        workspace = (ROOT / "docs" / "tenant-workspace.js").read_text(encoding="utf-8")
        self.assertIn('setTab("connection")', source)
        self.assertIn('>Qoşulma</Button>', source)
        self.assertIn('>Ayarlar</Button>', source)
        self.assertIn('id="integrationSettingsHost"', source)
        self.assertIn("tenant-integration-panel", workspace)
        self.assertIn("external-integration", workspace)
        self.assertNotIn("[['integrations','İnteqrasiyalar'", workspace)

    def test_prebuilt_asset_exists_for_python_only_deploy(self):
        bundle = ROOT / "docs" / "tenant-chakra-ui.js"
        self.assertTrue(bundle.is_file())
        self.assertGreater(bundle.stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
