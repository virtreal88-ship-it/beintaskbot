"""HTTP catalogue and dependency wiring without a database or external writes."""
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_tenant_hot_orders import load, ROOT
from tenant_notice_transport import TelegramBot
from tenant_web_assets import ASSETS, register_assets
from tenant_ai_learning_mcp import routes as learning_routes

MCP_API=(('POST','/mcp/ai-learning'),('GET','/mcp/ai-learning'),
 ('GET','/.well-known/oauth-protected-resource/mcp/ai-learning'),
 ('GET','/.well-known/oauth-authorization-server'),('GET','/oauth/authorize'),
 ('POST','/oauth/authorize'),('POST','/oauth/token'),('POST','/oauth/revoke'))

EXPECTED_API = (("POST","/api/platform/crm/deals/complete"),("GET","/api/platform/crm/deal-approvals"),("POST","/api/platform/crm/deal-approvals"),("POST","/api/platform/crm/chat/send"),("POST","/api/platform/crm/chat/send-file"),("GET","/api/platform/diagnostics"),("GET","/api/platform/crm/chat/history"),("GET","/api/platform/crm/chat/attachment"),("GET","/api/platform/crm/chat/receipts"),("POST","/api/platform/crm/chat/receipts"),("GET","/api/platform/finance"),("POST","/api/platform/finance"),("GET","/api/platform/linear/settings"),("POST","/api/platform/linear/settings"),("GET","/api/platform/linear/tasks"),("POST","/api/platform/linear/tasks"),("GET","/api/platform/news"),("POST","/api/platform/news"),("GET","/api/platform/news/ai"),("POST","/api/platform/news/ai"),("GET","/api/platform/chat/ai/settings"),("POST","/api/platform/chat/ai/settings"),("GET","/api/platform/chat/ai/models"),("POST","/api/platform/chat/ai/model-probe"),("POST","/api/platform/chat/ai"),("GET","/api/platform/news/telegram/settings"),("POST","/api/platform/news/telegram/settings"),("GET","/api/platform/news/telegram"),("POST","/api/platform/news/telegram"),("GET","/api/platform/news/public/{company}"))
EXPECTED_API += (("GET","/api/platform/crm/chat/quote-receipt"),)
EXPECTED_API += tuple((method,path) for path,methods in [
 ('/api/platform/instructions',('GET','POST')),
 ('/api/platform/instructions/{lesson_id}',('GET','POST')),
 ('/api/platform/instructions/{lesson_id}/assets/{asset_id}',('GET',)),
 ('/api/instructions/shared/{token}',('GET',)),
 ('/api/instructions/shared/{token}/assets/{asset_id}',('GET',))] for method in methods)
EXPECTED_ASSETS = ["/assets/tenant-knowledge-campaigns.js","/assets/tenant-knowledge-pilot-admin.js","/assets/tenant-ai-expert-editor.js","/assets/tenant-ai-state.js","/assets/tenant-ai-state.css","/assets/tenant-agent-catalog.js","/assets/tenant-agent-library.js","/assets/tenant-agent-library.css","/assets/tenant-ai-testing.js","/assets/tenant-ai-testing.css","/assets/tenant-night-l10n.js","/assets/tenant-night-integration.js","/app/night-report","/assets/tenant-night-report.js","/assets/tenant-night-report.css","/assets/tenant-ai-reliability.js","/app/push-sw.js","/assets/tenant-push.js","/assets/task-date-fields.js","/assets/tenant-chat-import.js","/assets/tenant-chat-history.js","/assets/tenant-chat-attachments.js","/assets/tenant-chat-review.js","/assets/tenant-finance.js","/assets/tenant-chat-ai.js","/assets/tenant-chat-ai-settings.js","/assets/tenant-news-telegram-settings.js","/updates/{company}","/assets/tenant-news.js","/assets/tenant-news-ai-settings.js","/assets/tenant-linear-settings.js","/assets/tenant-linear-board.js","/assets/tenant-linear-rules.js","/assets/tenant-task-approvals.js","/assets/tenant-task-completion.js","/assets/tenant-deal-completion.js","/assets/tenant-diagnostics.js","/assets/tenant-chat-send.js","/assets/tenant-ai-agents.js","/assets/tenant-ai-teams.js","/assets/tenant-ai-teams.css","/assets/tenant-knowledge-center.js","/assets/tenant-knowledge-center.css","/assets/tenant-night-agent-template.js","/assets/tenant-support-inbox.js","/assets/tenant-operator-i18n.js","/assets/tenant-operator-dialogs.js","/assets/tenant-knowledge-candidates.js","/assets/tenant-workflow.js","/assets/tenant-chakra-ui.js","/assets/tenant-workspace.js","/assets/tenant-hot-order-settings.js","/assets/tenant-hot-orders.js","/assets/tenant-hot-order-completion.js","/assets/tenant-crm-sync.js","/assets/tenant-tasks.js"]

EXPECTED_ASSETS += ['/app/instructions','/app/ai-instructor','/assets/tenant-instructions.js','/assets/tenant-instructions.css','/assets/tenant-instructor-lesson.js','/assets/tenant-instructor-lesson.css','/assets/tenant-ai-instructor.js','/assets/tenant-ai-instructor.css']
EXPECTED_ASSETS += ['/assets/tenant-dialog-consultations.js','/assets/tenant-owner-asr-comparison.js']
EXPECTED_ASSETS += ['/assets/tenant-support-consultations.js']
EXPECTED_ASSETS += ['/assets/tenant-members-ui.js','/assets/tenant-members-ui.css']
EXPECTED_ASSETS += ['/assets/tenant-ai-administrator-approvals.js','/assets/tenant-request-coordinator.js','/assets/tenant-view-demand.js']

AI_API = tuple((method, '/api/platform/ai/' + path) for path, methods in [
    ('dialog-consultations/developer-marker', ('POST',)),
    ('dialog-consultations/destinations', ('GET',)),
    ('dialog-consultations/preview', ('POST',)),
    ('dialog-consultations', ('POST',)),
    ('dialog-consultations/replies', ('GET',)),
    ('dialog-consultations/{id}/dispatch', ('POST',)),
    ('dialog-consultations/{id}/revoke', ('POST',)),
    ('dialog-consultations/{id}/manual-reply', ('POST',)),
    ('dialog-consultations/replies/{id}/customer-replies', ('GET',)),
    ('dialog-consultations/replies/{id}/action', ('POST',)),
    ('dialog-consultations/replies/{id}/draft', ('POST',)),
    ('dialogs/asr-comparison', ('GET', 'POST')),
    ('dialogs/audio-transcription', ('GET', 'POST')),
    ('dialogs/read', ('POST',)),
    ('dialogs/read-all', ('POST',)),
    ('testing/sessions', ('GET','POST')),
    ('testing/historical-replay', ('GET','POST')),
    ('testing/sessions/{session_id}', ('GET',)), ('testing/sessions/{session_id}/turn', ('POST',)),
    ('testing/sessions/{session_id}/control', ('POST',)),
    ('simulation/settings', ('GET','PATCH')), ('simulation/runs', ('GET',)),
    ('simulation/runs/{run_id}', ('GET',)), ('simulation/runs/{run_id}/recheck', ('POST',)),
    ('simulation/issues', ('GET',)), ('simulation/issues/{run_id}/review', ('POST',)),
    ('dashboard', ('GET',)), ('dashboard/events', ('GET',)),
    ('instructor', ('GET',)), ('instructor/connections', ('GET','POST')),
    ('instructor/jobs', ('GET','POST')), ('instructor/jobs/{job_id}', ('GET','POST')),
    ('instructor/jobs/{job_id}/artifacts/{artifact_id}', ('GET',)),
    ('agents/settings', ('GET','PATCH')),
    ('agents/pilot-preflight', ('GET',)), ('agents/pilot/resume', ('POST',)), ('agents/pilot/reset', ('POST',)), ('agents/pilot/kwid-confirmation', ('POST',)),
    ('agents/pilot/owner-approval', ('POST',)),
    ('knowledge/deduplication', ('GET','POST')),
    ('administrator/approvals', ('GET','POST')),
    ('agents', ('GET','POST')), ('agents/options', ('GET',)),
    ('dialogs', ('GET',)), ('dialogs/avatar', ('GET',)), ('dialogs/history', ('GET',)), ('dialogs/copilot', ('POST',)), ('dialogs/display-name', ('POST',)),
    ('dialogs/agent-assignment', ('GET','POST')),
    ('dialogs/agent-assignment-bulk', ('GET','POST')),
    ('dialogs/legacy-continuation', ('GET','POST')),
    ('dialogs/legacy-recovery', ('GET','POST')),
    ('dialogs/group-coordination', ('POST',)), ('dialogs/group-staff', ('POST',)),
    ('night-report', ('GET',)), ('night-report/dialog', ('GET',)),
    ('night-report/tickets/{ticket_id}', ('GET',)),
    ('support-tickets', ('GET','POST')), ('support-tickets/complete', ('POST',)), ('support-tickets/{ticket_id}', ('GET','PATCH')),
    ('support-tickets/{ticket_id}/attachments', ('GET','POST')),
    ('support-tickets/{ticket_id}/messages', ('GET','POST')),
    ('support-tickets/{ticket_id}/summary', ('POST',)),
    ('internal-consultations', ('GET','POST')), ('internal-consultations/groups', ('GET',)),
    ('internal-consultations/{consultation_id}/group', ('GET','POST')), ('internal-consultations/{consultation_id}/reply', ('POST',)),
    ('channels', ('GET',)), ('state', ('GET',)), ('state/technical', ('GET',)),
    ('reliability', ('GET',)), ('handoffs', ('GET',)), ('diagnostics', ('GET',)), ('agents/{agent_id}', ('PATCH','DELETE')),
    ('agents/{agent_id}/delete-preview', ('GET',)),
    ('agents/{agent_id}/triggers', ('GET','POST')),
    ('agents/{agent_id}/triggers/{trigger_id}', ('PATCH','DELETE')),
    ('conversations/{conversation_id}/state', ('GET',)),
    ('conversations/for-lead/{lead_id}', ('GET',)),
    ('conversations/{conversation_id}/handoff', ('POST',)),
    ('conversations/{conversation_id}/pause', ('POST',)),
    ('conversations/{conversation_id}/resume', ('POST',)),
    ('knowledge-candidates', ('GET','POST')),
    ('knowledge-candidates/batches', ('POST',)),
    ('knowledge-candidates/batches/{batch_id}', ('GET',)),
    ('knowledge-candidates/{candidate_id}/boundaries', ('POST',)),
    ('knowledge-candidates/{candidate_id}/source-review', ('POST',)),
    ('knowledge-candidates/{candidate_id}/source-review', ('GET',)),
    ('knowledge-candidates/{candidate_id}/clarify', ('POST',)),
    ('knowledge-candidates/{candidate_id}/conversation', ('GET',)),
    ('knowledge-candidates/requests/{request_id}', ('GET',)),
    ('knowledge-candidates/{candidate_id}', ('GET','PATCH')),
    ('knowledge-candidates/{candidate_id}/approve', ('POST',)),
    ('knowledge-candidates/{candidate_id}/publish-guidance', ('POST',)),
    ('knowledge-candidates/{candidate_id}/correct-published', ('POST',)),
    ('knowledge-candidates/{candidate_id}/reject', ('POST',)),
    ('knowledge-candidates/{candidate_id}/delete', ('POST',)),
    ('knowledge-candidates/{candidate_id}/archive', ('POST',)),
    ('knowledge-candidates/{candidate_id}/restore', ('POST',)),
    ('knowledge-candidates/{candidate_id}/mark-knowledge-published', ('POST',)),
    ('knowledge-candidates/{candidate_id}/reconcile-approved-publication', ('POST',)),
    ('knowledge', ('GET','POST')), ('knowledge/upload', ('POST',)),
    ('knowledge/{source_id}', ('PATCH','DELETE')),
    ('knowledge/{source_id}/restore', ('POST',)),
    ('simulation/settings', ('GET','PATCH')), ('simulation/runs', ('GET',)),
    ('simulation/runs/{run_id}', ('GET',)), ('simulation/runs/{run_id}/recheck', ('POST',)),
    ('simulation/issues', ('GET',)), ('simulation/issues/{run_id}/review', ('POST',)),
    ('dashboard', ('GET',)), ('dashboard/events', ('GET',)),
] for method in methods)
FACTORIES = ('instruction_handler', 'finance_handler', 'linear_handler', 'tasks_handler', 'news_handler',
    'news_telegram_handler', 'chat_ai_handler', 'chat_ai_model_handler', 'chat_send_handler', 'chat_receipt_handler', 'chat_upload_handler',
    'chat_history_handler', 'chat_attachment_handler', 'chat_review_handler', 'historical_replay_handler',
    'diagnostics_handler', 'deal_completion_handler', 'ai_agents_handler', 'administrator_handler', 'support_handler', 'knowledge_candidates_handler', 'operator_handler', 'dialog_consultations_handler', 'owner_asr_comparison_handler', 'dialog_audio_transcription_handler', 'night_report_handler', 'testing_handler', 'simulation_handler', 'ai_state_handler', 'ai_instructor_handler')


class Routes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.factories = {name: Mock(side_effect=lambda *args, **kwargs: AsyncMock(return_value=web.json_response({'success': True}))) for name in FACTORIES}
        imports = {node.module for node in ast.parse((ROOT/'tenant_web_routes.py').read_text()).body
                   if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('tenant_')}
        self.provider = Mock(return_value=AsyncMock())
        self.module = load('tenant_web_routes', {**self.factories, 'deal_provider': self.provider, 'learning_mcp_routes':learning_routes,
                           'TelegramBot': TelegramBot}, imports)
        self.context = self.module.TenantWebContext(member=Mock(), origin='https://crm.pro.az',
            logger=Mock(), crm_request=AsyncMock(), wait_slot=Mock(), bot=Mock())
        self.app = web.Application()
        self.module.register_feature_routes(self.app, self.context)

    async def resolve(self, method, path):
        request = make_mocked_request(method, path, app=self.app)
        match = await self.app.router.resolve(request)
        return request, match

    async def test_exact_previous_api_methods_and_automatic_head(self):
        actual = {(route.method, route.resource.canonical) for route in self.app.router.routes()}
        expected = set(EXPECTED_API)
        base='/api/platform/ai/knowledge-candidates/campaigns'
        expected |= {(method,base+suffix) for suffix in ('','/preview','/{campaign_id}') for method in ('GET','HEAD')}
        expected |= {('POST',base+suffix) for suffix in ('','/{campaign_id}/start','/{campaign_id}/control')}
        expected |= {(method,base+'/{campaign_id}/case-continuation') for method in ('GET','HEAD','POST')}
        expected.add(('POST',base+'/{campaign_id}/current-media-check'))

        expected.add(('POST','/api/platform/ai/knowledge-candidates/{candidate_id}/improve'))
        expected |= {('POST','/api/platform/ai/knowledge-candidates/'+name) for name in ('operator-preview','operator-submit')}
        expected |= {('HEAD', path) for method, path in EXPECTED_API if method == 'GET'}
        expected |= set(AI_API)
        expected |= set(MCP_API)
        expected |= {('HEAD',path) for method,path in MCP_API if method=='GET'}
        expected |= {('HEAD', path) for method, path in AI_API if method == 'GET'}
        self.assertEqual(actual, expected)
        for method, path in EXPECTED_API:
            request, match = await self.resolve(method, path.replace('{company}', 'demo').replace('{lesson_id}', '00000000-0000-0000-0000-000000000001').replace('{token}', 'testtoken').replace('{asset_id}', 'test.jpg'))
            self.assertIsNone(match.http_exception)
            self.assertEqual((await match.handler(request)).status, 200)

    async def test_retired_real_dialog_test_and_draft_apis_are_not_routed(self):
        for method, path in [
            ('GET', '/api/platform/ai/testing/kommo-context'),
            ('PATCH', '/api/platform/ai/agents/test-mode'),
            ('POST', '/api/platform/ai/agents/test-mode/verify'),
            ('POST', '/api/platform/ai/agents/test-mode/discover'),
            ('GET', '/api/platform/ai/agents/drafts'),
            ('POST', '/api/platform/ai/agents/drafts/id/send'),
        ]:
            _, match = await self.resolve(method, path)
            self.assertIsNotNone(match.http_exception)
            self.assertIn(match.http_exception.status, (404, 405))

    async def test_methods_not_widened(self):
        for method, path in [('GET', '/api/platform/crm/chat/send'),
                             ('POST', '/api/platform/diagnostics'),
                             ('DELETE', '/api/platform/news')]:
            _, match = await self.resolve(method, path)
            self.assertEqual(match.http_exception.status, 405)

    def test_identity_provider_and_origin_passed_unchanged(self):
        for name in FACTORIES:
            for call in self.factories[name].call_args_list:
                self.assertIs(call.args[0], self.context.member)
        self.factories['chat_send_handler'].assert_called_once_with(self.context.member,
            self.context.origin, self.context.crm_request, self.context.wait_slot, self.context.logger)
        self.factories['chat_upload_handler'].assert_called_once_with(self.context.member,
            self.context.origin, self.context.crm_request, self.context.wait_slot, self.context.logger)
        self.provider.assert_called_with(self.context.crm_request, self.context.wait_slot)
        self.assertEqual(self.provider.call_count, 2)
        self.context.member.assert_not_called()
        self.context.crm_request.assert_not_called()
        self.context.bot.assert_not_called()
        self.factories['historical_replay_handler'].assert_called_once_with(self.context.member,self.context.origin,self.context.logger)

    def test_special_modes_remain_explicit(self):
        self.assertEqual([call.kwargs for call in self.factories['news_handler'].call_args_list],
                         [{}, {'ai': True}, {'public': True}])
        self.assertEqual([call.kwargs for call in self.factories['chat_ai_handler'].call_args_list],
                         [{'settings': True}, {}])
        self.assertEqual([call.kwargs for call in self.factories['deal_completion_handler'].call_args_list],
                         [{}, {'review': True}])
        self.assertEqual([call.kwargs for call in self.factories['news_telegram_handler'].call_args_list],
                         [{'configuration': True}, {}])

    def test_factory_failure_does_not_mount_partial_group(self):
        app = web.Application()
        self.factories['diagnostics_handler'].side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            self.module.register_feature_routes(app, self.context)
        self.assertEqual(list(app.router.routes()), [])


class Assets(unittest.IsolatedAsyncioTestCase):
    async def test_exact_asset_catalogue_paths_files_headers_and_head(self):
        app = web.Application()
        register_assets(app)
        self.assertEqual({url for url, _, _ in ASSETS}, set(EXPECTED_ASSETS))
        self.assertEqual(len(ASSETS), len(EXPECTED_ASSETS))
        expected = {(method, url) for url in EXPECTED_ASSETS for method in ('GET', 'HEAD')}
        self.assertEqual({(r.method, r.resource.canonical) for r in app.router.routes()}, expected)
        for url, filename, cache in ASSETS:
            self.assertTrue((ROOT/'docs'/filename).is_file())
            request = make_mocked_request('GET', url.replace('{company}', 'malicious-value'), app=app)
            match = await app.router.resolve(request)
            response = await match.handler(request)
            self.assertEqual(response._path, ROOT/'docs'/filename)
            self.assertEqual(response.headers.get('Cache-Control'), cache)
            self.assertEqual(response.headers.get('Service-Worker-Allowed'), '/app/' if url == '/app/push-sw.js' else None)

    def test_bot_composes_separate_legacy_and_tenant_route_groups(self):
        source = (ROOT/'bot.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        start = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'start_webhook_server')
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        text = ast.get_source_segment(source, start)
        tenant_text = ast.get_source_segment(source, functions['_register_tenant_web_routes'])
        legacy_text = ast.get_source_segment(source, functions['_register_legacy_web_routes'])
        self.assertIn('_register_legacy_web_routes(app_web)', text)
        self.assertIn('_register_tenant_web_routes(app_web)', text)
        self.assertEqual(tenant_text.count('register_feature_routes('), 1)
        self.assertEqual(tenant_text.count('register_assets('), 1)
        for method, url in EXPECTED_API:
            self.assertNotIn("'"+url+"'", tenant_text)
            self.assertNotIn('"'+url+'"', tenant_text)
        self.assertIn('"/webapp", serve_webapp', tenant_text)
        self.assertIn('"/api/platform/integrations/kommo/callback"', tenant_text)
        self.assertIn('"/api/platform/register"', tenant_text)
        self.assertIn('"/api/action", handle_api_action', legacy_text)
        self.assertIn("handle_search_contacts", functions)
