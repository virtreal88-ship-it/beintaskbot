"""Neutral external push content. No company, person or task data."""
import re
from urllib.parse import parse_qs, urlsplit

_DEFAULT = {'title': 'CRM Smart Assistant',
            'body': 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
            'url': '/app', 'tag': 'crm-task-approval'}
_PAYLOADS = {
    'deal_completion_requested': {
        'title': 'CRM Smart Assistant',
        'body': 'Sövdələşmə tamamlanması üçün təsdiq var. Kabinetdə yoxlayın.',
        'url': '/app?view=approvals', 'tag': 'crm-deal-approval', 'kind': 'deal_approval'},
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
for _event, _body, _view in (
    ('task_assigned', 'Yeni tapşırıq var. Kabinetdə yoxlayın.', 'tasks'),
    ('task_overdue', 'Gecikmiş tapşırıq var. Kabinetdə yoxlayın.', 'tasks'),
    ('new_lead', 'Yeni sövdələşmə var. Kabinetdə yoxlayın.', 'customers'),
    ('incoming_message', 'Yeni mesaj var. Kabinetdə yoxlayın.', 'customers'),
):
    _PAYLOADS[_event] = {'title': 'CRM Smart Assistant', 'body': _body,
                         'url': '/app?view=' + _view, 'tag': 'crm-' + _event, 'kind': 'crm_notice', 'event': _event}


def _safe_dialog_target(value: object) -> str:
    target=str(value or '')
    try:
        parts=urlsplit(target);query=parse_qs(parts.query,keep_blank_values=True)
        channel=query.get('channel_id',[''])[0];lead=query.get('lead_id',[''])[0]
        conversation=query.get('conversation_id',[''])[0]
        if (parts.path!='/app' or parts.scheme or parts.netloc or parts.fragment
                or query.get('view')!=['dialogs'] or query.get('open_dialog')!=['1']
                or query.get('provider')!=['connect'] or not re.fullmatch(r'[0-9a-f-]{36}',channel,re.I)
                or not re.fullmatch(r'\d{1,20}',lead) or not conversation or len(conversation)>220
                or re.search(r'[\x00-\x1f\x7f]',conversation)):
            return ''
    except (TypeError,ValueError):
        return ''
    return target


def payload_for(event: str | None, target_url: str = '') -> dict[str, str]:
    """Return an independent copy; unknown events retain the old default."""
    payload=dict(_PAYLOADS.get(event, _DEFAULT))
    if event=='incoming_message':
        safe_target=_safe_dialog_target(target_url)
        if safe_target:payload['url']=safe_target
    return payload
