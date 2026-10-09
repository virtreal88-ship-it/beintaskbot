"""Strict commands and server-side latest-inbound Talk routing."""
import hashlib
import json
import uuid
from tenant_linear_policy import TenantLinearError


def command(data: dict) -> dict:
    try:
        request_id = str(uuid.UUID(str(data.get('request_id') or '')))
        lead_id = int(data.get('lead_id') or 0)
    except (ValueError, TypeError):
        raise TenantLinearError('Sorğu kodu və sövdələşmə düzgün deyil.') from None
    text = data.get('text')
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 8000 or not 0 < lead_id < 2**63:
        raise TenantLinearError('Sövdələşmə və mesaj mətni tələb olunur (maksimum 8000 simvol).')
    return {'request_id': request_id, 'lead_id': lead_id, 'text': text.strip()}


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def resolve_route(tenant: str, lead_id: int, request) -> dict:
    payload = await request(tenant, 'GET', 'talks', params={'filter[entity_id][]': lead_id, 'filter[entity_type]': 'lead', 'limit': 50})
    talks = (payload.get('_embedded') or {}).get('talks') or []
    if (payload.get('_links') or {}).get('next') or not 0 < len(talks) <= 6:
        raise TenantLinearError('Çat mənbəyi tam yoxlanmadı. Kommo-da cavab verin.', 409)
    incoming = []
    for talk in talks:
        if not isinstance(talk, dict) or int(talk.get('entity_id') or 0) != lead_id or talk.get('entity_type') not in {'lead', 'leads', 2, '2'}:
            raise TenantLinearError('Çatın sövdələşmə ilə əlaqəsi təsdiqlənmədi.', 409)
        talk_id = int(talk.get('talk_id') or talk.get('id') or 0)
        chat_id = str(talk.get('chat_id') or '')
        if not talk_id or not chat_id:
            raise TenantLinearError('Çat identifikatoru yoxdur.', 409)
        page = await request(tenant, 'GET', f'talks/{talk_id}/messages', params={'limit': 250, 'page': 1})
        if (page.get('_links') or {}).get('next'):
            raise TenantLinearError('Çat tarixçəsi natamamdır. Kommo-da cavab verin.', 409)
        rows = (page.get('_embedded') or {}).get('messages') or page.get('messages') or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get('chat_id') or '') != chat_id:
                raise TenantLinearError('Mesajın mənbəyi təsdiqlənmədi.', 409)
            if row.get('type') != 'incoming':
                continue
            timestamp = int(row.get('sec_created_at') or int(row.get('created_at') or 0) * 1000)
            if not timestamp or not row.get('id') or not row.get('origin'):
                raise TenantLinearError('Daxil olan mesajın mənbəyi məlum deyil.', 409)
            incoming.append({'talk_id': talk_id, 'chat_id': chat_id, 'origin': str(row['origin']),
                             'message_id': str(row['id']), 'timestamp': timestamp,
                             'closed': talk.get('is_in_work') is False or talk.get('is_closed') is True})
    if not incoming:
        raise TenantLinearError('Daxil olan mesaj tapılmadı; başqa kanala avtomatik keçid edilmir.', 409)
    incoming.sort(key=lambda row: row['timestamp'], reverse=True)
    latest = incoming[0]
    if latest['closed'] or any(row['timestamp'] == latest['timestamp'] and row['chat_id'] != latest['chat_id'] for row in incoming[1:]):
        raise TenantLinearError('Son mesajın çatı bağlıdır və ya mənbə qeyri-müəyyəndir. Kommo-da yoxlayın.', 409)
    return latest
