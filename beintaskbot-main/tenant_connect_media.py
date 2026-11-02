"""Read-only, bounded Connect preview. Never calls the AI, webhook or send paths."""
import ipaddress
import asyncio
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import uuid
from urllib.parse import urlencode, urlsplit
import aiohttp
from tenant_connect_transport import CONNECT_URL
from tenant_linear_policy import TenantLinearError

IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
AUDIO_TYPES = {'audio/ogg', 'application/ogg', 'audio/mpeg', 'audio/mp3', 'audio/mp4',
               'audio/opus', 'audio/x-m4a', 'audio/m4a', 'audio/wav', 'audio/x-wav', 'audio/webm', 'audio/flac', 'audio/x-flac', 'audio/aac'}

# All callers (human previews, automatic photos and AI preparation), including
# other event loops in this worker, share Connect's two resolution slots.
_media_lock = threading.Lock()
_media_active = 0
_media_auto_active = 0
_media_budgets = {}
_media_flights = {}
_media_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='connect-media')
_MEDIA_FLIGHT_TIMEOUT = 35


def _media_scope(route):
    return hashlib.sha256(str(route.get('tenant_id') or route.get('api_key') or '').encode()).hexdigest()


def media_backpressure(route):
    with _media_lock:
        now = time.monotonic()
        count, until, cooldown = _media_budgets.get(_media_scope(route), (0, now + 60, 0))
        wait = max(cooldown - now, until - now if count >= 20 else 0, 1 if _media_active >= 2 else 0)
        return max(0, min(3600, int(wait + .999)))


def _media_limited(seconds):
    error = TenantLinearError('Çox sorğu göndərilib. Göstərilən müddətdən sonra yoxlayın.', 429)
    error.retry_after = max(1, min(3600, seconds))
    return error


def _run_media(item, max_bytes, preview_documents):
    # Acquisition outlives every caller's task AND event loop (AI uses asyncio.run).
    async def acquire():
        async with asyncio.timeout(_MEDIA_FLIGHT_TIMEOUT):
            return await _download_media(item, max_bytes=max_bytes, preview_documents=preview_documents)
    try:
        return asyncio.run(acquire())
    except TimeoutError:
        raise TenantLinearError('WhatsApp faylı yüklənmədi. Sonra yenidən yoxlayın.', 502) from None


async def download_media(item, *, max_bytes=None, preview_documents=False, automatic_photo=False):
    global _media_active, _media_auto_active
    item = deepcopy(item)
    route = item['route']; kind = item['row']['message_type']
    raw = item['row'].get('raw') or {}
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    cap = media_limit(kind)
    if max_bytes is not None: cap = min(cap, max(0, int(max_bytes)))
    scope = _media_scope(route)
    authority = hashlib.sha256(str(route.get('api_key') or '').encode()).hexdigest()
    key = (scope, route['channel_id'], route['conversation_id'], item['message_id'], kind, digest,
           cap, bool(preview_documents) if kind == 'document' else False, authority)
    with _media_lock:
        future = _media_flights.get(key)
        owner = future is None
        if owner:
            now = time.monotonic()
            for old, (_count, until, cooldown) in list(_media_budgets.items()):
                if max(until, cooldown) <= now: _media_budgets.pop(old, None)
            count, until, cooldown = _media_budgets.get(scope, (0, now + 60, 0))
            wait = max(cooldown - now, until - now if count >= 20 else 0,
                       1 if _media_active >= 2 or automatic_photo and _media_auto_active >= 1 else 0)
            if wait > 0 or len(_media_budgets) >= 2000 and scope not in _media_budgets:
                raise _media_limited(max(1, int(wait + .999)))
            _media_budgets[scope] = (count + 1, until, cooldown)
            _media_active += 1
            _media_auto_active += bool(automatic_photo)
            try:
                future = _media_executor.submit(_run_media, item, max_bytes, preview_documents)
            except RuntimeError:
                _media_active -= 1; _media_auto_active -= bool(automatic_photo)
                _media_budgets[scope] = (count, until, cooldown)
                raise TenantLinearError('WhatsApp faylı hazırda əlçatan deyil.', 503) from None
            _media_flights[key] = future
    if owner:
        def settled(done):
            global _media_active, _media_auto_active
            error = None if done.cancelled() else done.exception()
            with _media_lock:
                if isinstance(error, TenantLinearError) and error.status == 429:
                    now = time.monotonic()
                    count, until, cooldown = _media_budgets.get(scope, (0, now + 60, 0))
                    _media_budgets[scope] = (count, until, max(cooldown, now + getattr(error, 'retry_after', 60)))
                _media_active -= 1; _media_auto_active -= bool(automatic_photo)
                if _media_flights.get(key) is done: _media_flights.pop(key, None)
        future.add_done_callback(settled)
    waiter = asyncio.wrap_future(future)
    waiter.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    return await asyncio.shield(waiter)


def canonical_audio_mime(content, mime):
    """Named audio aliases only; never guess a format for generic/missing MIME."""
    if mime == 'audio/opus':
        segments = content[26] if len(content) > 26 else 0
        start = 27 + segments
        if (len(content) >= start + 19 and content[:4] == b'OggS' and content[4] == 0
                and content[5] & 2 and segments and content[27] >= 19
                and content[start:start + 8] == b'OpusHead'):
            return 'audio/ogg'
        raise TenantLinearError('Fayl formatı dəstəklənmir.', 415)
    if mime in {'audio/x-m4a', 'audio/m4a'}:
        if (len(content) >= 16 and content[4:8] == b'ftyp'
                and content[8:12] in {b'M4A ', b'M4B ', b'isom', b'iso2', b'mp41', b'mp42'}):
            return 'audio/mp4'
        raise TenantLinearError('Fayl formatı dəstəklənmir.', 415)
    return mime


class PublicResolver(aiohttp.resolver.DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        addresses = await super().resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(address['host']).is_global for address in addresses):
            raise ValueError('Unsafe Connect address')
        return addresses


VIDEO_TYPES={'video/mp4','video/webm'}
DOCUMENT_TYPES={'application/pdf','text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
IMAGE_MAX_BYTES=20*1024*1024
AUDIO_MAX_BYTES=10*1024*1024
VIDEO_MAX_BYTES=100*1024*1024
DOCUMENT_MAX_BYTES=5*1024*1024
# The interactive attachment viewer may safely deliver these original formats;
# AI document extraction supports only DOCUMENT_TYPES above.
DOCUMENT_PREVIEW_TYPES=DOCUMENT_TYPES | {
    'application/msword', 'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/csv', 'text/rtf', 'application/rtf',
}

def media_limit(kind):
    return {
        'image': IMAGE_MAX_BYTES,
        'audio': AUDIO_MAX_BYTES,
        'voice': AUDIO_MAX_BYTES,
        'video': VIDEO_MAX_BYTES,
        'document': DOCUMENT_MAX_BYTES,
    }.get(kind, 0)


def retry_after_seconds(value, default=60):
    """Bound and normalize the upstream Retry-After seconds/date formats."""
    try:
        seconds = int(str(value or '').strip())
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(str(value or ''))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = int((when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            seconds = int(default)
    return max(1, min(3600, seconds))

async def _download_media(item, *, max_bytes=None, preview_documents=False):
    route = dict(item['route'])
    raw=item.get('row',{}).get('raw') or {}
    from tenant_connect_direct_identity import direct_identity
    source=direct_identity(raw.get('_crm_provider_conversation_id')) if isinstance(raw,dict) else ''
    if source and raw.get('conversationId')==route['conversation_id']:
        route['conversation_id']=source
    channel, message = str(uuid.UUID(route['channel_id'])), str(uuid.UUID(item['message_id']))
    origin = urlsplit(CONNECT_URL)
    if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {'', '/'} or origin.port not in {None, 443}):
        raise TenantLinearError('Connect bağlantısını yoxlayın.', 503)
    try:
        literal = ipaddress.ip_address(origin.hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise TenantLinearError('Connect bağlantısını yoxlayın.', 503)
    kind = item['row']['message_type']
    document_types=DOCUMENT_PREVIEW_TYPES if preview_documents else DOCUMENT_TYPES
    cap,types={'image':(IMAGE_MAX_BYTES,IMAGE_TYPES),'audio':(AUDIO_MAX_BYTES,AUDIO_TYPES),'voice':(AUDIO_MAX_BYTES,AUDIO_TYPES),'video':(VIDEO_MAX_BYTES,VIDEO_TYPES),'document':(DOCUMENT_MAX_BYTES,document_types)}.get(kind,(0,set()))
    if max_bytes is not None: cap=min(cap,max(0,int(max_bytes)))
    if not cap: raise TenantLinearError('Fayl formatı dəstəklənmir.',415)
    url = (CONNECT_URL.rstrip('/') + '/v1/channels/' + channel + '/messages/' + message +
           '/media?' + urlencode({'conversationId': route['conversation_id']}))
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver()),
                timeout=aiohttp.ClientTimeout(total=30, connect=5, sock_read=25),
                auto_decompress=False, trust_env=False) as session:
            async with session.get(url, allow_redirects=False, headers={
                    'x-api-key': route['api_key'], 'Accept': ', '.join(sorted(types)),
                    'Accept-Encoding': 'identity'}) as response:
                if response.status in {404, 410}:
                    raise TenantLinearError('Fayl artıq mövcud deyil və ya WhatsApp-dan alına bilmir.', 404)
                if response.status == 413:
                    if response.headers.get('X-Connect-Media-Error') == 'media_duration_limit':
                        error = TenantLinearError('Video 120 saniyədən uzun ola bilməz.', 413)
                        error.code = 'media_duration_limit'
                        raise error
                    raise TenantLinearError('Fayl çox böyükdür: şəkil 20 MB, audio 10 MB, video 100 MB.', 413)
                if response.status == 415:
                    raise TenantLinearError('Fayl formatı dəstəklənmir və ya fayl zədələnib.', 415)
                if response.status == 429:
                    error = TenantLinearError('Çox sorğu göndərilib. Göstərilən müddətdən sonra yoxlayın.', 429)
                    error.retry_after = retry_after_seconds(response.headers.get('Retry-After'))
                    raise error
                if response.status in {401,403}:
                    error = TenantLinearError('WhatsApp faylı hazırda əlçatan deyil. Sonra yenidən yoxlayın.', 502)
                    error.code = 'connect_access_denied'
                    raise error
                if response.status != 200:
                    raise TenantLinearError('WhatsApp faylı hazırda əlçatan deyil. Sonra yenidən yoxlayın.', 502)
                if (response.headers.get('X-Connect-Channel-Id') != channel or
                        response.headers.get('X-Connect-Message-Id') != message or
                        response.headers.get('Content-Encoding', 'identity') != 'identity'):
                    raise ValueError('Invalid media response')
                mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if mime not in types:
                    raise TenantLinearError('Fayl formatı dəstəklənmir.', 415)
                length = response.headers.get('Content-Length')
                if length and not 0 < int(length) <= cap:
                    raise TenantLinearError('Fayl çox böyükdür: şəkil 20 MB, audio 10 MB, video 100 MB.', 413)
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > cap:
                        raise TenantLinearError('Fayl çox böyükdür: şəkil 20 MB, audio 10 MB, video 100 MB.', 413)
                    chunks.append(chunk)
                if not size or length and size != int(length):
                    raise ValueError('Incomplete media')
                content = b''.join(chunks)
                if kind in {'audio', 'voice'}: mime = canonical_audio_mime(content, mime)
                return content, mime
    except TenantLinearError:
        raise
    except Exception:
        raise TenantLinearError('WhatsApp faylı yüklənmədi. Sonra yenidən yoxlayın.', 502) from None
