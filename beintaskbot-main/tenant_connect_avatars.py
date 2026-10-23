"""Protected, bounded WhatsApp avatar cache retrieval through Connect."""
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import aiohttp

from tenant_connect_transport import CONNECT_URL, _schema as ensure_connect_schema, connect_route
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_connect_media import PublicResolver, IMAGE_TYPES
from tenant_platform import _connect

MAX_AVATAR_BYTES = 1024 * 1024


def _image_mime(body):
    if body.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if body[:6] in {b'GIF87a', b'GIF89a'}:
        return 'image/gif'
    if body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        return 'image/webp'
    return ''


async def _fetch(route):
    origin = urlsplit(CONNECT_URL)
    if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {'', '/'} or origin.port not in {None, 443}):
        raise TenantLinearError('WhatsApp şəkli hazırda əlçatan deyil.', 503)
    channel = str(uuid.UUID(route['channel_id']))
    conversation = str(route['conversation_id'])
    url = CONNECT_URL.rstrip('/') + '/v1/channels/' + channel + '/conversations/' + quote(conversation, safe='') + '/avatar'
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver()),
                timeout=aiohttp.ClientTimeout(total=15, connect=4, sock_read=10), auto_decompress=False,
                trust_env=False) as session:
            async with session.get(url, allow_redirects=False, headers={
                    'x-api-key': route['api_key'], 'Accept': ', '.join(sorted(IMAGE_TYPES)),
                    'Accept-Encoding': 'identity'}) as response:
                if response.status == 404:
                    raise TenantLinearError('WhatsApp fotosu mövcud deyil.', 404)
                if response.status != 200:
                    raise TenantLinearError('WhatsApp fotosu hazırda yüklənmədi.', 502)
                if (response.headers.get('X-Connect-Channel-Id') != channel or
                        response.headers.get('X-Connect-Conversation-Id') != conversation or
                        response.headers.get('Content-Encoding', 'identity') != 'identity'):
                    raise ValueError('avatar scope mismatch')
                mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                length = response.headers.get('Content-Length')
                if mime not in IMAGE_TYPES or length and not 0 < int(length) <= MAX_AVATAR_BYTES:
                    raise TenantLinearError('WhatsApp fotosunun formatı dəstəklənmir.', 415)
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(32768):
                    size += len(chunk)
                    if size > MAX_AVATAR_BYTES:
                        raise TenantLinearError('WhatsApp fotosu çox böyükdür.', 413)
                    chunks.append(chunk)
                body = b''.join(chunks)
                if not body or _image_mime(body) != mime:
                    raise TenantLinearError('WhatsApp fotosunun məzmunu etibarlı deyil.', 415)
                return body, mime
    except TenantLinearError:
        raise
    except Exception:
        raise TenantLinearError('WhatsApp fotosu hazırda yüklənmədi.', 502) from None


async def get_avatar(profile, params):
    if params.get('provider') != 'connect':
        raise TenantLinearError('Bu dialoq üçün WhatsApp fotosu yoxdur.', 404)
    with _connect() as conn:
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant, _, policy = access(cur, profile, 'dialogs')
            selected = assert_route(cur, tenant, policy, 'connect', params.get('conversation_id', ''), params.get('channel_id', ''), params.get('lead_id'))
            route = connect_route(cur, tenant, selected['lead_id'])
            if not route or route['channel_id'] != str(selected['connect_channel_id']) or route['conversation_id'] != selected['conversation_id']:
                raise TenantLinearError('WhatsApp-kanal artıq əlçatan deyil.', 404)
            status = str(selected.get('avatar_status') or '')
            checked = selected.get('avatar_checked_at')
            if isinstance(checked,str): checked=datetime.fromisoformat(checked.replace('Z','+00:00'))
            fresh=checked is not None and checked>datetime.now(timezone.utc)-timedelta(days=7)
            if status == 'unavailable' and fresh:
                raise TenantLinearError('WhatsApp fotosu yoxdur.', 404)
            loading=checked is not None and checked>datetime.now(timezone.utc)-timedelta(minutes=3)
            if status == 'loading' and loading:
                raise TenantLinearError('WhatsApp fotosu yüklənir. Sonra yenidən yoxlayın.', 409)
            if not fresh or status=='loading':
                cur.execute('''UPDATE saas_connect_conversations SET avatar_status='loading',avatar_checked_at=now(),updated_at=now()
                  WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
                    AND (avatar_checked_at IS NULL OR avatar_checked_at<now()-interval '7 days'
                      OR (avatar_status='loading' AND avatar_checked_at<now()-interval '3 minutes'))
                  RETURNING conversation_id''', (tenant, route['channel_id'], route['conversation_id']))
                claimed = cur.fetchone() is not None
                if not claimed:
                    raise TenantLinearError('WhatsApp fotosu yüklənir. Sonra yenidən yoxlayın.', 409)
                conn.commit()
    try:
        body, mime = await _fetch(route)
    except TenantLinearError:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE saas_connect_conversations SET avatar_status='unavailable'
                  WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
                    AND avatar_status='loading' ''',(tenant,route['channel_id'],route['conversation_id']))
        raise
    with _connect() as conn:
        with conn.cursor() as cur:
            # Membership, selected workspace and transport authority may change
            # while the protected image download is in flight.
            current_tenant, _, current_policy = access(cur, profile, 'dialogs')
            current = assert_route(cur, current_tenant, current_policy, 'connect',
                params.get('conversation_id', ''), params.get('channel_id', ''), params.get('lead_id'))
            latest = connect_route(cur, current_tenant, current['lead_id'])
            if (current_tenant != tenant or not latest or
                    any(latest.get(key) != route.get(key) for key in ('channel_id','conversation_id','api_key'))):
                raise TenantLinearError('WhatsApp-kanal artıq əlçatan deyil.', 404)
            cur.execute('''UPDATE saas_connect_conversations SET avatar_status='available',updated_at=now()
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s ''',
              (tenant, route['channel_id'], route['conversation_id']))
    return body, mime
