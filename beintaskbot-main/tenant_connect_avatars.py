"""Protected, bounded WhatsApp avatar cache retrieval through Connect."""
import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import aiohttp
import psycopg

from tenant_connect_transport import CONNECT_URL, connect_route
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_connect_media import PublicResolver, IMAGE_TYPES
from tenant_platform import _connect as _platform_connect

MAX_AVATAR_BYTES = 1024 * 1024
_AVATAR_DB_WORKERS = 4
_AVATAR_DB_DEADLINE = 5
_AVATAR_DB_POOL = ThreadPoolExecutor(max_workers=_AVATAR_DB_WORKERS, thread_name_prefix='avatar-db')
_AVATAR_DB_SLOTS = threading.BoundedSemaphore(_AVATAR_DB_WORKERS)


def _connect():
    return _platform_connect(connect_timeout=3, application_name='crm-avatar', options='-c lock_timeout=1500 -c statement_timeout=3000')


def _avatar_db_unavailable():
    return TenantLinearError('WhatsApp fotosu hazırda yüklənmədi.', 503)


async def _avatar_db(operation, *args):
    # Optional avatar loads must not queue behind one another or consume
    # threads used by the rest of the HTTP app.
    slots = _AVATAR_DB_SLOTS
    if not slots.acquire(blocking=False):
        raise _avatar_db_unavailable()
    try:
        work = _AVATAR_DB_POOL.submit(operation, *args)
    except BaseException:
        slots.release()
        raise
    work.add_done_callback(lambda _: slots.release())
    try:
        return await asyncio.wait_for(asyncio.wrap_future(work), timeout=_AVATAR_DB_DEADLINE)
    except (TimeoutError, psycopg.OperationalError, psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable,
            psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        raise _avatar_db_unavailable() from None


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
    if route.get('native'):
        return await _fetch_native(route)
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


def _prepare_avatar(profile, params):
    if params.get('provider') != 'connect':
        raise TenantLinearError('Bu dialoq üçün WhatsApp fotosu yoxdur.', 404)
    with _connect() as conn:
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
    return tenant, route


def _cleanup_avatar(tenant, route, missing):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_connect_conversations SET avatar_status=%s,
                avatar_checked_at=CASE WHEN %s THEN avatar_checked_at ELSE NULL END
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
                AND avatar_status='loading' ''',('unavailable' if missing else '',missing,tenant,route['channel_id'],route['conversation_id']))


def _complete_avatar(profile, params, tenant, route):
    with _connect() as conn:
        with conn.cursor() as cur:
            # Membership, selected workspace and transport authority may change
            # while the protected image download is in flight.
            current_tenant, _, current_policy = access(cur, profile, 'dialogs')
            current = assert_route(cur, current_tenant, current_policy, 'connect',
                params.get('conversation_id', ''), params.get('channel_id', ''), params.get('lead_id'))
            latest = connect_route(cur, current_tenant, current['lead_id'])
            if (current_tenant != tenant or not latest or
                    any(latest.get(key) != route.get(key) for key in ('channel_id','conversation_id','api_key','native'))):
                raise TenantLinearError('WhatsApp-kanal artıq əlçatan deyil.', 404)
            cur.execute('''UPDATE saas_connect_conversations SET avatar_status='available',updated_at=now()
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
                AND avatar_status IS DISTINCT FROM 'available' ''',
              (tenant, route['channel_id'], route['conversation_id']))


_avatar_requests = threading.BoundedSemaphore(6)


async def get_avatar(profile, params):
    if not _avatar_requests.acquire(blocking=False):
        raise _avatar_db_unavailable()
    try:
        return await _load_avatar(profile, params)
    finally:
        _avatar_requests.release()


async def _load_avatar(profile, params):
    tenant, route = await _avatar_db(_prepare_avatar, profile, params)
    try:
        body, mime = await _fetch(route)
    except TenantLinearError as error:
        try:
            await _avatar_db(_cleanup_avatar, tenant, route, error.status == 404)
        except TenantLinearError:
            # The existing short cache-claim expiry lets a later request retry.
            pass
        raise
    await _avatar_db(_complete_avatar, profile, params, tenant, route)
    return body, mime


async def _fetch_native(route):
    from tenant_call_sources import service
    url,token=service()
    binding=route['native']
    payload={'scope':binding['scope'],'chat':route['conversation_id']}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15,connect=4),
                auto_decompress=False,trust_env=False) as session:
            async with session.post(url+'/v1/sources/'+binding['gateway_id']+'/messages/avatar',
                    json=payload,headers={'Authorization':'Bearer '+token,'Accept-Encoding':'identity'},
                    allow_redirects=False) as response:
                if response.status==404:raise TenantLinearError('WhatsApp fotosu mövcud deyil.',404)
                if response.status!=200 or response.headers.get('Content-Encoding','identity')!='identity':raise ValueError()
                chunks=[];size=0
                async for chunk in response.content.iter_chunked(32768):
                    size+=len(chunk)
                    if size>MAX_AVATAR_BYTES:raise ValueError()
                    chunks.append(chunk)
                body=b''.join(chunks);mime=_image_mime(body)
                if not mime or mime not in IMAGE_TYPES:raise ValueError()
                return body,mime
    except TenantLinearError:raise
    except Exception:raise TenantLinearError('WhatsApp fotosu hazırda yüklənmədi.',502) from None

