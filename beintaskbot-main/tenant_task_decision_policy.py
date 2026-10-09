"""Final outcomes only; a reviewer accepting a request is not success yet."""
from tenant_policy import TenantPolicy
from tenant_notifications import notification_channels

EVENT = 'task_approval_decided'


def outcome(state: dict) -> str | None:
    approval = state.get('approval') or {}
    if not isinstance(approval, dict):
        return None
    try:
        reviewer = int(approval.get('reviewer_id') or 0)
    except (ValueError, TypeError):
        return None
    if reviewer <= 0 or not (state.get('task_input') or state.get('completion_input')):
        return None
    if state.get('step') == 'done' and approval.get('status') == 'approved':
        return 'approved'
    if state.get('step') == 'rejected' and approval.get('status') == 'rejected':
        return 'rejected'
    return None


def eligible(profile: dict, item: dict, channel: str) -> bool:
    return (str(profile.get('tenant_id')) == str(item['tenant_id'])
            and profile.get('telegram_id') == item['actor_id']
            and profile.get('active') is True and profile.get('tenant_status') == 'active'
            and outcome(item['state']) == item['outcome']
            and channel in notification_channels(TenantPolicy(profile), event=EVENT, tenant_id=str(item['tenant_id'])))
