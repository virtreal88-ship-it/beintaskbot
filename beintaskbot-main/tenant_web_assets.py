"""Fixed SaaS asset catalogue. No request path becomes a filesystem path."""
from collections.abc import Awaitable, Callable
from pathlib import Path
from aiohttp import web

ASSETS = (
    ('/app/instructions', 'tenant-instructions.html', 'no-store'),
    ('/app/ai-instructor', 'tenant-ai-instructor.html', 'no-store'),
    ('/assets/tenant-ai-instructor.js', 'tenant-ai-instructor.js', 'no-store'),
    ('/assets/tenant-ai-instructor.css', 'tenant-ai-instructor.css', 'no-store'),
    ('/assets/tenant-instructions.js', 'tenant-instructions.js', 'no-store'),
    ('/assets/tenant-instructions.css', 'tenant-instructions.css', 'no-store'),
    ('/assets/tenant-instructor-lesson.js', 'tenant-instructor-lesson.js', 'no-store'),
    ('/assets/tenant-instructor-lesson.css', 'tenant-instructor-lesson.css', 'no-store'),
    ('/assets/tenant-agent-catalog.js', 'tenant-agent-catalog.js', 'no-store'),
    ('/assets/tenant-agent-library.js', 'tenant-agent-library.js', 'no-store'),
    ('/assets/tenant-agent-library.css', 'tenant-agent-library.css', 'no-store'),
    ('/assets/tenant-ai-state.js', 'tenant-ai-state.js', 'no-store'),
    ('/assets/tenant-ai-state.css', 'tenant-ai-state.css', 'no-store'),

    ('/assets/tenant-ai-testing.js', 'tenant-ai-testing.js', 'no-store'),
    ('/assets/tenant-ai-testing.css', 'tenant-ai-testing.css', 'no-store'),
    ('/assets/tenant-ai-administrator-approvals.js', 'tenant-ai-administrator-approvals.js', 'no-store'),
    ('/app/night-report', 'tenant-night-report.html', 'no-store'),
    ('/assets/tenant-night-report.js', 'tenant-night-report.js', 'no-store'),
    ('/assets/tenant-night-report.css', 'tenant-night-report.css', 'no-store'),
    ('/app/push-sw.js', 'tenant-push-sw.js', 'no-store'),
    ('/assets/tenant-push.js', 'tenant-push.js', 'no-store'),
    ('/assets/task-date-fields.js', 'task-date-fields.js', 'no-store'),
    ('/assets/tenant-finance.js', 'tenant-finance.js', 'no-cache'),
    ('/assets/tenant-chat-import.js', 'tenant-chat-import.js', 'no-cache'),
    ('/assets/tenant-chat-history.js', 'tenant-chat-history.js', 'no-cache'),
    ('/assets/tenant-chat-attachments.js', 'tenant-chat-attachments.js', 'no-cache'),
    ('/assets/tenant-chat-review.js', 'tenant-chat-review.js', 'no-cache'),
    ('/assets/tenant-chat-ai.js', 'tenant-chat-ai.js', 'no-cache'),
    ('/assets/tenant-chat-ai-settings.js', 'tenant-chat-ai-settings.js', 'no-cache'),
    ('/assets/tenant-news-telegram-settings.js', 'tenant-news-telegram-settings.js', 'no-cache'),
    ('/updates/{company}', 'tenant-news-public.html', 'no-cache'),
    ('/assets/tenant-news.js', 'tenant-news.js', 'no-cache'),
    ('/assets/tenant-news-ai-settings.js', 'tenant-news-ai-settings.js', 'no-cache'),
    ('/assets/tenant-linear-settings.js', 'tenant-linear-settings.js', 'no-cache'),
    ('/assets/tenant-linear-board.js', 'tenant-linear-board.js', 'no-cache'),
    ('/assets/tenant-linear-rules.js', 'tenant-linear-rules.js', 'no-cache'),
    ('/assets/tenant-task-approvals.js', 'tenant-task-approvals.js', 'no-store'),
    ('/assets/tenant-task-completion.js', 'tenant-task-completion.js', 'no-store'),
    ('/assets/tenant-deal-completion.js', 'tenant-deal-completion.js', 'no-store'),
    ('/assets/tenant-diagnostics.js', 'tenant-diagnostics.js', 'no-store'),
    ('/assets/tenant-chat-send.js', 'tenant-chat-send.js', 'no-store'),
    ('/assets/tenant-operator-i18n.js', 'tenant-operator-i18n.js', 'no-store'),
    ('/assets/tenant-request-coordinator.js', 'tenant-request-coordinator.js', 'no-store'),
    ('/assets/tenant-view-demand.js', 'tenant-view-demand.js', 'no-store'),
    ('/assets/tenant-operator-dialogs.js', 'tenant-operator-dialogs.js', 'no-store'),
    ('/assets/tenant-dialog-consultations.js', 'tenant-dialog-consultations.js', 'no-store'),
    ('/assets/tenant-knowledge-campaigns.js', 'tenant-knowledge-campaigns.js', 'no-store'),
    ('/assets/tenant-knowledge-candidates.js', 'tenant-knowledge-candidates.js', 'no-store'),
    ('/assets/tenant-knowledge-pilot-admin.js', 'tenant-knowledge-pilot-admin.js', 'no-store'),
    ('/assets/tenant-support-inbox.js', 'tenant-support-inbox.js', 'no-store'),
    ('/assets/tenant-ai-reliability.js', 'tenant-ai-reliability.js', 'no-store'),
    ('/assets/tenant-night-integration.js', 'tenant-night-integration.js', 'no-store'),
    ('/assets/tenant-night-l10n.js', 'tenant-night-l10n.js', 'no-store'),
    ('/assets/tenant-ai-expert-editor.js', 'tenant-ai-expert-editor.js', 'no-store'),
    ('/assets/tenant-ai-agents.js', 'tenant-ai-agents.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-ai-teams.js', 'tenant-ai-teams.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-ai-teams.css', 'tenant-ai-teams.css', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-knowledge-center.js', 'tenant-knowledge-center.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-knowledge-center.css', 'tenant-knowledge-center.css', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-night-agent-template.js', 'tenant-night-agent-template.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-workflow.js', 'tenant-workflow.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-chakra-ui.js', 'tenant-chakra-ui.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-workspace.js', 'tenant-workspace.js', None),
    ('/assets/tenant-members-ui.js', 'tenant-members-ui.js', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-members-ui.css', 'tenant-members-ui.css', 'no-cache, max-age=0, must-revalidate'),
    ('/assets/tenant-hot-order-settings.js', 'tenant-hot-order-settings.js', 'no-store'),
    ('/assets/tenant-hot-orders.js', 'tenant-hot-orders.js', 'no-store'),
    ('/assets/tenant-hot-order-completion.js', 'tenant-hot-order-completion.js', 'no-cache'),
    ('/assets/tenant-crm-sync.js', 'tenant-crm-sync.js', 'no-store'),
    ('/assets/tenant-tasks.js', 'tenant-tasks.js', 'no-cache, max-age=0, must-revalidate'),
)


def asset_handler(path: Path, cache: str | None, *, worker: bool = False) -> Callable[[web.Request], Awaitable[web.FileResponse]]:
    async def handle(_request: web.Request) -> web.FileResponse:
        headers = {} if cache is None else {'Cache-Control': cache}
        if worker:
            headers['Service-Worker-Allowed'] = '/app/'
        return web.FileResponse(path, headers=headers)
    return handle


def register_assets(app: web.Application) -> None:
    directory = Path(__file__).resolve().parent / 'docs'
    for url, filename, cache in ASSETS:
        app.router.add_get(url, asset_handler(directory / filename, cache, worker=url == '/app/push-sw.js'))
