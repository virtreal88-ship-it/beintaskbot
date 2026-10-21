"""Read-only, bounded Connect preview. Never calls the AI, webhook or send paths."""
import ipaddress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import uuid
from urllib.parse import urlencode, urlsplit
import aiohttp
from tenant_connect_transport import CONNECT_URL
from tenant_linear_policy import TenantLinearError

IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
AUDIO_TYPES = {'audio/ogg', 'application/ogg', 'audio/mpeg', 'audio/mp3', 'audio/mp4',
               'audio/wav', 'audio/x-wav', 'audio/webm', 'audio/flac', 'audio/x-flac', 'audio/aac'}


class PublicResolver(aiohttp.resolver.DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        addresses = await super().resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(address['host']).is_global for address in addresses):
            raise ValueError('Unsafe Connect address')
        return addresses


VIDEO_TYPES={'video/mp4','video/webm'}
DOCUMENT_TYPES={'application/pdf','text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
# The interactive attachment viewer may safely deliver these original formats;
# AI document extraction intentionally remains limited to DOCUMENT_TYPES above.
DOCUMENT_PREVIEW_TYPES=DOCUMENT_TYPES | {
    'application/msword', 'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/csv', 'text/rtf', 'application/rtf',
}


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

async def download_media(item, *, max_bytes=None, preview_documents=False):
    route = item['route']
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
    cap,types={'image':(8*1024*1024,IMAGE_TYPES),'audio':(10*1024*1024,AUDIO_TYPES),'voice':(10*1024*1024,AUDIO_TYPES),'video':(16*1024*1024,VIDEO_TYPES),'document':(5*1024*1024,document_types)}.get(kind,(0,set()))
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
                    raise TenantLinearError('Fayl çox böyükdür: şəkil 8 MB, audio 10 MB.', 413)
                if response.status == 415:
                    raise TenantLinearError('Fayl formatı dəstəklənmir və ya fayl zədələnib.', 415)
                if response.status == 429:
                    error = TenantLinearError('Çox sorğu göndərilib. Göstərilən müddətdən sonra yoxlayın.', 429)
                    error.retry_after = retry_after_seconds(response.headers.get('Retry-After'))
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
                    raise TenantLinearError('Fayl çox böyükdür.', 413)
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > cap:
                        raise TenantLinearError('Fayl çox böyükdür.', 413)
                    chunks.append(chunk)
                if not size or length and size != int(length):
                    raise ValueError('Incomplete media')
                return b''.join(chunks), mime
    except TenantLinearError:
        raise
    except Exception:
        raise TenantLinearError('WhatsApp faylı yüklənmədi. Sonra yenidən yoxlayın.', 502) from None
