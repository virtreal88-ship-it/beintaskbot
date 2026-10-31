"""Private employee observations into existing inactive Knowledge Sources.

No inference from client/attachment/model output. No publication, overwrite,
media analysis or provider calls. Only current private Copilot turns may submit inactive review material.
"""
import json
import re
from tenant_linear_policy import TenantLinearError

# Deliberately prefer false negatives to turning a question, denial, quotation
# or attributed client statement into an employee-verified diagnosis.
_CONFIRMED = re.compile(
    r'(?i)(?:^|[.!;\n]\s*)(?:'
    r'(?:(?:мы\s+)?|после\s+(?:подключения|проверки|диагностики)\s+(?:мы\s+)?)'
    r'(?:проверили|выяснили|подтвердили|установили)\b|'
    r'we\s+(?:have\s+)?(?:verified|confirmed|checked|established|found)\b|'
    r'(?:confirmed diagnosis|verified cause):|'
    r'(?:biz\s+)?(?:yoxladıq|təsdiqlədik|müəyyən etdik)\b|'
    r'(?:исправление|поправка|correction|düzəliş):\s*\S)')
_UNCERTAIN = re.compile(
    r'(?i)(?:[?？"«»“”`]|'
    r'\b(?:не|нет|никогда|неизвестно|без подтверждения|со слов|по словам|якобы|'
    r'может|возможно|вероятно|скорее всего|вроде|кажется|если|когда|гипотеза|предполага\w*|предполож\w*|'
    r'not|no|never|unconfirmed|unverified|unknown|maybe|perhaps|hypothesis|suspected|possible|probably|likely|guess|assume|tentative|if|reportedly|'
    r'according to|(?:customer|client)\s+(?:says?|said|reports?|reported|told|claims?|confirmed|verified)|'
    r'bəlkə|ehtimal\w*|ola bilər|güman\w*|şübhə\w*|görünür|əgər|deyil|yoxlamadıq|təsdiqləməmişik|bilmirik|guya|müştəri\w*\s+(?:sözlərinə|dedi|deyir|bildirdi|yazdı|yazır|təsdiqlədi))\b|'
    r'\bклиент\w*\s+(?:сказал|написал|пишет|утверждает|сообщил|сообщает|подтвердил)|'
    r'\b(?:напиши|скажи|сообщи|представь|допустим|tell|say|write)\b)')


def confirmed_observation(command):
    # This function deliberately cannot accept conversation/AI/media content.
    if command.get('mode') not in {'chat', 'instruction'}:
        return None
    value = str(command.get('prompt') or '').strip()
    if not _CONFIRMED.search(value) or _UNCERTAIN.search(value):
        return None
    from tenant_knowledge_candidates_policy import redact
    return redact(value)[:1600]


# This in-process token is never accepted from a JSON request. The generic
# knowledge API continues to require its existing management authorization.
_SUBMISSION_CAPABILITY = object()


def _payload(command):
    value = confirmed_observation(command)
    if not value:
        raise TenantLinearError('Нет подтверждённого приватного вывода.', 403)
    return {'agent_id': command['_agent_id'],
        'setup_key': 'copilot_' + command['request_id'],
        'title': 'Copilot case observation ' + command['request_id'][:8],
        'kind': 'text', 'content': ('Employee-reported confirmed observation for this case only. Not independently verified. '
            'Not a universal instruction; reconcile contradictions before publication.\n' + value),
        'active': False}


def authorize_creation(cur, profile, data):
    """Create-only review submission, bound to an authenticated private receipt."""
    if profile.get('_copilot_teaching_capability') is not _SUBMISSION_CAPABILITY:
        raise TenantLinearError('Нет доступа к подаче приватного вывода.', 403)
    command = profile.get('_copilot_teaching_command')
    if not isinstance(command, dict) or data != _payload(command):
        raise TenantLinearError('Некорректный материал приватного вывода.', 403)
    from tenant_operator_access import access, assert_route
    from tenant_operator_copilot import _assigned_context, _agent_binding, _hash, _can_use_copilot
    tenant, actor, _ = access(cur, profile, 'support')
    checked, checked_actor, policy = access(cur, profile, 'dialogs')
    if checked != tenant or checked_actor != actor or not _can_use_copilot(policy):
        raise TenantLinearError('Нет доступа к приватному Copilot.', 403)
    assert_route(cur, tenant, policy, 'connect', command['conversation_id'],
                 command['channel_id'], command['lead_id'])
    context = _assigned_context(cur, tenant, command)
    if (str(context['agent_id']) != command['_agent_id']
            or _agent_binding(context) != command.get('_agent_binding')):
        raise TenantLinearError('Назначение агента изменилось.', 409)
    cur.execute('''SELECT requested_by,input_hash,status,provider,lead_id,channel_id,conversation_id
      FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE''',
      (tenant, command['request_id']))
    receipt = cur.fetchone() or {}
    if (receipt.get('requested_by') != actor or receipt.get('input_hash') != _hash(command)
            or receipt.get('status') not in {'sending', 'completed'}
            or receipt.get('provider') != 'connect' or receipt.get('lead_id') != command['lead_id']
            or receipt.get('channel_id') != command['channel_id']
            or receipt.get('conversation_id') != command['conversation_id']):
        raise TenantLinearError('Приватный запрос сотрудника не подтверждён.', 403)
    return tenant, actor


def record(cur, conn, profile, command):
    if not confirmed_observation(command):
        return {'status': 'not_applicable', 'message': ''}
    from tenant_ai_agents_store import save_knowledge
    # No management permission is granted. Only the exact immutable payload
    # below can be created inactive through this server-only capability.
    scoped = {**profile, '_copilot_teaching_capability': _SUBMISSION_CAPABILITY,
              '_copilot_teaching_command': dict(command)}
    source = save_knowledge(scoped, _payload(command), connection=conn)
    tenant, actor = profile['tenant_id'], int(profile['telegram_id'])
    provenance = {'origin': 'private_employee_copilot', 'employee_id': actor,
        'employee_turn_id': command['request_id'], 'agent_id': command['_agent_id'],
        'channel_id': command['channel_id'], 'conversation_id': command['conversation_id'],
        'lead_id': command['lead_id'], 'scope': 'single_case_employee_verified',
        'review_status': 'pending_review', 'revision': 1,
        'confirmation_basis': 'affirmative_private_employee_statement',
        'verification_scope': 'employee_report_not_independently_verified'}
    cur.execute('''UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND agent_id=%s::uuid AND active=FALSE''',
      (json.dumps(provenance), tenant, source['id'], command['_agent_id']))
    return {'status': 'pending_review', 'source_id': str(source['id']),
            'message': 'Вывод сохранён на разбор в базе знаний назначенного агента; ещё не опубликован.'}


def attempt(cur, conn, profile, command):
    if not confirmed_observation(command):
        return {'status': 'not_applicable', 'message': ''}
    cur.execute('SAVEPOINT copilot_learning')
    try:
        result = record(cur, conn, profile, command)
    except TenantLinearError as error:
        cur.execute('ROLLBACK TO SAVEPOINT copilot_learning')
        result = {'status': 'denied' if error.status == 403 else 'failed',
            'message': ('Вывод не сохранён: нет доступа к приватному Copilot или его текущему диалогу.'
                        if error.status == 403 else 'Не удалось сохранить вывод на разбор.')}
    except Exception:
        cur.execute('ROLLBACK TO SAVEPOINT copilot_learning')
        result = {'status': 'failed', 'message': 'Не удалось сохранить вывод на разбор.'}
    cur.execute('RELEASE SAVEPOINT copilot_learning')
    return result
