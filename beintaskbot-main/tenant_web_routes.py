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
from tenant_chat_ai_api import model_management_handler as chat_ai_model_handler
from tenant_chat_send_api import handler as chat_send_handler, receipt_handler as chat_receipt_handler
from tenant_chat_upload_api import handler as chat_upload_handler
from tenant_chat_history_api import handler as chat_history_handler
from tenant_chat_attachment_api import handler as chat_attachment_handler
from tenant_chat_review_api import handler as chat_review_handler
from tenant_diagnostics_api import handler as diagnostics_handler
from tenant_owner_asr_comparison_api import handler as owner_asr_comparison_handler
from tenant_deal_completion_api import handler as deal_completion_handler
from tenant_deal_completion_provider import provider as deal_provider
from tenant_ai_agents_api import handler as ai_agents_handler
from tenant_knowledge_candidates_api import handler as knowledge_candidates_handler
from tenant_support_inbox_api import handler as support_handler
from tenant_ai_testing_api import handler as testing_handler
from tenant_ai_historical_replay_api import handler as historical_replay_handler
from tenant_ai_simulation_api import handler as simulation_handler
from tenant_ai_state_api import handler as ai_state_handler
from tenant_operator_api import handler as operator_handler
from tenant_dialog_consultations_api import handler as dialog_consultations_handler
from tenant_instruction_library_api import handler as instruction_handler
from tenant_night_report_api import handler as night_report_handler
from tenant_ai_instructor_api import handler as ai_instructor_handler
from tenant_ai_administrator_api import handler as administrator_handler
from tenant_ai_learning_mcp import routes as learning_mcp_routes

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
        ('/api/platform/instructions', instruction_handler(member,origin,logger), ('GET','POST')),
        ('/api/platform/instructions/{lesson_id}', instruction_handler(member,origin,logger), ('GET','POST')),
        ('/api/platform/instructions/{lesson_id}/assets/{asset_id}', instruction_handler(member,origin,logger), ('GET',)),
        ('/api/instructions/shared/{token}', instruction_handler(member,origin,logger,'public'), ('GET',)),
        ('/api/instructions/shared/{token}/assets/{asset_id}', instruction_handler(member,origin,logger,'public'), ('GET',)),

        ('/api/platform/finance', finance_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/linear/settings', linear_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/linear/tasks', tasks_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/news', news_handler(member, origin, logger), ('GET', 'POST')),
        ('/api/platform/news/ai', news_handler(member, origin, logger, ai=True), ('GET', 'POST')),
        ('/api/platform/chat/ai/settings', chat_ai_handler(member, origin, logger, settings=True), ('GET', 'POST')),
        ('/api/platform/chat/ai/models', chat_ai_model_handler(member, origin, logger), ('GET',)),
        ('/api/platform/chat/ai/model-probe', chat_ai_model_handler(member, origin, logger, probe=True), ('POST',)),
        ('/api/platform/chat/ai', chat_ai_handler(member, origin, logger), ('POST',)),
        ('/api/platform/news/telegram/settings', news_telegram_handler(member, origin, context.bot, logger, configuration=True), ('GET', 'POST')),
        ('/api/platform/news/telegram', news_telegram_handler(member, origin, context.bot, logger), ('GET', 'POST')),
        ('/api/platform/news/public/{company}', news_handler(member, origin, logger, public=True), ('GET',)),
        ('/api/platform/crm/chat/quote-receipt', chat_receipt_handler(member,logger), ('GET',)),
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
    routes.extend(ai_agent_routes(context))
    routes.extend(learning_mcp_routes(context.member))
    return routes


def ai_agent_routes(context: TenantWebContext, prefix: str = '/api/platform/ai/') -> list[tuple[str, str, Handler]]:
    """AI Agents are a tenant product module, independent of message transports."""
    member, origin, logger = context.member, context.origin, context.logger
    routes = [
        ('testing/sessions', testing_handler(member, origin, logger, 'sessions'), ('GET','POST')),
        ('testing/historical-replay', historical_replay_handler(member,origin,logger), ('GET','POST')),
        ('testing/sessions/{session_id}', testing_handler(member, origin, logger, 'session'), ('GET',)),
        ('testing/sessions/{session_id}/turn', testing_handler(member, origin, logger, 'turn'), ('POST',)),
        ('testing/sessions/{session_id}/control', testing_handler(member, origin, logger, 'control'), ('POST',)),
        ('simulation/settings', simulation_handler(member, origin, logger, 'settings'), ('GET','PATCH')),
        ('simulation/runs', simulation_handler(member, origin, logger, 'runs'), ('GET',)),
        ('simulation/runs/{run_id}', simulation_handler(member, origin, logger, 'run'), ('GET',)),
        ('simulation/runs/{run_id}/recheck', simulation_handler(member, origin, logger, 'recheck'), ('POST',)),
        ('simulation/issues', simulation_handler(member, origin, logger, 'issues'), ('GET',)),
        ('simulation/issues/{run_id}/review', simulation_handler(member, origin, logger, 'issue_review'), ('POST',)),
        ('dashboard', simulation_handler(member, origin, logger, 'dashboard'), ('GET',)),
        ('dashboard/events', simulation_handler(member, origin, logger, 'events'), ('GET',)),
        ('agents/settings', ai_agents_handler(member, origin, logger, 'settings'), ('GET', 'PATCH')),
        ('agents/pilot-preflight', ai_agents_handler(member, origin, logger, 'pilot_preflight'), ('GET',)),
        ('agents/pilot/resume', ai_agents_handler(member, origin, logger, 'pilot_resume'), ('POST',)),
        ('agents/pilot/reset', ai_agents_handler(member, origin, logger, 'pilot_reset'), ('POST',)),
        ('agents/pilot/kwid-confirmation', ai_agents_handler(member, origin, logger, 'pilot_kwid_confirmation'), ('POST',)),
        ('agents/pilot/owner-approval', ai_agents_handler(member, origin, logger, 'pilot_owner_approval'), ('POST',)),
        ('administrator/approvals', administrator_handler(member, origin, logger), ('GET','POST')),
        ('agents', ai_agents_handler(member, origin, logger, 'agents'), ('GET', 'POST')),
        ('instructor', ai_instructor_handler(member, origin, logger), ('GET',)),
        ('instructor/connections', ai_instructor_handler(member, origin, logger, 'connections'), ('GET','POST')),
        ('instructor/jobs', ai_instructor_handler(member, origin, logger, 'jobs'), ('GET','POST')),
        ('instructor/jobs/{job_id:[0-9a-fA-F-]{36}}', ai_instructor_handler(member, origin, logger, 'job'), ('GET','POST')),
        ('instructor/jobs/{job_id:[0-9a-fA-F-]{36}}/artifacts/{artifact_id:[0-9a-fA-F-]{36}}', ai_instructor_handler(member, origin, logger, 'artifact'), ('GET',)),
        ('agents/options', ai_agents_handler(member, origin, logger, 'options', context.crm_request), ('GET',)),
        ('agents/{agent_id:[0-9a-fA-F-]{36}}/delete-preview', ai_agents_handler(member, origin, logger, 'agent_delete_preview'), ('GET',)),
        ('dialogs', operator_handler(member, origin, logger, 'dialogs'), ('GET',)),
        ('dialogs/avatar', operator_handler(member, origin, logger, 'avatar'), ('GET',)),
        ('dialogs/history', operator_handler(member, origin, logger, 'history'), ('GET',)),
        ('dialogs/read', operator_handler(member, origin, logger, 'dialogs_read'), ('POST',)),
        ('dialogs/read-all', operator_handler(member, origin, logger, 'dialogs_read_all'), ('POST',)),
        ('dialogs/asr-comparison', owner_asr_comparison_handler(member, origin, logger), ('GET', 'POST')),
        ('dialogs/copilot', operator_handler(member, origin, logger, 'copilot'), ('POST',)),
        ('dialogs/display-name', operator_handler(member, origin, logger, 'display_name'), ('POST',)),
        ('dialogs/agent-assignment', operator_handler(member, origin, logger, 'agent_assignment'), ('GET', 'POST')),
        ('dialogs/agent-assignment-bulk', operator_handler(member, origin, logger, 'agent_assignment_bulk'), ('GET', 'POST')),
        ('dialogs/legacy-continuation', operator_handler(member, origin, logger, 'legacy_continuation'), ('GET', 'POST')),
        ('dialogs/legacy-recovery', operator_handler(member, origin, logger, 'legacy_recovery'), ('GET', 'POST')),
        ('dialogs/group-coordination', operator_handler(member, origin, logger, 'group_coordination'), ('POST',)),
        ('dialogs/group-staff', operator_handler(member, origin, logger, 'group_staff'), ('POST',)),
        ('night-report', night_report_handler(member, logger), ('GET',)),
        ('night-report/dialog', night_report_handler(member, logger, 'dialog'), ('GET',)),
        ('night-report/tickets/{ticket_id}', night_report_handler(member, logger, 'ticket'), ('GET',)),
        ('support-tickets', support_handler(member, origin, logger, 'tickets'), ('GET', 'POST')),
        ('support-tickets/complete', support_handler(member, origin, logger, 'complete'), ('POST',)),
        ('support-tickets/{ticket_id}', support_handler(member, origin, logger, 'ticket'), ('GET', 'PATCH')),
        ('support-tickets/{ticket_id}/attachments', support_handler(member, origin, logger, 'ticket_attachments'), ('GET', 'POST')),
        ('support-tickets/{ticket_id}/messages', support_handler(member, origin, logger, 'ticket_messages'), ('GET', 'POST')),
        ('support-tickets/{ticket_id}/summary', support_handler(member, origin, logger, 'summary'), ('POST',)),
        ('dialog-consultations/developer-marker', dialog_consultations_handler(member, origin, logger, 'developer_marker'), ('POST',)),
        ('dialog-consultations/destinations', dialog_consultations_handler(member, origin, logger, 'destinations'), ('GET',)),
        ('dialog-consultations/preview', dialog_consultations_handler(member, origin, logger, 'preview'), ('POST',)),
        ('dialog-consultations', dialog_consultations_handler(member, origin, logger, 'prepare'), ('POST',)),
        ('dialog-consultations/replies', dialog_consultations_handler(member, origin, logger, 'replies'), ('GET',)),
        ('dialog-consultations/{id}/dispatch', dialog_consultations_handler(member, origin, logger, 'dispatch'), ('POST',)),
        ('dialog-consultations/{id}/revoke', dialog_consultations_handler(member, origin, logger, 'revoke'), ('POST',)),
        ('dialog-consultations/replies/{id}/customer-replies', dialog_consultations_handler(member, origin, logger, 'customer_replies'), ('GET',)),
        ('dialog-consultations/replies/{id}/action', dialog_consultations_handler(member, origin, logger, 'action'), ('POST',)),
        ('dialog-consultations/replies/{id}/draft', dialog_consultations_handler(member, origin, logger, 'draft'), ('POST',)),
        ('internal-consultations', support_handler(member, origin, logger, 'consultation'), ('GET','POST')),
        ('internal-consultations/groups', support_handler(member, origin, logger, 'consultation_groups'), ('GET',)),
        ('internal-consultations/{consultation_id}/group', support_handler(member, origin, logger, 'consultation_group'), ('GET','POST')),
        ('internal-consultations/{consultation_id}/reply', support_handler(member, origin, logger, 'consultation_reply'), ('POST',)),
        ('handoffs', ai_agents_handler(member, origin, logger, 'handoffs'), ('GET',)),
        ('channels', ai_state_handler(member, logger, 'channels'), ('GET',)),
        ('state', ai_state_handler(member, logger), ('GET',)),
        ('state/technical', ai_state_handler(member, logger, 'technical'), ('GET',)),
        ('reliability', ai_agents_handler(member, origin, logger, 'reliability'), ('GET',)),
        ('diagnostics', ai_agents_handler(member, origin, logger, 'diagnostics'), ('GET',)),
        ('agents/{agent_id:[0-9a-fA-F-]{36}}', ai_agents_handler(member, origin, logger, 'agent'), ('PATCH', 'DELETE')),
        ('agents/{agent_id:[0-9a-fA-F-]{36}}/triggers', ai_agents_handler(member, origin, logger, 'triggers'), ('GET', 'POST')),
        ('agents/{agent_id:[0-9a-fA-F-]{36}}/triggers/{trigger_id:[0-9a-fA-F-]{36}}', ai_agents_handler(member, origin, logger, 'trigger'), ('PATCH', 'DELETE')),
        ('conversations/{conversation_id}/state', ai_agents_handler(member, origin, logger, 'conversation_state'), ('GET',)),
        ('conversations/for-lead/{lead_id}', ai_agents_handler(member, origin, logger, 'conversation_for_lead'), ('GET',)),
        ('conversations/{conversation_id}/handoff', ai_agents_handler(member, origin, logger, 'handoff'), ('POST',)),
        ('conversations/{conversation_id}/pause', ai_agents_handler(member, origin, logger, 'pause'), ('POST',)),
        ('conversations/{conversation_id}/resume', ai_agents_handler(member, origin, logger, 'resume'), ('POST',)),
        ('knowledge-candidates', knowledge_candidates_handler(member, origin, logger), ('GET','POST')),
        ('knowledge-candidates/operator-preview', knowledge_candidates_handler(member, origin, logger, 'operator_preview'), ('POST',)),
        ('knowledge-candidates/operator-submit', knowledge_candidates_handler(member, origin, logger, 'operator_submit'), ('POST',)),
        ('knowledge-candidates/campaigns', knowledge_candidates_handler(member, origin, logger, 'campaigns'), ('GET','POST')),
        ('knowledge-candidates/campaigns/preview', knowledge_candidates_handler(member, origin, logger, 'campaign_preview'), ('GET',)),
        ('knowledge-candidates/campaigns/{campaign_id}', knowledge_candidates_handler(member, origin, logger, 'campaign'), ('GET',)),
        ('knowledge-candidates/campaigns/{campaign_id}/start', knowledge_candidates_handler(member, origin, logger, 'campaign_start'), ('POST',)),
        ('knowledge-candidates/campaigns/{campaign_id}/control', knowledge_candidates_handler(member, origin, logger, 'campaign_control'), ('POST',)),
        ('knowledge-candidates/campaigns/{campaign_id}/case-continuation', knowledge_candidates_handler(member, origin, logger, 'campaign_case_continuation'), ('GET','POST')),
        ('knowledge-candidates/campaigns/{campaign_id}/current-media-check', knowledge_candidates_handler(member, origin, logger, 'campaign_current_media_check'), ('POST',)),
        ('knowledge-candidates/batches', knowledge_candidates_handler(member, origin, logger, 'batches'), ('POST',)),
        ('knowledge-candidates/batches/{batch_id}', knowledge_candidates_handler(member, origin, logger, 'batch'), ('GET',)),
        ('knowledge-candidates/{candidate_id}/improve', knowledge_candidates_handler(member, origin, logger, 'improve'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/boundaries', knowledge_candidates_handler(member, origin, logger, 'boundaries'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/source-review', knowledge_candidates_handler(member, origin, logger, 'source_review_preflight'), ('GET',)),
        ('knowledge-candidates/{candidate_id}/source-review', knowledge_candidates_handler(member, origin, logger, 'source_review'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/clarify', knowledge_candidates_handler(member, origin, logger, 'clarify'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/conversation', knowledge_candidates_handler(member, origin, logger, 'conversation'), ('GET',)),
        ('knowledge-candidates/requests/{request_id}', knowledge_candidates_handler(member, origin, logger, 'request'), ('GET',)),
        ('knowledge-candidates/{candidate_id}', knowledge_candidates_handler(member, origin, logger, 'candidate'), ('GET','PATCH')),
        ('knowledge-candidates/{candidate_id}/approve', knowledge_candidates_handler(member, origin, logger, 'approve'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/publish-guidance', knowledge_candidates_handler(member, origin, logger, 'publish_guidance'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/correct-published', knowledge_candidates_handler(member, origin, logger, 'correct_published'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/reject', knowledge_candidates_handler(member, origin, logger, 'reject'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/delete', knowledge_candidates_handler(member, origin, logger, 'delete'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/archive', knowledge_candidates_handler(member, origin, logger, 'archive'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/restore', knowledge_candidates_handler(member, origin, logger, 'restore'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/mark-knowledge-published', knowledge_candidates_handler(member, origin, logger, 'mark_knowledge_published'), ('POST',)),
        ('knowledge-candidates/{candidate_id}/reconcile-approved-publication', knowledge_candidates_handler(member, origin, logger, 'reconcile_approved_publication'), ('POST',)),
        ('knowledge', ai_agents_handler(member, origin, logger, 'knowledge'), ('GET', 'POST')),
        ('knowledge/deduplication', ai_agents_handler(member, origin, logger, 'knowledge_deduplication'), ('GET', 'POST')),
        ('knowledge/upload', ai_agents_handler(member, origin, logger, 'knowledge_upload'), ('POST',)),
        ('knowledge/{source_id}', ai_agents_handler(member, origin, logger, 'knowledge_item'), ('PATCH', 'DELETE')),
        ('knowledge/{source_id}/restore', ai_agents_handler(member, origin, logger, 'knowledge_restore'), ('POST',)),
    ]
    return [(method, prefix + path, handler) for path, handler, methods in routes for method in methods]


def register_feature_routes(app: web.Application, context: TenantWebContext) -> None:
    for method, path, handle in feature_routes(context):
        # AI agent CRUD also needs PATCH and DELETE; register explicitly so
        # every module route is mounted instead of silently becoming POST-only.
        if method == 'GET':
            app.router.add_get(path, handle)
        else:
            app.router.add_route(method, path, handle)
