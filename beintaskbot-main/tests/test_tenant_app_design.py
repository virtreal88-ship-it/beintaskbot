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

    def test_native_connect_deal_does_not_claim_a_kommo_id(self):
        app = (ROOT / 'docs/tenant-app.html').read_text(encoding='utf-8')
        self.assertIn("x.raw?.source==='connect'?'Beintaskbot ID':'Kommo ID'", app)

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

    def test_topbar_instructions_and_single_localized_crm_nav(self):
        shell = (ROOT / 'ui/src/app-shell.js').read_text(encoding='utf-8')
        app = (ROOT / 'docs/tenant-app.html').read_text(encoding='utf-8')
        i18n = (ROOT / 'docs/tenant-operator-i18n.js').read_text(encoding='utf-8')
        self.assertIn('data-app-instructions', shell)
        self.assertIn("pages:['tasks','customers','companies','deals','hot_orders']", shell)
        self.assertIn("button.querySelector('[data-crm-nav-label]')", shell)
        self.assertIn('extra!==label)extra.remove()', shell)
        self.assertIn("customers:'Контакты'", shell)
        self.assertIn("companies:'Şirkətlər'", shell)
        self.assertNotIn('instruction-library-nav', app)

    def test_support_case_field_labels_use_accent_and_preserve_click_editing(self):
        app = (ROOT / 'docs/tenant-app.html').read_text(encoding='utf-8')
        self.assertIn('.case-content-field>span{color:var(--brand);font-size:14.5px;font-weight:600}', app)
        self.assertIn('.case-field-preview{display:block', app)
        self.assertIn('font-size:14.5px;font-weight:500;line-height:1.55', app)
        candidates = (ROOT / 'docs/tenant-knowledge-candidates.js').read_text(encoding='utf-8')
        self.assertIn('data-case-preview', candidates)
        self.assertIn('openInlineCaseEditor', candidates)

    def test_existing_dialog_actions_and_settings_panels_are_preserved(self):
        source = (ROOT / 'ui/src/main.jsx').read_text(encoding='utf-8')
        workspace = (ROOT / 'docs/tenant-workspace.js').read_text(encoding='utf-8')
        self.assertIn('closeOnInteractOutside={false}', source)
        self.assertIn('id="integrationSettingsHost"', source)
        self.assertIn('onClick={()=>setTab("settings")}', source)
        self.assertIn("panel.id === 'chakra-settings-root'", workspace)

    def test_ai_agents_is_a_transport_independent_app_module(self):
        app = (ROOT / 'docs/tenant-app.html').read_text(encoding='utf-8')
        routes = (ROOT / 'tenant_web_routes.py').read_text(encoding='utf-8')
        assets = (ROOT / 'tenant_web_assets.py').read_text(encoding='utf-8')
        self.assertIn('id="aiAgentsNav" data-view="ai_agents"', app)
        self.assertIn('id="view-ai_agents"', app)
        self.assertIn('/assets/tenant-ai-agents.js', app)
        self.assertIn('/assets/tenant-night-agent-template.js', app)
        self.assertIn('id="aiNightTemplateOpen"', app)
        self.assertIn("('agents', ai_agents_handler(member, origin, logger, 'agents'), ('GET', 'POST'))", routes)
        self.assertIn('routes.extend(ai_agent_routes(context))', routes)
        self.assertIn("('/assets/tenant-ai-agents.js', 'tenant-ai-agents.js'", assets)
        self.assertIn("('/assets/tenant-night-agent-template.js', 'tenant-night-agent-template.js'", assets)
        script = (ROOT / 'docs/tenant-ai-agents.js').read_text(encoding='utf-8')
        self.assertNotIn("if (nav) { nav.hidden = true", script)
        self.assertIn("existing?.scopes||[]", script)
        self.assertIn("data-ai-overview-create>${t('createAgent')}", script)


if __name__ == '__main__':
    unittest.main()
