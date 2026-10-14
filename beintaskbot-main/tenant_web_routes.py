"""Compose modular SaaS APIs once, with explicit runtime dependencies.

No bot import, tenant lookup, database access or network call during mounting.
Authentication and resource policy remain in the existing API/store modules.
"""
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from logging import Logger
import os
from aiohttp import web
from tenant_notice_transport import TelegramBot
from tenant_finance_api import finance_handler
from tenant_linear_api import linear_handler
from tenant_linear_tasks_api import tasks_handler
from tenant_news_api import news_handler
from tenant_news_telegram_api import handler as news_telegram_handler
from tenant_chat_ai_api import handler as chat_ai_handler
from tenant_chat_send_api import handler as chat_send_handler
from tenant_chat_upload_api import handler as chat_upload_handler
from tenant_chat_history_api import handler as chat_history_handler
from tenant_chat_attachment_api import handler as chat_attachment_handler
from tenant_chat_review_api import handler as chat_review_handler
from tenant_diagnostics_api import handler as diagnostics_handler
from tenant_deal_completion_api import handler as deal_completion_handler
from tenant_deal_completion_provider import provider as deal_provider
from tenant_ai_agents_api import handler as ai_agents_handler
from tenant_ai_agents_store import list_agents
from tenant_linear_policy import TenantLinearError

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
MemberReader = Callable[[web.Request], dict | None]
CrmRequest = Callable[..., Awaitable[dict]]


@dataclass(frozen=True)
class TenantWebContext:
    member: MemberReader
    origin: str
    logger: Logger
    crm_request: CrmRequest
    wait_slot: Callable[[str], None]
    bot: Callable[[], TelegramBot | None]


def feature_routes(context: TenantWebContext) -> list[tuple[str, str, Handler]]:
    """Build before mounting so a factory error cannot leave a partial group."""
    member, origin, logger = context.member, context.origin, context.logger
    bindings = [
        ('/api/platform/finance', finance_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/linear/settings', linear_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/linear/tasks', tasks_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/news', news_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/news/ai', news_handler(member, origin, logger, ai=True), ('GET', 'POST')),
        ('/api/platform/chat/ai/settings', chat_ai_handler(member, origin, logger, settings=True), ('GET', 'POST')),
        ('/api/platform/chat/ai', chat_ai_handler(member, origin, logger), ('POST',)),
        ('/api/platform/news/telegram/settings', news_telegram_handler(member, origin, context.bot, logger, configuration=True), ('GET', 'POST')),
        ('/api/platform/news/telegram', news_telegram_handler(member, origin, context.bot, logger), ('GET', 'POST')),
        ('/api/platform/news/public/{company}', news_handler(member, origin, logger, public=True), ('GET',)),
        ('/api/platform/crm/chat/send', chat_send_handler(member, origin, context.crm_request, context.wait_slot, logger), ('POST',)),
        ('/api/platform/crm/chat/send-file', chat_upload_handler(member, origin, context.crm_request, context.wait_slot, logger), ('POST',)),
        ('/api/platform/crm/chat/history', chat_history_handler(member, logger), ('GET',)),
        ('/api/platform/crm/chat/attachment', chat_attachment_handler(member, context.crm_request, logger), ('GET',)),
        ('/api/platform/crm/chat/receipts', chat_review_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/diagnostics', diagnostics_handler(member, logger), ('GET',)),
        ('/api/platform/crm/deals/complete', deal_completion_handler(member, origin, deal_provider(context.crm_request, context.wait_slot), logger), ('POST',)),
        ('/api/platform/crm/deal-approvals', deal_completion_handler(member, origin, deal_provider(context.crm_request, context.wait_slot), logger, review=True), ('GET', 'POST')),
    ]
    routes = [(method, path, handle) for path, handle, methods in bindings for method in methods]
    if os.environ.get('AI_AGENTS_ENABLED', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        # ai_agent_routes already returns the flattened (method, path, handler)
        # shape expected by register_feature_routes. Keep it out of `bindings`,
        # whose entries use the grouped (path, handler, methods) shape above.
        routes.extend(ai_agent_routes(context))
    return routes


def ai_agent_routes(context: TenantWebContext, prefix: str = '/api/platform/ai/') -> list[tuple[str, str, Handler]]:
    """Build the same tenant-scoped AI Agents API under a trusted UI prefix.

    The legacy /webapp profile uses a signed employee session and resolves that
    identity to an existing SaaS tenant before these handlers run. The stores
    still enforce tenant membership and AI Agents management permissions on
    every operation.
    """
    member, origin, logger = context.member, context.origin, context.logger
    routes = [
        ('agents/settings', ai_agents_handler(member, origin, logger, 'settings'), ('GET', 'PATCH')),
        ('agents/test-mode', ai_agents_handler(member, origin, logger, 'test_mode'), ('GET', 'PATCH')),
        ('agents/test-mode/verify', ai_agents_handler(member, origin, logger, 'test_mode_verify', context.crm_request), ('POST',)),
        ('agents/test-mode/discover', ai_agents_handler(member, origin, logger, 'test_mode_discover', context.crm_request), ('POST',)),
        ('agents', ai_agents_handler(member, origin, logger, 'agents'), ('GET', 'POST')),
        ('agents/options', ai_agents_handler(member, origin, logger, 'options', context.crm_request), ('GET',)),
        ('agents/drafts', ai_agents_handler(member, origin, logger, 'drafts'), ('GET',)),
        ('agents/drafts/{draft_id}/review', ai_agents_handler(member, origin, logger, 'draft_review'), ('POST',)),
        ('agents/drafts/{draft_id}/send', ai_agents_handler(member, origin, logger, 'draft_send', context.crm_request, context.wait_slot), ('POST',)),
        ('handoffs', ai_agents_handler(member, origin, logger, 'handoffs'), ('GET',)),
        ('agents/{agent_id}', ai_agents_handler(member, origin, logger, 'agent'), ('PATCH', 'DELETE')),
        ('agents/{agent_id}/triggers', ai_agents_handler(member, origin, logger, 'triggers'), ('GET', 'POST')),
        ('agents/{agent_id}/triggers/{trigger_id}', ai_agents_handler(member, origin, logger, 'trigger'), ('PATCH', 'DELETE')),
        ('conversations/{conversation_id}/state', ai_agents_handler(member, origin, logger, 'conversation_state'), ('GET',)),
        ('conversations/for-lead/{lead_id}', ai_agents_handler(member, origin, logger, 'conversation_for_lead'), ('GET',)),
        ('conversations/{conversation_id}/handoff', ai_agents_handler(member, origin, logger, 'handoff'), ('POST',)),
        ('conversations/{conversation_id}/resume', ai_agents_handler(member, origin, logger, 'resume'), ('POST',)),
        ('knowledge', ai_agents_handler(member, origin, logger, 'knowledge'), ('GET', 'POST')),
        ('knowledge/upload', ai_agents_handler(member, origin, logger, 'knowledge_upload'), ('POST',)),
        ('knowledge/{source_id}', ai_agents_handler(member, origin, logger, 'knowledge_item'), ('PATCH', 'DELETE')),
    ]
    return [(method, prefix + path, handler) for path, handler, methods in routes for method in methods]


def ai_agent_profile_handler(member_from_request, logger):
    """Return only the identity fields the shared AI Agents UI needs."""
    async def handle(request: web.Request) -> web.Response:
        profile = member_from_request(request)
        if not profile:
            return web.json_response({'success': False, 'error': 'AI Agents üçün aktiv şirkət üzvlüyü tapılmadı.'}, status=403,
                                     headers={'Cache-Control': 'no-store'})
        try:
            # list_agents checks owner/admin AI permissions through the same
            # store policy used by every other AI Agents endpoint.
            await asyncio.to_thread(list_agents, profile)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status,
                                     headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.error('AI Agents profile lookup failed')
            return web.json_response({'success': False, 'error': 'AI Agents profili yüklənmədi.'}, status=503,
                                     headers={'Cache-Control': 'no-store'})
        safe_profile = {key: profile.get(key) for key in ('tenant_id', 'telegram_id', 'role', 'tenant_name')}
        return web.json_response({'success': True, 'member': safe_profile}, headers={'Cache-Control': 'no-store'})
    return handle


def register_feature_routes(app: web.Application, context: TenantWebContext) -> None:
    for method, path, handle in feature_routes(context):
        # add_get keeps aiohttp's existing automatic HEAD registration.
        if method == 'GET':
            app.router.add_get(path, handle)
        elif method == 'POST':
            app.router.add_post(path, handle)
        elif method == 'PATCH':
            app.router.add_patch(path, handle)
        elif method == 'DELETE':
            app.router.add_delete(path, handle)
        else:
            raise ValueError('Unsupported tenant route method')
