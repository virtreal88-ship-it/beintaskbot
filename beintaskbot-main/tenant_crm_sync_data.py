"""Provider page normalization and stable pagination, without external calls."""
from datetime import datetime, timezone

from tenant_policy import positive_id

PAGE_SIZE = 250


def timestamp(value: object) -> str | None:
    try:
        number = int(value or 0)
        return datetime.fromtimestamp(number, timezone.utc).isoformat() if number > 0 else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def page_items(payload: dict, resource: str) -> list[dict]:
    # 204 is represented by {}. Invalid nonempty replies must not end a job.
    if not payload:
        return []
    embedded = payload.get('_embedded')
    rows = embedded.get(resource) if isinstance(embedded, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid CRM page')
    if len(rows) > PAGE_SIZE:
        raise ValueError('Oversized CRM page')
    return rows


def phone(contact: dict) -> str:
    for field in contact.get('custom_fields_values') or []:
        if isinstance(field, dict) and str(field.get('field_code') or '').upper() == 'PHONE':
            values = field.get('values')
            if isinstance(values, list) and values and isinstance(values[0], dict):
                return str(values[0].get('value') or '')[:80]
    return ''


def normalize_page(resource: str, rows: list[dict], stage_names: dict[int, str]) -> list[dict]:
    result = []
    for item in rows:
        record_id = positive_id(item.get('id'))
        if not record_id:
            raise ValueError('CRM record ID missing')
        if resource == 'leads':
            contacts = (item.get('_embedded') or {}).get('contacts') or []
            contact = contacts[0] if contacts and isinstance(contacts[0], dict) else {}
            status = positive_id(item.get('status_id'))
            result.append({'kommo_lead_id': record_id, 'pipeline_id': positive_id(item.get('pipeline_id')),
                'status_id': status, 'stage_name': stage_names.get(status, 'Mərhələ göstərilməyib'),
                'name': str(item.get('name') or 'Adsız sövdələşmə'),
                'contact_name': str(contact.get('name') or ''), 'phone': phone(contact),
                'source_updated_at': timestamp(item.get('updated_at')), 'raw': item})
        else:
            result.append({'kommo_task_id': record_id,
                'kommo_lead_id': positive_id(item.get('entity_id')) if item.get('entity_type') in ('leads', 2, '2') else 0,
                'text': str(item.get('text') or item.get('complete_till_at') or 'Tapşırıq'),
                'due_at': timestamp(item.get('complete_till')), 'responsible_id': positive_id(item.get('responsible_user_id')),
                'completed': bool(item.get('is_completed')), 'raw': item})
    return result
