"""Display-only context from an already authorized and revalidated risk source.

No model, extra provider request, inferred customer identity or workflow mutation.
Keep this out of event fingerprints and neutral notification/push payloads.
"""
import re
from tenant_proactive_risk_policy import source_record_timestamp


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    # Source prose is untrusted data, never instructions. Render only as text.
    value = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', ' ', value)
    value = ' '.join(value.split())
    return value[:limit] + ('…' if len(value) > limit else '')


def detail_context(source, kind, findings):
    linear = kind == 'linear'
    rules = {item.get('rule_id') for item in findings}
    topic = next((name for rule, name in (
        ('access_price_claim', 'price_access'), ('access_control_claim', 'access'),
        ('price_control_claim', 'price'), ('service_failure_claim', 'outage'),
    ) if rule in rules), '')
    if not linear:
        topic = next((name for name in ('price_access', 'access', 'price', 'outage')
                      if any(str(rule).endswith('_' + name) for rule in rules)), '')
    title = _text(source.get('title') if linear else source.get('text'), 240)
    description = _text(source.get('description') if linear else source.get('text'), 1400)
    assignee = source.get('assignee') or {}
    priority = source.get('priority')
    updated = source_record_timestamp(source.get('updatedAt') if linear else (source.get('raw') or {}).get('updated_at'))
    due = source.get('dueDate') if linear else source.get('due_at')
    return {
        'identifier': _text(source.get('identifier'), 40) if linear else '',
        'title': title,
        'excerpt': description,
        'excerpt_truncated': len(' '.join(str(source.get('description') if linear else source.get('text') or '').split())) > 1400,
        'project': _text((source.get('project') or {}).get('name'), 120) if linear else '',
        'priority': priority if linear and type(priority) is int and priority in {1, 2, 3, 4} else None,
        'assigned': bool(assignee.get('id')) if linear else bool(source.get('responsible_id')),
        'due_date': _text(due.isoformat() if hasattr(due, 'isoformat') else due, 60),
        'source_updated_at': updated.isoformat() if updated else '',
        'status': _text((source.get('state') or {}).get('name'), 100) if linear else '',
        'topic': topic,
        'overdue': 'overdue_task' in rules or any('_overdue_' in str(rule) for rule in rules),
        'stale_record': bool(rules & {'stale_source_update', 'crm_task_stale_source_update'}),
        'source_checked_at': str(source.get('synced_at') or '') if not linear else '',
        'source_mode': 'live' if linear else 'recent_cache',
    }

