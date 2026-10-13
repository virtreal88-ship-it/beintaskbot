"""HTTP catalogue and dependency wiring without a database or external writes."""
import ast
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_tenant_hot_orders import load, ROOT
from tenant_notice_transport import TelegramBot
from tenant_linear_policy import TenantLinearError
from tenant_web_assets import ASSETS, register_assets

EXPECTED_API = (("POST","/api/platform/crm/deals/complete"),("GET","/api/platform/crm/deal-approvals"),("POST","/api/platform/crm/deal-approvals"),("POST","/api/platform/crm/chat/send"),("POST","/api/platform/crm/chat/send-file"),("GET","/api/platform/diagnostics"),("GET","/api/platform/crm/chat/history"),("GET","/api/platform/crm/chat/attachment"),("GET","/api/platform/crm/chat/receipts"),("POST","/api/platform/crm/chat/receipts"),("GET","/api/platform/finance"),("POST","/api/platform/finance"),("GET","/api/platform/linear/settings"),("POST","/api/platform/linear/settings"),("GET","/api/platform/linear/tasks"),("POST","/api/platform/linear/tasks"),("GET","/api/platform/news"),("POST","/api/platform/news"),("GET","/api/platform/news/ai"),("POST","/api/platform/news/ai"),("GET","/api/platform/chat/ai/settings"),("POST","/api/platform/chat/ai/settings"),("POST","/api/platform/chat/ai"),("GET","/api/platform/news/telegram/settings"),("POST","/api/platform/news/telegram/settings"),("GET","/api/platform/news/telegram"),("POST","/api/platform/news/telegram"),("GET","/api/platform/news/public/{company}"))
EXPECTED_ASSETS = ["/app/push-sw.js","/assets/tenant-push.js","/assets/task-date-fields.js","/assets/tenant-chat-import.js","/assets/tenant-chat-history.js","/assets/tenant-chat-attachments.js","/assets/tenant-chat-review.js","/assets/tenant-finance.js","/assets/tenant-chat-ai.js","/assets/tenant-chat-ai-settings.js","/assets/tenant-ai-agents.js","/assets/tenant-news-telegram-settings.js","/updates/{company}","/assets/tenant-news.js","/assets/tenant-news-ai-settings.js","/assets/tenant-linear-settings.js","/assets/tenant-linear-board.js","/assets/tenant-linear-rules.js","/assets/tenant-task-approvals.js","/assets/tenant-task-completion.js","/assets/tenant-deal-completion.js","/assets/tenant-diagnostics.js","/assets/tenant-chat-send.js","/assets/tenant-workflow.js","/assets/tenant-chakra-ui.js","/assets/tenant-workspace.js","/assets/tenant-hot-order-settings.js","/assets/tenant-hot-orders.js","/assets/tenant-hot-order-completion.js","/assets/tenant-crm-sync.js","/assets/tenant-tasks.js"]
FACTORIES = ('finance_handler', 'linear_handler', 'tasks_handler', 'news_handler',
    'news_telegram_handler', 'chat_ai_handler', 'chat_send_handler', 'chat_upload_handler',
    'chat_history_handler', 'chat_attachment_handler', 'chat_review_handler',
    'diagnostics_handler', 'deal_completion_handler', 'ai_agents_handler')

EXPECTED_AI_API = {
    ('GET','/api/platform/ai/agents/settings'), ('PATCH','/api/platform/ai/agents/settings'),
    ('GET','/api/platform/ai/agents/test-mode'), ('PATCH','/api/platform/ai/agents/test-mode'),
    ('POST','/api/platform/ai/agents/test-mode/verify'),
    ('POST','/api/platform/ai/agents/test-mode/discover'),
    ('GET','/api/platform/ai/agents'), ('POST','/api/platform/ai/agents'),
    ('GET','/api/platform/ai/agents/drafts'),
    ('POST','/api/platform/ai/agents/drafts/{draft_id}/review'),
    ('POST','/api/platform/ai/agents/drafts/{draft_id}/send'),
    ('GET','/api/platform/ai/handoffs'),
    ('PATCH','/api/platform/ai/agents/{agent_id}'), ('DELETE','/api/platform/ai/agents/{agent_id}'),
    ('GET','/api/platform/ai/agents/{agent_id}/triggers'), ('POST','/api/platform/ai/agents/{agent_id}/triggers'),
    ('PATCH','/api/platform/ai/agents/{agent_id}/triggers/{trigger_id}'),
    ('DELETE','/api/platform/ai/agents/{agent_id}/triggers/{trigger_id}'),
    ('GET','/api/platform/ai/conversations/{conversation_id}/state'),
    ('POST','/api/platform/ai/conversations/{conversation_id}/handoff'),
    ('POST','/api/platform/ai/conversations/{conversation_id}/resume'),
    ('GET','/api/platform/ai/knowledge'), ('POST','/api/platform/ai/knowledge'),
    ('POST','/api/platform/ai/knowledge/upload'),
    ('PATCH','/api/platform/ai/knowledge/{source_id}'), ('DELETE','/api/platform/ai/knowledge/{source_id}'),
}


class Routes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.factories = {name: Mock(side_effect=lambda *args, **kwargs: AsyncMock(return_value=web.json_response({'success': True}))) for name in FACTORIES}
        self.list_agents = Mock(return_value=[])
        imports = {node.module for node in ast.parse((ROOT/'tenant_web_routes.py').read_text()).body
                   if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('tenant_')}
        self.provider = Mock(return_value=AsyncMock())
        self.module = load('tenant_web_routes', {**self.factories, 'deal_provider': self.provider,
                           'TelegramBot': TelegramBot, 'list_agents': self.list_agents,
                           'TenantLinearError': TenantLinearError}, imports)
        self.context = self.module.TenantWebContext(member=Mock(), origin='https://crm.pro.az',
            logger=Mock(), crm_request=AsyncMock(), wait_slot=Mock(), bot=Mock())
        self.app = web.Application()
        with patch.dict(os.environ, {'AI_AGENTS_ENABLED':'false'}):
            self.module.register_feature_routes(self.app, self.context)

    async def resolve(self, method, path):
        request = make_mocked_request(method, path, app=self.app)
        match = await self.app.router.resolve(request)
        return request, match

    async def test_exact_previous_api_methods_and_automatic_head(self):
        actual = {(route.method, route.resource.canonical) for route in self.app.router.routes()}
        expected = set(EXPECTED_API)
        expected |= {('HEAD', path) for method, path in EXPECTED_API if method == 'GET'}
        self.assertEqual(actual, expected)
        for method, path in EXPECTED_API:
            request, match = await self.resolve(method, path.replace('{company}', 'demo'))
            self.assertIsNone(match.http_exception)
            self.assertEqual((await match.handler(request)).status, 200)

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

    def test_ai_agents_routes_are_complete_and_explicitly_gated(self):
        app = web.Application()
        with patch.dict(os.environ, {'AI_AGENTS_ENABLED':'true'}):
            self.module.register_feature_routes(app, self.context)
        actual = {(route.method, route.resource.canonical) for route in app.router.routes()}
        actual = {item for item in actual if item[1].startswith('/api/platform/ai/')}
        expected = EXPECTED_AI_API | {('HEAD', path) for method, path in EXPECTED_AI_API if method == 'GET'}
        self.assertEqual(actual, expected)
        self.factories['ai_agents_handler'].assert_any_call(self.context.member,self.context.origin,
            self.context.logger,'draft_send',self.context.crm_request,self.context.wait_slot)

    def test_ai_agents_routes_can_be_reused_for_authenticated_webapp_profile(self):
        routes = self.module.ai_agent_routes(self.context, prefix='/api/webapp/ai/')
        actual = {(method, path) for method, path, _handle in routes}
        expected = {(method, path.replace('/api/platform/ai/', '/api/webapp/ai/'))
                    for method, path in EXPECTED_AI_API if method != 'HEAD'}
        self.assertEqual(actual, expected)
        self.assertEqual(len({handle for _method, _path, handle in routes}), len(routes))

    async def test_webapp_profile_requires_ai_management_permission(self):
        member = {'tenant_id':'tenant-a','telegram_id':7,'role':'owner','tenant_name':'Company',
                  'tenant_status':'active','secrets':'must not be returned'}
        handler = self.module.ai_agent_profile_handler(lambda _request: member, Mock())
        response = await handler(make_mocked_request('GET','/api/webapp/ai/profile'))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
        self.assertEqual(response.body, b'{"success": true, "member": {"tenant_id": "tenant-a", "telegram_id": 7, "role": "owner", "tenant_name": "Company"}}')
        self.list_agents.assert_called_once_with(member)

    async def test_webapp_profile_fails_closed_without_authorized_member(self):
        handler = self.module.ai_agent_profile_handler(lambda _request: None, Mock())
        response = await handler(make_mocked_request('GET','/api/webapp/ai/profile'))
        self.assertEqual(response.status, 403)
        self.list_agents.assert_not_called()


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
