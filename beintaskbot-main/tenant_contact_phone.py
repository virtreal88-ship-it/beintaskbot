"""Display-only device contact details for an authorized WhatsApp dialogue."""
import asyncio
import json
import re

import aiohttp
from tenant_platform import _connect
from tenant_operator_access import access, assert_route
from tenant_connect_transport import connect_route
from tenant_linear_policy import TenantLinearError


def authorized_route(profile, params):
    if params.get('provider') != 'connect' or not re.fullmatch(r'[1-9][0-9]{5,21}@lid', str(params.get('conversation_id') or '')):
        raise TenantLinearError('Контакт не найден.', 404)
    with _connect() as conn, conn.cursor() as cur:
        tenant, _, policy = access(cur, profile, 'dialogs')
        selected = assert_route(cur, tenant, policy, 'connect', params.get('conversation_id'), params.get('channel_id'), params.get('lead_id'))
        route = connect_route(cur, tenant, selected['lead_id'])
        if (not route or not route.get('native') or selected.get('conversation_kind') == 'group'
                or route['channel_id'] != str(selected['connect_channel_id'])
                or route['conversation_id'] != selected['conversation_id']):
            raise TenantLinearError('Контакт недоступен.', 404)
        return {'tenant': str(tenant), **route}


async def fetch_contact(route):
    from tenant_call_sources import service
    from tenant_call_identity import valid
    url, token = service()
    binding = route['native']
    peer = route['conversation_id']
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3, connect=1),
                auto_decompress=False, trust_env=False) as session:
            async with session.post(url+'/v1/sources/'+binding['gateway_id']+'/messages/contact',
                    json={'scope': binding['scope'], 'chat': peer}, allow_redirects=False,
                    headers={'Authorization': 'Bearer '+token, 'Accept-Encoding': 'identity'}) as response:
                if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
                    return {}
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(2048):
                    size += len(chunk)
                    if size > 4096: return {}
                    chunks.append(chunk)
                data = json.loads(b''.join(chunks))
                if (not isinstance(data, dict) or data.get('scope') != binding['scope']
                        or data.get('peer_jid') != peer or not valid(peer, data.get('caller'))):
                    return {}
                caller = data['caller']
                phone = caller['phone_jid'].split('@')[0]
                name = caller['display_name'].strip()
                if (re.fullmatch(r'[+0-9(). -]+', name)
                        or re.search(r'@(?:s\.whatsapp\.net|c\.us|g\.us|lid)$', name, re.I)
                        or name.casefold() in {'whatsapp contact', 'контакт whatsapp', 'whatsapp kontaktı'}):
                    name = ''
                return {'phone': '+'+phone if re.fullmatch(r'[1-9][0-9]{6,14}', phone) else '',
                        'display_name': name}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
        return {}


async def get_phone(profile, params):
    route = await asyncio.to_thread(authorized_route, profile, params)
    details = await fetch_contact(route)
    # Recheck current membership, selected route and generation after I/O.
    latest = await asyncio.to_thread(authorized_route, profile, params)
    if any(latest.get(k) != route.get(k) for k in ('tenant', 'channel_id', 'conversation_id', 'lead_id', 'api_key', 'native')):
        raise TenantLinearError('Источник изменился. Откройте диалог заново.', 409)
    return {'status': 'available' if any(details.values()) else 'unavailable', 'display_only': True,
            'phone': details.get('phone', ''), 'display_name': details.get('display_name', ''),
            'provider': 'connect', 'channel_id': route['channel_id'],
            'conversation_id': route['conversation_id'], 'lead_id': int(params['lead_id'])}
