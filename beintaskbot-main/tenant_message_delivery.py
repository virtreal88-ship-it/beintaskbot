"""Read-only, exact-source delivery evidence for the visible history page.

Connect owns ACK state. CRM rows may predate ACKs (or the optional self echo).
Never update CRM messages, infer delivery from direction, or look up by text.
"""
import asyncio
import json
import re
import uuid
from urllib.parse import urlsplit

import aiohttp
from tenant_connect_media import PublicResolver
from tenant_connect_transport import CONNECT_URL, connect_route
from tenant_operator_access import access, assert_route
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError

STATES = {'pending', 'retrying', 'unknown', 'failed', 'sent', 'delivered', 'read'}


def candidates(result):
    batches = {}
    if result.get('dialog', {}).get('provider') != 'connect':
        return batches
    for message in result.get('messages', [])[:50]:
        if message.get('direction') != 'outgoing' or message.get('source') != 'connect':
            continue
        try:
            channel = str(uuid.UUID(str(message.get('source_channel_id') or str(message.get('channel', '')).removeprefix('connect:'))))
            external = str(message.get('external_id') or '')
            if not external.startswith('connect:'): continue
            ident = str(uuid.UUID(external[8:]))
            lead = int(message.get('source_lead_id') or result['dialog']['lead_id'])
            conversation = str(message.get('source_conversation_id') or result['dialog']['conversation_id'])
            if not conversation or len(conversation) > 200: continue
        except (ValueError, TypeError, KeyError):
            continue
        key = (channel, conversation, lead)
        if key not in batches and len(batches) >= 8: continue
        batches.setdefault(key, {})[ident] = message
    return batches


def authorized_routes(profile, batches):
    routes = {}
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _, policy = access(cur, profile, 'dialogs')
            for key in batches:
                channel, conversation, lead = key
                selected = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead, allow_merged=True)
                # Do not resolve historical aliases into another receipt route.
                if (str(selected['connect_channel_id']), selected['conversation_id'], int(selected['lead_id'])) != key:
                    continue
                route = connect_route(cur, tenant, lead, historical_read=True)
                if route and (route['channel_id'], route['conversation_id']) == (channel, conversation):
                    routes[key] = route
    return routes


def validated_statuses(data, route, requested):
    if (not isinstance(data, dict) or data.get('channelId') != route['channel_id']
            or data.get('conversationId') != route['conversation_id']
            or not isinstance(data.get('data'), list) or len(data['data']) > len(requested)):
        return {}
    statuses = {}
    for item in data['data']:
        if not isinstance(item, dict): return {}
        ident, state = item.get('id'), item.get('status')
        if ident not in requested or ident in statuses or state not in STATES: return {}
        provider = item.get('externalMessageId')
        if state in {'sent', 'delivered', 'read'} and (not isinstance(provider, str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}', provider)):
            continue
        existing = requested[ident].get('provider_message_id')
        if existing and provider and existing != provider: continue
        statuses[ident] = state
    return statuses


async def fetch_statuses(route, requested):
    origin = urlsplit(CONNECT_URL)
    if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {'', '/'} or origin.port not in {None, 443}):
        return {}
    url = CONNECT_URL.rstrip('/') + '/v1/channels/' + route['channel_id'] + '/message-statuses'
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver()),
                timeout=aiohttp.ClientTimeout(total=3, connect=1), auto_decompress=False, trust_env=False) as session:
            async with session.get(url, params={'conversationId': route['conversation_id'], 'messageIds': ','.join(requested)},
                    allow_redirects=False, headers={'x-api-key': route['api_key'], 'Accept': 'application/json', 'Accept-Encoding': 'identity'}) as response:
                if (response.status != 200 or response.headers.get('X-Connect-Channel-Id') != route['channel_id']
                        or response.headers.get('X-Connect-Conversation-Id') != route['conversation_id']
                        or response.headers.get('Content-Encoding', 'identity') != 'identity'
                        or response.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                    return {}
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(8192):
                    size += len(chunk)
                    if size > 32768: return {}
                    chunks.append(chunk)
                return validated_statuses(json.loads(b''.join(chunks)), route, requested)
    except Exception:
        return {}


def apply_status(message, state):
    """Never regress stronger saved positive evidence on a stale lookup."""
    old = message.get('delivery_state')
    rank = {'sent': 1, 'delivered': 2, 'read': 3}
    if old in {'delivered', 'read'} and rank.get(state, 0) < rank[old]: return
    if old == 'sent' and state in {'pending', 'retrying', 'unknown'}: return
    if old == 'failed' and state in {'pending', 'retrying', 'sent'}: return
    message['delivery_state'] = state


async def enrich_history(profile, result):
    batches = candidates(result)
    if not batches: return result
    routes = await asyncio.to_thread(authorized_routes, profile, batches)
    keys = list(routes)
    # Max eight exact sources, max 50 visible messages, no provider calls or DB writes.
    statuses = await asyncio.gather(*(fetch_statuses(routes[key], batches[key]) for key in keys))
    latest = await asyncio.to_thread(authorized_routes, profile, batches)
    for key, values in zip(keys, statuses):
        if latest.get(key) != routes[key]:
            raise TenantLinearError('Источник изменился. Откройте диалог заново.', 409)
        for ident, state in values.items():
            apply_status(batches[key][ident], state)
    return result
