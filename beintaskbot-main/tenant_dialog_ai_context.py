"""Current assigned identity for AI work belonging to a conversation.

Never selects a default agent. Credentials and monetary limits remain owned by
existing admissions; specialized media processors are not independent agents.
"""
import hashlib
import json
from tenant_linear_policy import TenantLinearError


def resolve(cur, tenant, command):
    # Shared direct/group authority lookup also fences assignment revisions.
    from tenant_operator_case_submission import assignment
    selected = assignment(cur, tenant, command['channel_id'], command['conversation_id'])
    cur.execute('''SELECT a.id AS agent_id,a.version,a.model,a.instructions,a.language,a.style,s.company_instructions
      FROM saas_ai_agents a LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
      WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid AND a.archived_at IS NULL FOR SHARE OF a''',
      (tenant, selected['agent_id']))
    context = cur.fetchone() or {}
    if not context.get('agent_id'):
        raise TenantLinearError('Назначьте агента этому диалогу перед использованием AI.', 409)
    return {**context, 'assignment_revision': selected['assignment_revision']}


def binding(context):
    # Instructions/company instructions are included so a settings change also
    # invalidates cached results and in-flight responses.
    return hashlib.sha256(json.dumps({k: str(context.get(k) or '') for k in
        ('agent_id', 'version', 'assignment_revision', 'model', 'instructions', 'language', 'style', 'company_instructions')},
        sort_keys=True).encode()).hexdigest()

def require_assignment(cur, tenant, channel, conversation, expected_agent=None):
    from tenant_operator_case_submission import assignment
    current = assignment(cur, tenant, channel, conversation)
    if expected_agent and current['agent_id'] != str(expected_agent):
        raise TenantLinearError('Назначение агента изменилось. Обновите диалог.', 409)
    return current


def assert_reservation_current(job, reservation):
    """Revalidate the frozen agent snapshot without changing source semantics.

    Historical CASE followups may deliberately use an older source fragment;
    they still cannot generate with an outdated assigned model/instruction set.
    """
    from tenant_ai_agent_run_store import load_agent_config
    current = load_agent_config(job)
    if not current or str(current.get('agent_model') or '') != str(reservation.get('model') or ''):
        raise TenantLinearError('Назначенная модель агента изменилась.',409)
    if reservation.get('agent_version') is not None and str(current.get('agent_version')) != str(reservation['agent_version']):
        raise TenantLinearError('Настройки назначенного агента изменились.',409)
    return current
