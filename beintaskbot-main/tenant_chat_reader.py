"""Bounded, tenant-bound Talk reads; never follows provider-supplied URLs."""
import asyncio
from typing import Awaitable, Callable
from tenant_linear_policy import TenantLinearError

KommoRequest = Callable[..., Awaitable[dict]]

MAX_TALKS = 6
MAX_PAGES = 4
PAGE_SIZE = 250


def fail() -> TenantLinearError:
    return TenantLinearError('Çat tarixçəsi tam yoxlanmadı. Kommo-da yoxlayın.', 409)


def rows(payload: dict, key: str) -> list[dict]:
    if not isinstance(payload, dict):
        raise fail()
    embedded = payload.get('_embedded', {})
    if embedded is None:
        embedded = {}
    if not isinstance(embedded, dict):
        raise fail()
    result = embedded.get(key, payload.get(key, []))
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise fail()
    return result


def continuation(payload: dict) -> bool:
    links = payload.get('_links', {})
    if links is None:
        links = {}
    if not isinstance(links, dict):
        raise fail()
    # Only the presence is used, never href (even if it points outside Kommo).
    return 'next' in links and links['next'] is not None


async def read_talks(tenant: str, lead_id: int, request: KommoRequest) -> list[dict]:
    payload = await request(tenant, 'GET', 'talks', params={
        'filter[entity_id][]': lead_id, 'filter[entity_type]': 'lead', 'limit': 50})
    talks = rows(payload, 'talks')
    if continuation(payload) or len(talks) > MAX_TALKS:
        raise fail()
    seen = set()
    for talk in talks:
        try:
            talk_id = int(talk.get('talk_id') or talk.get('id') or 0)
            entity_id = int(talk.get('entity_id') or 0)
        except (ValueError, TypeError):
            raise fail() from None
        if (entity_id != lead_id or talk.get('entity_type') not in ('lead', 'leads', 2, '2')
                or talk_id <= 0 or not talk.get('chat_id') or talk_id in seen):
            raise fail()
        seen.add(talk_id)
    return talks


async def read_messages(tenant: str, talk: dict, request: KommoRequest) -> list[dict]:
    talk_id = int(talk.get('talk_id') or talk.get('id'))
    chat_id = str(talk['chat_id'])
    seen, result = {}, []
    for number in range(1, MAX_PAGES + 1):
        payload = await request(tenant, 'GET', f'talks/{talk_id}/messages', params={'limit': PAGE_SIZE, 'page': number})
        messages = rows(payload, 'messages')
        if len(messages) > PAGE_SIZE:
            raise fail()
        progress = False
        for message in messages:
            if str(message.get('chat_id') or '') != chat_id or not message.get('id'):
                raise fail()
            key = str(message['id'])
            if key in seen:
                if seen[key] != message:
                    raise fail()  # Moving/changing pages cannot be trusted as a snapshot.
                continue
            seen[key] = message
            result.append(message)
            progress = True
        more = continuation(payload) or len(messages) == PAGE_SIZE
        if not more:
            return result
        if not progress or number == MAX_PAGES:
            raise fail()
    raise fail()


async def read_conversations(tenant: str, lead_id: int, request: KommoRequest) -> list[tuple[dict, list[dict]]]:
    # Sequential calls share the tenant provider's rate limiter. No unlimited fan-out.
    async with asyncio.timeout(40):
        talks = await read_talks(tenant, lead_id, request)
        return [(talk, await read_messages(tenant, talk, request)) for talk in talks]
