from pathlib import Path
import unittest
from tenant_web_assets import ASSETS

class KommoReferenceAssets(unittest.TestCase):
    def test_card_module_is_registered_and_app_uses_it(self):
        root=Path(__file__).resolve().parents[1]
        entry=('/assets/tenant-kommo-deal-card.js','tenant-kommo-deal-card.js','no-store')
        self.assertIn(entry,ASSETS)
        self.assertIn('src="'+entry[0]+'"',(root/'docs/tenant-app.html').read_text())
        self.assertIn('window.tenantKommoDealCard=',(root/'docs'/entry[1]).read_text())
