"""Read-only, bounded candidate search. A matching phone is not proof of a duplicate order."""
import asyncio
import re
from typing import Awaitable, Callable
from crm_stage_labels import stage_display_name

Read = Callable[[str, dict], Awaitable[dict]]


def normalize_phone(value: object) -> str:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError('Telefon nömrəsini tam yazın.')
    value = value.strip()
    if not value or not re.fullmatch(r'\+?[0-9 ()-]+', value):
        raise ValueError('Telefon nömrəsini tam yazın.')
    digits = re.sub(r'[^0-9]', '', value)
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 9 and not value.startswith('+') and not digits.startswith('0'):
        digits = '994' + digits
    elif len(digits) == 10 and digits.startswith('0'):
        digits = '994' + digits[1:]
    if digits.startswith('994') and len(digits) != 12:
        raise ValueError('Azərbaycan nömrəsini tam yazın.')
    if not 11 <= len(digits) <= 15 or digits.startswith('0'):
        raise ValueError('Telefon nömrəsini tam yazın.')
    return '+' + digits


def variants(phone: str) -> list[str]:
    digits = normalize_phone(phone)[1:]
    if digits.startswith('994'):
        local = digits[3:]
        return [local, '0' + local, '+' + digits, digits]
    return [digits, '+' + digits]


def phones(contact: dict) -> list[str]:
    result = []
    for field in contact.get('custom_fields_values') or []:
        if isinstance(field, dict) and field.get('field_code') == 'PHONE':
            for value in field.get('values') or []:
                if isinstance(value, dict) and isinstance(value.get('value'), str):
                    result.append(value['value'])
    return result


def rows(payload: dict, kind: str) -> list[dict]:
    result = (payload.get('_embedded') or {}).get(kind, [])
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise RuntimeError('Invalid provider response')
    return result


async def search(phone: str, source_id: int, read: Read, allowed: Callable[[dict], bool]) -> dict:
    canonical = normalize_phone(phone)
    matched, candidates, incomplete = {}, {}, False
    async with asyncio.timeout(35):
        for variant in variants(canonical):
            for page in (1, 2):
                payload = await read('contacts', {'query': variant, 'with': 'leads', 'limit': 100, 'page': page})
                contacts = rows(payload, 'contacts')
                for contact in contacts:
                    same = []
                    for number in phones(contact):
                        try:
                            if normalize_phone(number) == canonical:
                                same.append(number)
                        except ValueError:
                            continue
                    if not same:
                        continue
                    for lead in (contact.get('_embedded') or {}).get('leads') or []:
                        lead_id = int(lead.get('id') or 0)
                        if lead_id > 0 and lead_id != source_id:
                            matched[lead_id] = {'contact_name': str(contact.get('name') or '')[:240], 'phone': same[0]}
                more = bool((payload.get('_links') or {}).get('next')) or len(contacts) >= 100
                if not more:
                    break
                if page == 2:
                    incomplete = True
        ids = sorted(matched)
        if len(ids) > 50:
            incomplete = True
        if ids:
            payload = await read('leads', {'filter[id][]': ids[:50], 'limit': 50})
            for lead in rows(payload, 'leads'):
                lead_id = int(lead.get('id') or 0)
                if lead_id not in matched or lead_id not in ids[:50] or not allowed(lead):
                    continue
                candidates[lead_id] = {'id': lead_id, 'name': str(lead.get('name') or '')[:240],
                    **matched[lead_id], 'pipeline_id': int(lead.get('pipeline_id') or 0),
                    'status_id': int(lead.get('status_id') or 0), 'updated_at': lead.get('updated_at')}
            if (payload.get('_links') or {}).get('next'):
                incomplete = True
        if candidates:
            catalog = rows(await read('leads/pipelines', {'limit': 250}), 'pipelines')
            for item in candidates.values():
                pipeline = next((p for p in catalog if int(p.get('id') or 0) == item['pipeline_id']), {})
                stage = next((s for s in (pipeline.get('_embedded') or {}).get('statuses', [])
                              if int(s.get('id') or 0) == item['status_id']), {})
                item['pipeline_name'] = str(pipeline.get('name') or item['pipeline_id'])
                item['stage_name'] = stage_display_name(item['status_id'], str(stage.get('name') or item['status_id']))
    return {'phone': canonical, 'deals': list(candidates.values()), 'incomplete': incomplete}
