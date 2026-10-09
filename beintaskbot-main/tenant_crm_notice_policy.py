"""Current resource authority and observed CRM transitions, not Kommo seats."""
import hashlib
import json
import math
from datetime import datetime
from tenant_policy import TenantPolicy, positive_id
from tenant_notifications import notification_channels

EVENTS = {'new_lead', 'incoming_message', 'task_assigned', 'task_overdue'}


def timestamp(value: object) -> float:
    try:
        if isinstance(value, datetime):
            return value.timestamp() if value.tzinfo else 0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) if math.isfinite(value) else 0
        if isinstance(value, str) and value.isdecimal():
            return float(value)
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def version(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def task_route(row: dict) -> str:
    # Stage changes and shared responsible_user_id do not reassign a task.
    return version([row.get('kommo_lead_id'), (row.get('deal') or {}).get('pipeline_id'),
                    sorted(part for part in str(row.get('text') or '').split() if part.startswith('[CRM:'))])


def task_owner(profile: dict, task: dict) -> bool:
    policy = TenantPolicy(profile)
    if not policy.allows('tasks') or task.get('completed') is not False:
        return False
    scope = policy.task_scope()
    deal = task.get('deal') or {}
    if task.get('kommo_lead_id') and (not deal or deal.get('deleted_at')):
        return False
    if scope['kind'] == 'marker':
        return policy.task_marker() in str(task.get('text') or '')
    if policy.privileged and not task.get('kommo_lead_id'):
        return policy.task_marker() in str(task.get('text') or '')
    # Administrators receive only their personal pipeline tasks, not everybody's.
    personal = TenantPolicy({**profile, 'role': 'manager'}) if policy.privileged else policy
    return any(positive_id(deal.get('pipeline_id')) == item['pipeline_id'] and
               (not item['status_ids'] or positive_id(deal.get('status_id')) in item['status_ids'])
               for item in personal.pipeline_scope())


def eligible(profile: dict, event: str, row: dict, channel: str) -> bool:
    if (event not in EVENTS or profile.get('active') is not True or profile.get('tenant_status') != 'active'
            or str(profile.get('tenant_id')) != str(row.get('tenant_id'))):
        return False
    policy = TenantPolicy(profile)
    deal = row.get('deal') or row
    allowed = task_owner(profile, row) if event.startswith('task_') else (not deal.get('deleted_at') and policy.can_access_deal(deal))
    if event == 'incoming_message':
        allowed = allowed and row.get('direction') == 'incoming' and positive_id((row.get('deal') or {}).get('status_id')) != 142
    return bool(allowed and channel in notification_channels(policy, event=event, tenant_id=str(row['tenant_id'])))


def transitions(kind: str, row: dict, previous: dict | None, baseline: object, now: object) -> tuple[dict, list[tuple[str, str]]]:
    """First observations of old data are baselines, never a historical broadcast."""
    events = []
    start, current = timestamp(baseline), timestamp(now)
    raw = row.get('raw') or {}
    if kind == 'task':
        route, due = task_route(row), timestamp(row.get('due_at'))
        armed = due > start and (not previous or previous.get('due') != due or previous.get('armed') is True)
        sequence = int((previous or {}).get('sequence', 0)) + int(bool(previous and previous.get('route') != route))
        state = {'route': route, 'due': due, 'armed': armed, 'sequence': sequence,
                 'overdue': (previous or {}).get('overdue')}
        if row.get('completed') is False:
            if (previous and previous.get('route') != route) or (not previous and timestamp(raw.get('created_at')) > start):
                events.append(('task_assigned', version([route, sequence])))
            if armed and 0 < due <= current:
                stamp = version([route, due])
                if stamp != state['overdue']:
                    events.append(('task_overdue', stamp))
                    state['overdue'] = stamp
        return state, events
    if kind == 'lead':
        if not previous and timestamp(raw.get('created_at')) > start and not row.get('deleted_at'):
            events.append(('new_lead', 'created'))
        return {'seen': True}, events
    if kind == 'message':
        if not previous and row.get('direction') == 'incoming' and timestamp(row.get('happened_at')) > start:
            events.append(('incoming_message', 'incoming'))
        return {'seen': True}, events
    raise ValueError('Invalid resource kind')
