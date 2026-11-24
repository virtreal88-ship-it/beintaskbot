"""Display-only device contact details for an authorized WhatsApp dialogue."""
import asyncio
import hashlib
import json
import re

import aiohttp
from tenant_platform import _connect
from tenant_operator_access import access, assert_route
from tenant_connect_transport import connect_route
from tenant_linear_policy import TenantLinearError


def contact_source_revision(gateway_id, account, generation):
    # Opaque cache key, not a gateway identifier or routing credential.
    return hashlib.sha256(json.dumps([str(gateway_id),str(account),str(generation)]).encode()).hexdigest()


def attach_list_source_revisions(cur,tenant,rows):
    import os
    from tenant_schema_preflight import ready
    candidates=[row for row in rows if row.get('provider')=='connect'
        and re.fullmatch(r'[1-9][0-9]{5,21}@lid',str(row.get('conversation_id') or ''))
        and row.get('customer_kind')!='group' and not row.get('contact_identity_conflict')
        and not row.get('archive_read_only')]
    channels=sorted({row['channel_id'] for row in candidates})
    if not channels or os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_SEND')!='1' or not ready(cur,{
          'saas_whatsapp_native_routes': ('tenant_id','channel_id','generation','enabled'),
          'saas_whatsapp_capture_generations': ('tenant_id','channel_id','generation','gateway_id','account_jid','enabled'),
          'saas_browser_call_sources': ('tenant_id','channel_id','gateway_id','account_jid','enabled'),
    },[]):return
    cur.execute('''SELECT r.channel_id::text AS channel,g.gateway_id,g.account_jid,r.generation
      FROM saas_whatsapp_native_routes r
      JOIN saas_whatsapp_capture_generations g USING (tenant_id,channel_id,generation)
      JOIN saas_browser_call_sources s ON s.tenant_id=r.tenant_id AND s.channel_id=r.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      JOIN saas_connect_channels c ON c.tenant_id=r.tenant_id AND c.connect_channel_id=r.channel_id
      WHERE r.tenant_id=%s::uuid AND r.channel_id=ANY(%s::uuid[])
        AND r.enabled AND g.enabled AND s.enabled AND c.active''',(tenant,channels))
    revisions={row['channel']:contact_source_revision(row['gateway_id'],row['account_jid'],row['generation'])
        for row in cur.fetchall()}
    for row in candidates:
        if row['channel_id'] in revisions:
            row['contact_source_revision']=revisions[row['channel_id']]


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
        from tenant_whatsapp_contact_refresh import enabled_for
        if params.get('contact_source_revision') or enabled_for(tenant, route['channel_id']):
            cur.execute("""SELECT EXISTS(SELECT 1 FROM saas_connect_identity_conflicts
               WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s) AS conflict""",
              (tenant,route['channel_id'],route['conversation_id']))
            if cur.fetchone()['conflict']:
                raise TenantLinearError('Личность контакта не подтверждена.',409)
        return {'tenant': str(tenant), **route}


async def fetch_contact(route):
    from tenant_call_sources import service
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
                        or data.get('peer_jid') != peer):
                    return {}
                caller = data.get('caller')
                # Contact presentation may prove the exact LID has a display
                # name while WhatsApp does not expose its phone number. Keep
                # identity reconciliation stricter in tenant_call_identity;
                # this endpoint never joins histories or writes a route.
                if (not isinstance(caller, dict)
                        or set(caller) not in ({'display_name'}, {'phone_jid','display_name'})):
                    return {}
                phone_jid=caller.get('phone_jid','')
                if phone_jid is None:phone_jid=''
                if not isinstance(phone_jid,str):return {}
                if phone_jid and not re.fullmatch(r'[1-9][0-9]{5,21}@s\.whatsapp\.net',phone_jid):
                    return {}
                raw_name=caller.get('display_name')
                if (not isinstance(raw_name,str) or len(raw_name)>256
                        or any(ord(char)<32 or ord(char)==127 for char in raw_name)):
                    return {}
                phone = phone_jid.split('@')[0] if '@' in phone_jid else ''
                name = raw_name.strip()
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
    binding=route['native']
    revision=contact_source_revision(binding['gateway_id'],binding['scope']['account'],binding['scope']['generation'])
    if params.get('contact_source_revision') and params['contact_source_revision'] != revision:
        raise TenantLinearError('Источник изменился. Откройте диалог заново.', 409)
    details = await fetch_contact(route)
    # Recheck current membership, selected route and generation after I/O.
    latest = await asyncio.to_thread(authorized_route, profile, params)
    if any(latest.get(k) != route.get(k) for k in ('tenant', 'channel_id', 'conversation_id', 'lead_id', 'api_key', 'native')):
        raise TenantLinearError('Источник изменился. Откройте диалог заново.', 409)
    return {'status': ('available' if details.get('phone') else
                       'hidden' if details.get('display_name') else 'unavailable'), 'display_only': True,
            'contact_source_revision': revision, 'phone': details.get('phone', ''), 'display_name': details.get('display_name', ''),
            'provider': 'connect', 'channel_id': route['channel_id'],
            'conversation_id': route['conversation_id'], 'lead_id': int(params['lead_id'])}
