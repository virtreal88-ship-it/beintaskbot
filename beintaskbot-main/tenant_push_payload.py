"""Neutral external push content. No company, person or task data."""

_DEFAULT = {'title': 'CRM Smart Assistant',
            'body': 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
            'url': '/app', 'tag': 'crm-task-approval'}
_PAYLOADS = {
    'task_approval_decided': {
        'title': 'CRM Smart Assistant',
        'body': 'Tapşırıq təsdiqinin nəticəsi hazırdır. Kabinetdə yoxlayın.',
        'url': '/app?view=tasks', 'tag': 'crm-task-decision', 'kind': 'task_decided'},
    'linear_done': {
        'title': 'CRM Smart Assistant',
        'body': 'Linear tapşırığının statusu dəyişib. Kabinetdə yoxlayın.',
        'url': '/app?view=linear', 'tag': 'crm-linear', 'kind': 'linear'},
    'hot_order_available': {
        'title': 'CRM Smart Assistant',
        'body': 'Yeni isti sifariş var. Kabinetdə yoxlayın.',
        'url': '/app?view=hot_orders', 'tag': 'crm-hot-order', 'kind': 'hot_order'},
    'hot_order_completion_requested': {
        'title': 'CRM Smart Assistant',
        'body': 'İsti sifariş üzrə yenilik var. Kabinetdə yoxlayın.',
        'url': '/app?view=approvals', 'tag': 'crm-hot-order-review', 'kind': 'hot_order_review'},
    'hot_order_completion_decided': {
        'title': 'CRM Smart Assistant',
        'body': 'İsti sifariş üzrə yenilik var. Kabinetdə yoxlayın.',
        'url': '/app?view=hot_orders', 'tag': 'crm-hot-order-review', 'kind': 'hot_order_decided'},
}
_PAYLOADS['linear_status_changed'] = _PAYLOADS['linear_done']


def payload_for(event: str | None) -> dict[str, str]:
    """Return an independent copy; unknown events retain the old default."""
    return dict(_PAYLOADS.get(event, _DEFAULT))
