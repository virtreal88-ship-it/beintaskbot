"""External push payloads; Connect message details are included only for an
already-authorized incoming-message delivery to an opted-in device.
"""
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


def _notification_text(value: object, limit: int, *, display_label: bool = False) -> str:
    """Bound user-visible text and remove control/route-like identifiers."""
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    # WhatsApp/Connect addressing identifiers are never human display labels.
    if display_label and ('@' in value or value.casefold() in {'customer','client','contact','unknown'}):
        return ''
    if not display_label:
        value = re.sub(r'(?i)\S+@(?:g\.us|s\.whatsapp\.net|c\.us|lid)\b', '', value)
        value = re.sub(r'(?i)(?:^|\s)\[CRM:[^\]]*\]', ' ', value)
        value = re.sub(r'(?i)\[Attachment pending_processing: contents not read\]', ' ', value)
        value = re.sub(r'\s+', ' ', value).strip()
    return value[:limit]


def _incoming_copy(context: object) -> tuple[str, str]:
    if not isinstance(context, dict):
        return 'CRM Smart Assistant', 'Yeni mesaj var. Kabinetdə yoxlayın.'
    group = context.get('conversation_kind') == 'group'
    name = _notification_text(context.get('display_name'), 80, display_label=True)
    if group:
        author = _notification_text(context.get('author'), 50, display_label=True) or 'İştirakçı'
        title = f'{name or "Qrup"} · {author}'
    else:
        phone = _notification_text(context.get('phone'), 28, display_label=True)
        if phone and not re.fullmatch(r'\+?[0-9][0-9 ()-]{4,25}', phone):
            phone = ''
        title = name or phone or 'Yeni mesaj'
        if name and phone:
            title += ' · ' + phone
    body = _notification_text(context.get('text'), 180)
    if not body:
        kind = str(context.get('message_type') or '').casefold()
        if kind in {'audio', 'voice', 'ptt', 'voicenote', 'audio_message'}:
            body = 'Səsli mesaj'
        elif kind in {'image', 'photo', 'image_message', 'sticker'}:
            body = 'Foto'
        elif kind in {'video', 'video_message'}:
            body = 'Video'
        elif kind in {'document', 'file', 'document_message'}:
            body = 'Sənəd'
        else:
            body = 'Yeni mesaj var. Kabinetdə yoxlayın.'
    return title[:120], body


def payload_for(event: str | None, target_url: str = '', notification_context: object = None) -> dict[str, str]:
    """Return an independent copy; unknown events retain the old default."""
    payload=dict(_PAYLOADS.get(event, _DEFAULT))
    if event=='incoming_message':
        safe_target=_safe_dialog_target(target_url)
        if safe_target:payload['url']=safe_target
        payload['title'],payload['body']=_incoming_copy(notification_context)
    return payload
