"""Prepare complete bounded history before any cache write."""
import asyncio
from typing import Callable, Awaitable
from tenant_chat_reader import read_conversations, read_recent_conversations
from datetime import datetime, timezone
from tenant_policy import TenantPolicy
from tenant_platform import TenantPlatformError


async def sync(profile: dict, lead_id: int, request: Callable[..., Awaitable[dict]],
               member: Callable[[str, int], dict | None], get_deal: Callable[..., dict | None],
               list_messages: Callable[..., list[dict]], upsert_messages: Callable[..., int],
               update_preview: Callable[..., int], normalize: Callable[[dict, str], dict],
               channel: Callable[[dict], str], *, recent=False) -> list[dict]:
    tenant = str(profile['tenant_id'])

    async def authorize() -> tuple[dict, dict]:
        current = await asyncio.to_thread(member, tenant, int(profile['telegram_id']))
        deal = await asyncio.to_thread(get_deal, tenant_id=tenant, kommo_lead_id=lead_id)
        if not current or current.get('tenant_status') != 'active' or not TenantPolicy(current).can_read_kommo_chat(deal):
            raise TenantPlatformError('Çat tarixçəsi üçün icazəniz yoxdur.')
        return current, deal

    _, snapshot = await authorize()
    if recent:
        # Overlap the cached watermark to include same-second arrivals. No
        # provider URL, conversation name or phone number is used as identity.
        value = snapshot.get('last_message_at')
        try:
            stamp = value.timestamp() if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError):
            stamp = datetime.now(timezone.utc).timestamp() - 86400
        conversations = await read_recent_conversations(tenant, lead_id, request, max(0, int(stamp) - 300))
    else:
        conversations = await read_conversations(tenant, lead_id, request)
    normalized, seen = [], {}
    for talk, messages in conversations:
        for raw in messages:
            item = normalize(raw, str(raw.get('origin') or channel(talk)))
            if str(item.get('channel') or '').startswith('connect:'):
                raise TenantPlatformError('Источник сообщения не относится к Kommo.')
            key = item['external_id']
            if key in seen:
                # Cache keys are lead/message, not lead/chat/message. Conflicts
                # must not silently overwrite a different channel's message.
                if seen[key] != item:
                    raise TenantPlatformError('Mesaj identifikatorları ziddiyyətlidir.')
                continue
            seen[key] = item; normalized.append(item)
    current, deal = await authorize()
    live = await request(tenant, 'GET', f'leads/{lead_id}')
    if int(live.get('id') or 0) != lead_id or not TenantPolicy(current).can_read_kommo_chat({**live, 'tenant_id':tenant}):
        raise TenantPlatformError('Sövdələşmənin vərəqi və ya mərhələsi dəyişib.')
    if normalized:
        await asyncio.to_thread(upsert_messages, tenant_id=tenant, kommo_lead_id=lead_id, messages=normalized)
        latest = max(normalized, key=lambda item: item.get('happened_at') or '')
        if not deal.get('last_message_at') or str(latest.get('happened_at') or '') >= str(deal['last_message_at']):
            await asyncio.to_thread(update_preview, tenant_id=tenant, lead_id=lead_id,
                body=latest.get('body') or '', happened_at=latest.get('happened_at'),
                channel=latest.get('channel') or '')
    if recent:
        return []  # HTTP boundary reauthorizes and reads exactly one cached page.
    cached = await asyncio.to_thread(list_messages, tenant_id=tenant, kommo_lead_id=lead_id)
    return [row for row in cached if not str(row.get('channel') or '').startswith('connect:')]
