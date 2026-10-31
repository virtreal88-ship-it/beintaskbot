"""Private employee observations into existing inactive Knowledge Sources.

No inference from client/attachment/model output. No publication, overwrite,
media analysis or provider calls. Existing AI management rights remain required.
"""
import json
import re
from tenant_linear_policy import TenantLinearError

_CONFIRMED = re.compile(
    r'(?i)(?:\b(?:проверили|выяснили|подтвердили|установили)\b|'
    r'\b(?:we verified|we confirmed|confirmed diagnosis|verified cause)\b|'
    r'\b(?:yoxladıq|təsdiqlədik|müəyyən etdik)\b|'
    r'\b(?:исправление|поправка):|\bcorrection:|\bdüzəliş:)')
_UNCERTAIN = re.compile(r'(?i)(?:[?？"«»“”`]|(?:клиент|customer|müştəri)\s+(?:сказал|написал|пишет|says|said|yazır)|\b(?:может|возможно|предполагаю|кажется|если|гипотеза|maybe|perhaps|hypothesis|if|bəlkə|ehtimal|əgər)\b)')


def confirmed_observation(command):
    # This function deliberately cannot accept conversation/AI/media content.
    if command.get('mode') not in {'chat', 'instruction'}:
        return None
    value = str(command.get('prompt') or '').strip()
    if not _CONFIRMED.search(value) or _UNCERTAIN.search(value):
        return None
    from tenant_knowledge_candidates_policy import redact
    return redact(value)[:1600]


def record(cur, conn, profile, command):
    value = confirmed_observation(command)
    if not value:
        return {'status': 'not_applicable', 'message': ''}
    from tenant_ai_agents_store import _authorize, save_knowledge
    # Do not catch a permission denial and attempt another writing path.
    tenant, actor = _authorize(cur, profile)
    agent = command['_agent_id']
    key = 'copilot_' + command['request_id']
    provenance = {'origin': 'private_employee_copilot', 'employee_id': actor,
        'employee_turn_id': command['request_id'], 'agent_id': agent,
        'channel_id': command['channel_id'], 'conversation_id': command['conversation_id'],
        'lead_id': command['lead_id'], 'scope': 'single_case_employee_verified',
        'review_status': 'pending_review', 'revision': 1}
    content = ('Employee-confirmed observation for this case only. '
               'Not a universal instruction; reconcile contradictions before publication.\n'
               + value)
    source = save_knowledge(profile, {'agent_id': agent,
        'setup_key': key, 'title': 'Copilot case observation ' + command['request_id'][:8],
        'kind': 'text', 'content': content, 'active': False}, connection=conn)
    cur.execute('''UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND agent_id=%s::uuid AND active=FALSE''',
      (json.dumps(provenance), tenant, source['id'], agent))
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
            'message': ('Вывод не сохранён: существующие права AI не разрешают сотруднику создавать материал для разбора.'
                        if error.status == 403 else 'Не удалось сохранить вывод на разбор.')}
    except Exception:
        cur.execute('ROLLBACK TO SAVEPOINT copilot_learning')
        result = {'status': 'failed', 'message': 'Не удалось сохранить вывод на разбор.'}
    cur.execute('RELEASE SAVEPOINT copilot_learning')
    return result
