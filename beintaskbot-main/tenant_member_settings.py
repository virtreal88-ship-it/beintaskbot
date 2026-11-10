"""Sparse employee settings on the existing tenant workflow policy."""
import hashlib
import json

from tenant_notifications import notification_catalog, validate_notification_preferences
from tenant_hot_order_policy import validate_hot_order_settings
from tenant_policy import policy_values

SETTING_FLAGS = frozenset({'ai_enabled', 'ai_agents_manage', 'creation_requires_admin',
    'completion_requires_admin', 'deal_completion_requires_admin', 'hot_order_create',
    'hot_order_claim', 'hot_order_completion_requires_admin'})
SETTING_FIELDS = SETTING_FLAGS | {'hot_order_services', 'notifications'}
# Provider-specific grants/scopes remain in integration settings.
PERMISSION_MODULES = frozenset({'tasks', 'deals', 'customers', 'dialogs', 'support',
    'hot_orders', 'finance', 'employees', 'integrations', 'settings', 'night_report'})
NOTIFICATION_KEYS = frozenset(item['key'] for item in notification_catalog()
    if item['key'] not in {'linear_done', 'linear_status_changed', 'proactive_risk'})


def settings_snapshot(workflow, target):
    policies = policy_values(workflow or {})
    settings = (policies.get('members') or {}).get(str(target['telegram_id']), {})
    values = {key: value for key, value in settings.items() if key in SETTING_FIELDS}
    if 'notifications' in values:
        values['notifications'] = {k: v for k, v in values['notifications'].items() if k in NOTIFICATION_KEYS}
    hot = policies.get('hot_orders') or {}
    task = policies.get('task_approval') or {}
    defaults = {'ai_enabled': target.get('role') == 'owner', 'ai_agents_manage': target.get('role') == 'owner',
        'creation_requires_admin': task.get('creation_requires_admin', False),
        'completion_requires_admin': task.get('completion_requires_admin', False),
        'deal_completion_requires_admin': (policies.get('deal_completion') or {}).get('requires_admin', False),
        'hot_order_create': target.get('role') in hot.get('create_roles', ['owner', 'admin']),
        'hot_order_claim': target.get('role') in hot.get('claim_roles', ['master']),
        'hot_order_completion_requires_admin': hot.get('completion_requires_admin', True)}
    services = [{key: row.get(key) for key in ('id','name','active')} for row in hot.get('services', [])]
    version = hashlib.sha256(json.dumps([values, defaults, services], sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    events = [item for item in notification_catalog() if item['key'] in NOTIFICATION_KEYS
              and target.get('role') in item['roles']]
    return {'values': values, 'defaults': defaults, 'services': services,
            'notifications': events, 'version': version}


def apply_settings_patch(cur, *, tenant_id, owner_id, target, workflow, patch):
    from tenant_member_store import MemberCardError
    from tenant_platform_core import _json
    if (not isinstance(patch, dict) or set(patch) != {'expected_version', 'changes'}
            or not isinstance(patch['changes'], dict) or not patch['changes']
            or set(patch['changes']) - SETTING_FIELDS):
        raise MemberCardError('member_card_invalid', fields={'work_settings': 'invalid'})
    snapshot = settings_snapshot(workflow, target)
    if patch['expected_version'] != snapshot['version']:
        raise MemberCardError('member_settings_conflict', 409, fields={'work_settings': 'version_conflict'})
    policies = policy_values(workflow)
    original = (policies.get('members') or {}).get(str(target['telegram_id']), {})
    updated = dict(original)
    try:
        for key, value in patch['changes'].items():
            if key in SETTING_FLAGS:
                if value is None:
                    updated.pop(key, None)
                elif type(value) is bool:
                    updated[key] = value
                else:
                    raise ValueError('invalid flag')
            elif key == 'hot_order_services':
                if not isinstance(value, list):
                    raise ValueError('invalid services')
                updated[key] = list(dict.fromkeys(value))
            elif key == 'notifications':
                validate_notification_preferences(value)
                if set(value) - NOTIFICATION_KEYS:
                    raise ValueError('notification scope')
                # The patch contains only changed channels; preserve hidden or
                # unsupported event preferences, including integration alerts.
                notifications = {k: dict(v) for k, v in (original.get(key) or {}).items()}
                for event, channels in value.items():
                    notifications[event] = {**notifications.get(event, {}), **channels}
                updated[key] = notifications
        changed = {key: value for key, value in updated.items() if key in patch['changes']}
        if 'hot_order_services' in changed:
            # Retain historical IDs; only new grants must exist in the current
            # catalogue. Service matching still requires an active catalogue row.
            original_ids = set(original.get('hot_order_services') or [])
            changed['hot_order_services'] = [item for item in changed['hot_order_services'] if item not in original_ids]
        validate_hot_order_settings(policies.get('hot_orders') or {},
            {str(target['telegram_id']): changed})
    except (TypeError, ValueError):
        raise MemberCardError('member_card_invalid', fields={'work_settings': 'invalid'}) from None
    cur.execute('''UPDATE saas_tenant_workflow_policies
        SET value=jsonb_set(value,ARRAY[%s::text],%s::jsonb,TRUE),updated_by=%s,updated_at=now()
        WHERE tenant_id=%s::uuid AND policy_key='members' ''',
        (str(target['telegram_id']), _json(updated), int(owner_id), tenant_id))
