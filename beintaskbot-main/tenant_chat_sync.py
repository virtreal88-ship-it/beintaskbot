"""Prepare complete bounded history before any cache write."""
import asyncio
from typing import Callable, Awaitable
from tenant_chat_reader import read_conversations
from tenant_policy import TenantPolicy
from tenant_platform import TenantPlatformError


async def sync(profile: dict, lead_id: int, request: Callable[..., Awaitable[dict]],
               member: Callable[[str, int], dict | None], get_deal: Callable[..., dict | None],
               list_messages: Callable[..., list[dict]], upsert_messages: Callable[..., int],
               upsert_deals: Callable[..., int], normalize: Callable[[dict, str], dict],
               channel: Callable[[dict], str]) -> list[dict]:
    tenant = str(profile['tenant_id'])

    async def authorize() -> tuple[dict, dict]:
        current = await asyncio.to_thread(member, tenant, int(profile['telegram_id']))
        deal = await asyncio.to_thread(get_deal, tenant_id=tenant, kommo_lead_id=lead_id)
        if not current or current.get('tenant_status') != 'active' or not TenantPolicy(current).can_access_deal(deal):
            raise TenantPlatformError('Çat tarixçəsi üçün icazəniz yoxdur.')
        return current, deal

    await authorize()
    conversations = await read_conversations(tenant, lead_id, request)
    normalized, seen = [], {}
    for talk, messages in conversations:
        for raw in messages:
            item = normalize(raw, str(raw.get('origin') or channel(talk)))
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
    if int(live.get('id') or 0) != lead_id or not TenantPolicy(current).can_access_deal({**live, 'tenant_id':tenant}):
        raise TenantPlatformError('Sövdələşmənin vərəqi və ya mərhələsi dəyişib.')
    if normalized:
        await asyncio.to_thread(upsert_messages, tenant_id=tenant, kommo_lead_id=lead_id, messages=normalized)
        latest = max(normalized, key=lambda item: item.get('happened_at') or '')
        # Compare the read snapshot; concurrent preview writes remain possible.
        if not deal.get('last_message_at') or str(latest.get('happened_at') or '') >= str(deal['last_message_at']):
            await asyncio.to_thread(upsert_deals, tenant_id=tenant, deals=[{
                **deal, 'last_message':latest.get('body') or '', 'last_message_at':latest.get('happened_at'),
                'channel':latest.get('channel') or '', 'raw':deal.get('raw') or {}}])
    return await asyncio.to_thread(list_messages, tenant_id=tenant, kommo_lead_id=lead_id)
