"""Compose modular SaaS APIs once, with explicit runtime dependencies.

No bot import, tenant lookup, database access or network call during mounting.
Authentication and resource policy remain in the existing API/store modules.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from logging import Logger
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
    return [(method, path, handle) for path, handle, methods in bindings for method in methods]


def register_feature_routes(app: web.Application, context: TenantWebContext) -> None:
    for method, path, handle in feature_routes(context):
        # add_get keeps aiohttp's existing automatic HEAD registration.
        if method == 'GET':
            app.router.add_get(path, handle)
        else:
            app.router.add_post(path, handle)
