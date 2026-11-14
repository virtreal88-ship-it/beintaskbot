"""Read-only scoped projection of Connect's ephemeral provider metadata cache."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import aiohttp
from tenant_connect_transport import CONNECT_URL, connect_route
from tenant_connect_media import PublicResolver
from tenant_operator_access import access, assert_route
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError


def projection(data, now=None):
    unavailable = {'status': 'unavailable'}
    if not isinstance(data, dict) or data.get('status') != 'available':
        return unavailable
    try:
        observed = datetime.fromisoformat(data['observedAt'].replace('Z', '+00:00'))
        now = now or datetime.now(timezone.utc)
        if not now - timedelta(hours=1) <= observed <= now + timedelta(minutes=1):
            return unavailable
    except (KeyError, ValueError, TypeError, AttributeError):
        return unavailable
    phones, total, unresolved = data.get('phones'), data.get('total'), data.get('unresolved')
    if (not isinstance(phones, list) or len(phones) > 2048 or type(total) is not int
            or type(unresolved) is not int or not 0 <= unresolved <= total <= 2048
            or len(phones) + unresolved > total
            or any(not isinstance(p, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', p) for p in phones)):
        return unavailable
    return {'status': 'available', 'observed_at': observed.isoformat(),
            'phones': sorted(set(phones)), 'total': total, 'unresolved': unresolved}


def authorized_route(profile, params):
    if params.get('provider') != 'connect' or not re.fullmatch(r'[0-9-]+@g\.us', str(params.get('conversation_id') or '')):
        raise TenantLinearError('Группа не найдена.', 404)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _, policy = access(cur, profile, 'dialogs')
            selected = assert_route(cur, tenant, policy, 'connect', params.get('conversation_id'), params.get('channel_id'), params.get('lead_id'))
            route = connect_route(cur, tenant, selected['lead_id'])
            if not route or route['channel_id'] != str(selected['connect_channel_id']) or route['conversation_id'] != selected['conversation_id']:
                raise TenantLinearError('Группа недоступна.', 404)
            return {'tenant': tenant, **route}


async def fetch_cached(route):
    origin = urlsplit(CONNECT_URL)
    if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {'', '/'} or origin.port not in {None, 443}):
        return {'status': 'unavailable'}
    channel = str(uuid.UUID(route['channel_id']))
    conversation = route['conversation_id']
    url = CONNECT_URL.rstrip('/') + '/v1/channels/' + channel + '/conversations/' + quote(conversation, safe='') + '/participants'
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver()),
                timeout=aiohttp.ClientTimeout(total=8, connect=3), auto_decompress=False, trust_env=False) as session:
            async with session.get(url, allow_redirects=False, headers={'x-api-key': route['api_key'],
                    'Accept': 'application/json', 'Accept-Encoding': 'identity'}) as response:
                if (response.status != 200 or response.headers.get('X-Connect-Channel-Id') != channel
                        or response.headers.get('X-Connect-Conversation-Id') != conversation
                        or response.headers.get('Content-Encoding', 'identity') != 'identity'
                        or response.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                    return {'status': 'unavailable'}
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(8192):
                    size += len(chunk)
                    if size > 65536: return {'status': 'unavailable'}
                    chunks.append(chunk)
                import json
                return projection(json.loads(b''.join(chunks)))
    except Exception:
        return {'status': 'unavailable'}


async def get_participants(profile, params):
    import asyncio
    route = await asyncio.to_thread(authorized_route, profile, params)
    result = await fetch_cached(route)
    latest = await asyncio.to_thread(authorized_route, profile, params)
    if any(latest.get(k) != route.get(k) for k in ('tenant', 'channel_id', 'conversation_id', 'api_key')):
        raise TenantLinearError('Группа изменилась. Откройте её заново.', 409)
    return result
