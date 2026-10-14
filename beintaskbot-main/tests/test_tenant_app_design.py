"""Guard the /app-only visual layer and reuse of existing action handlers."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AppDesignTests(unittest.TestCase):
    def test_shell_is_opt_in_and_has_no_backend_calls(self):
        shell = (ROOT / 'ui/src/app-shell.js').read_text(encoding='utf-8')
        self.assertIn("if (document.body.dataset.appUi === 'kwid')", shell)
        self.assertNotIn('fetch(', shell)
        self.assertNotIn('/api/', shell)
        self.assertIn('target.click()', shell)
        self.assertIn("target&&!target.hidden&&target.style.display!=='none'", shell)

    def test_only_app_loads_new_design(self):
        app = (ROOT / 'docs/tenant-app.html').read_text(encoding='utf-8')
        legacy = (ROOT / 'docs/index.html').read_text(encoding='utf-8')
        self.assertIn('data-app-ui="kwid"', app)
        self.assertNotIn('data-app-ui="kwid"', legacy)
        self.assertNotIn('app-shell', legacy)
        self.assertNotIn('tenant-chakra-ui.js', legacy)

    def test_app_applies_critical_theme_before_the_module_bundle(self):
        app = (ROOT / 'docs/tenant-app.html').read_text(encoding='utf-8')
        self.assertIn('id="app-design-critical"', app)
        self.assertIn("localStorage.getItem('tenant-app-theme')", app)
        self.assertLess(app.index('id="app-design-critical"'), app.index('tenant-chakra-ui.js'))
        self.assertIn('background:var(--app-bg)!important', app)

    def test_theme_and_responsive_preferences_are_presentation_only(self):
        shell = (ROOT / 'ui/src/app-shell.js').read_text(encoding='utf-8')
        css = (ROOT / 'ui/src/app-theme.css').read_text(encoding='utf-8')
        for mode in ('light', 'dark', 'system'):
            self.assertIn(mode, shell)
        self.assertIn("sidebar.inert=mobile.matches&&!open", shell)
        self.assertIn('prefers-reduced-motion:reduce', css)
        self.assertIn('max-width:1023px', css)
        self.assertIn('max-width:639px', css)
        self.assertIn('--app-sidebar:288px', css)
        self.assertIn('--app-sidebar:88px', css)

    def test_existing_dialog_actions_and_settings_panels_are_preserved(self):
        source = (ROOT / 'ui/src/main.jsx').read_text(encoding='utf-8')
        workspace = (ROOT / 'docs/tenant-workspace.js').read_text(encoding='utf-8')
        self.assertIn('closeOnInteractOutside={false}', source)
        self.assertIn('id="integrationSettingsHost"', source)
        self.assertIn('onClick={()=>setTab("settings")}', source)
        self.assertIn("panel.id === 'chakra-settings-root'", workspace)


if __name__ == '__main__':
    unittest.main()
