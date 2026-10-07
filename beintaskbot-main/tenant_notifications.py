"""Tenant notification preferences; no transport or legacy side effects here."""

CHANNELS = ('telegram', 'push')
EVENTS = {
    'new_lead': {'label': 'Yeni sövdələşmə', 'module': 'deals', 'roles': ('owner', 'admin', 'manager')},
    'incoming_message': {'label': 'Yeni mesaj', 'module': 'customers', 'roles': ('owner', 'admin', 'manager')},
    'task_assigned': {'label': 'Yeni tapşırıq', 'module': 'tasks', 'roles': ('owner', 'admin', 'manager', 'worker')},
    'task_overdue': {'label': 'Gecikmiş tapşırıq', 'module': 'tasks', 'roles': ('owner', 'admin', 'manager', 'worker')},
    'task_approval_requested': {'label': 'Tapşırıq yaradılması üçün təsdiq', 'module': 'tasks', 'roles': ('owner', 'admin')},
    'task_completion_requested': {'label': 'Tapşırıq tamamlanması üçün təsdiq', 'module': 'tasks', 'roles': ('owner', 'admin')},
    'task_approval_decided': {'label': 'Tapşırıq təsdiqinin nəticəsi', 'module': 'tasks', 'roles': ('owner', 'admin', 'manager', 'worker')},
    'hot_order_available': {'label': 'Yeni isti sifariş', 'module': 'hot_orders', 'roles': ('owner', 'admin', 'manager', 'worker', 'master')},
    'linear_done': {'label': 'Linear tapşırığı tamamlandı', 'module': 'linear', 'roles': ('owner', 'admin', 'manager', 'worker')},
}


def notification_catalog() -> list[dict]:
    """Single catalog for both the editor and server recipient checks."""
    return [{'key': key, **value, 'roles': list(value['roles']), 'channels': list(CHANNELS)}
            for key, value in EVENTS.items()]


def validate_notification_preferences(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError('Bildiriş ayarları obyekt olmalıdır.')
    for event, channels in value.items():
        if event not in EVENTS or not isinstance(channels, dict):
            raise ValueError('Bildiriş növü düzgün deyil.')
        if any(channel not in CHANNELS or not isinstance(enabled, bool)
               for channel, enabled in channels.items()):
            raise ValueError('Bildiriş kanalı üçün aktiv/deaktiv seçin.')


def available_notification_events(policy) -> list[str]:
    return [key for key, event in EVENTS.items()
            if policy.profile.get('role') in event['roles'] and policy.allows(event['module'])]


def notification_channels(policy, *, event: str, tenant_id: str) -> list[str]:
    """Fail closed. Callers must ALSO authorize the event's task/deal/service.

    This determines preferences, not resource ownership or device ownership.
    Unconfigured employees receive nothing; old global flags never opt them in.
    """
    profile = policy.profile
    if not tenant_id or str(profile.get('tenant_id')) != str(tenant_id):
        return []
    if event not in available_notification_events(policy):
        return []
    company = {**(profile.get('notification_rules') or {}), **(policy.policies.get('notifications') or {})}
    if company.get(event, True) is not True:
        return []
    preferences = policy.member_settings.get('notifications') or {}
    channels = preferences.get(event) or {}
    return [channel for channel in CHANNELS
            if channels.get(channel) is True and company.get(channel, True) is True]
