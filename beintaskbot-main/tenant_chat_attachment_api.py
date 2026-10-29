"""Bounded read-only attachment endpoint; fresh authority before returning bytes."""
import asyncio
import hashlib
import json
import mimetypes
import time
from collections import OrderedDict
from urllib.parse import quote
from aiohttp import web
from tenant_chat_attachment_store import load, token
from tenant_chat_attachment_provider import download
from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session

SLOTS = asyncio.Semaphore(3)
REQUESTS = OrderedDict()
MEDIA_CACHE = OrderedDict()
MEDIA_CACHE_BYTES = 0
MEDIA_INFLIGHT = {}
MEDIA_CACHE_LIMIT = 32 * 1024 * 1024
MEDIA_CACHE_TTL = 300
MEDIA_PENDING_LIMIT = 12


def _media_name(raw, mime=''):
    stack, seen = [raw], 0
    while stack and seen < 120:
        item = stack.pop(); seen += 1
        if isinstance(item, dict):
            for key in ('fileName', 'filename', 'file_name', 'documentName', 'originalName'):
                value = item.get(key)
                if isinstance(value, str) and value.strip() and not any(ord(c) < 32 for c in value):
                    name = value.strip().replace('\\', '/').rsplit('/', 1)[-1]
                    if name and name not in {'.', '..'}:
                        return name[:160]
            stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
        elif isinstance(item, list):
            stack.extend(value for value in item if isinstance(value, (dict, list)))
    extension = mimetypes.guess_extension(str(mime or '').split(';', 1)[0].strip().lower()) or ''
    return 'attachment' + extension


def _media_key(session, item, lead, message):
    route = item.get('route') or {}
    row = item.get('row') or {}
    raw = row.get('raw') if isinstance(row.get('raw'), (dict, list)) else {}
    # Do not place connection credentials or provider URLs in cache keys.
    material = json.dumps(raw, sort_keys=True, default=str, separators=(',', ':'))
    digest = hashlib.sha256(material.encode('utf-8')).hexdigest()
    return (str(session['tenant_id']), str(lead), str(message), str(item.get('provider') or ''),
            str(route.get('channel_id') or ''), str(route.get('conversation_id') or ''), digest)


def _cache_get(key):
    global MEDIA_CACHE_BYTES
    now = time.monotonic()
    for old_key, (_expires, data, _mime, _name) in list(MEDIA_CACHE.items()):
        if _expires <= now:
            MEDIA_CACHE.pop(old_key, None); MEDIA_CACHE_BYTES -= len(data)
    value = MEDIA_CACHE.get(key)
    if value is None:
        return None
    MEDIA_CACHE.move_to_end(key)
    return value[1:]


def _cache_put(key, result):
    global MEDIA_CACHE_BYTES
    data, _mime, _name = result
    if not data or len(data) > MEDIA_CACHE_LIMIT:
        return
    previous = MEDIA_CACHE.pop(key, None)
    if previous:
        MEDIA_CACHE_BYTES -= len(previous[1])
    while MEDIA_CACHE_BYTES + len(data) > MEDIA_CACHE_LIMIT and MEDIA_CACHE:
        _old_key, (_expires, old_data, _old_mime, _old_name) = MEDIA_CACHE.popitem(last=False)
        MEDIA_CACHE_BYTES -= len(old_data)
    MEDIA_CACHE[key] = (time.monotonic() + MEDIA_CACHE_TTL, *result)
    MEDIA_CACHE_BYTES += len(data)


def _content_disposition(filename, *, inline=False):
    safe = ''.join(c for c in filename if ord(c) >= 32 and c not in '\r\n"\\')[:120] or 'attachment'
    fallback = ''.join(c if 32 <= ord(c) < 127 else '_' for c in safe) or 'attachment'
    disposition = 'inline' if inline else 'attachment'
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{quote(safe, safe=".-_")}'


def _byte_range(value, length):
    if not value:
        return None
    import re
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', str(value).strip())
    if not match or not length:
        return False
    first, last = match.groups()
    if not first and not last:
        return False
    if not first:
        count = int(last)
        if count <= 0:
            return False
        return max(0, length - count), length - 1
    start = int(first)
    end = min(length - 1, int(last)) if last else length - 1
    return (start, end) if start < length and end >= start else False


def admit(session):
    now = time.monotonic()
    for key in list(REQUESTS):
        if REQUESTS[key][1] <= now:
            del REQUESTS[key]
    keys = [(str(session['tenant_id']), str(session['telegram_id'])), (str(session['tenant_id']), '*')]
    blocked = [(key, cap) for key, cap in zip(keys, (20, 60))
               if REQUESTS.get(key, (0, 0))[0] >= cap]
    if len(REQUESTS) > 2000 or blocked:
        wait = max((REQUESTS[key][1] - now for key, _cap in blocked), default=60)
        error = TenantLinearError('Çox sorğu göndərilib. Göstərilən müddətdən sonra yoxlayın.', 429)
        error.retry_after = max(1, min(3600, int(wait + 0.999)))
        raise error
    for key in keys:
        count, until = REQUESTS.get(key, (0, now + 60))
        REQUESTS[key] = (count + 1, until)


def _check_case_attachment(session, item, query):
    keys=('case_id','case_message_id','case_version')
    if not any(query.get(k) for k in keys):return
    if not all(query.get(k) for k in keys):raise TenantLinearError('Некорректная ссылка на вложение.',400)
    from tenant_knowledge_candidates_store import conversation_link
    version=int(query['case_version'])
    resolved=conversation_link(session,query['case_id'],query['case_message_id'],expected_version=version)
    route=item.get('route') or {};selected=resolved['route']
    if (str((item.get('row') or {}).get('id'))!=resolved['message_id']
        or str(route.get('channel_id'))!=str(selected['channel_id'])
        or str(route.get('conversation_id'))!=str(selected['conversation_id'])
        or str(query.get('lead_id'))!=str(selected['lead_id'])):
        raise TenantLinearError('Вложение недоступно в этом кейсе.',403)


def handler(member_from_request, request_provider, logger):
    async def handle(request):
        global MEDIA_CACHE_BYTES
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'Content-Security-Policy': "default-src 'none'; sandbox", 'Referrer-Policy': 'no-referrer'}
        try:
            session = require_read_session(request, member_from_request)
            lead, message = request.query.get('lead_id'), request.query.get('message_id')
            scope={'channel_id':request.query.get('channel_id') or None,
                   'conversation_id':request.query.get('conversation_id') or None}
            item = await asyncio.to_thread(load, session, lead, message, **scope)
            await asyncio.to_thread(_check_case_attachment,session,item,request.query)
            key = _media_key(session, item, lead, message)
            result = _cache_get(key)
            if result is None:
                task = MEDIA_INFLIGHT.get(key)
                if task is None:
                    if len(MEDIA_INFLIGHT) >= MEDIA_PENDING_LIMIT:
                        error = TenantLinearError('Fayllar yüklənir. Göstərilən müddətdən sonra yenidən yoxlayın.', 429)
                        error.retry_after = 5
                        raise error
                    admit(session)

                    async def fetch_and_verify():
                        async with SLOTS:
                            if item.get('provider') == 'connect':
                                from tenant_connect_media import download_media
                                data, content_type = await download_media(item, preview_documents=True)
                            else:
                                async with asyncio.timeout(15):
                                    account = await request_provider(session['tenant_id'], 'GET', 'account', params={'with': 'drive_url'})
                                bearer = await asyncio.to_thread(token, item)
                                data, content_type = await asyncio.to_thread(download, item['row'], account.get('drive_url'), bearer)
                            filename = _media_name((item.get('row') or {}).get('raw'), content_type)
                            return data, content_type, filename

                    task = asyncio.create_task(fetch_and_verify())
                    MEDIA_INFLIGHT[key] = task
                try:
                    result = await task
                    _cache_put(key, result)
                finally:
                    if MEDIA_INFLIGHT.get(key) is task and task.done():
                        MEDIA_INFLIGHT.pop(key, None)
            # Recheck authorization and tenant/channel/message ownership for every request,
            # including cache hits and concurrent waiters.
            current = await asyncio.to_thread(load, session, lead, message, **scope)
            if current != item:
                raise TenantLinearError('Çat və ya bağlantı dəyişib. Yeniləyin.', 409)
            await asyncio.to_thread(_check_case_attachment,session,current,request.query)
            content, mime, filename = result
            inline = request.query.get('download')!='1' and (mime.startswith(('image/', 'audio/', 'video/')) or mime in {'application/pdf', 'text/plain'})
            headers['Content-Disposition'] = _content_disposition(filename, inline=inline)
            headers['Accept-Ranges'] = 'bytes'
            requested_range = _byte_range(getattr(request, 'headers', {}).get('Range'), len(content))
            if requested_range is False:
                headers['Content-Range'] = f'bytes */{len(content)}'
                return web.Response(status=416, headers=headers)
            if requested_range is not None:
                start, end = requested_range
                headers['Content-Range'] = f'bytes {start}-{end}/{len(content)}'
                headers['Content-Length'] = str(end - start + 1)
                return web.Response(status=206, body=content[start:end + 1], content_type=mime, headers=headers)
            headers['Content-Length'] = str(len(content))
            return web.Response(body=content, content_type=mime, headers=headers)
        except TenantLinearError as error:
            if error.status == 429:
                headers['Retry-After'] = str(getattr(error, 'retry_after', 60))
            payload = {'success': False, 'error': str(error)}
            if getattr(error, 'code', None):
                payload['code'] = error.code
            return web.json_response(payload, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Fayl yüklənmədi. Fayl icazəsini və faylın mövcudluğunu yoxlayın.'}, status=400, headers=headers)
        except Exception:
            logger.warning('Tenant attachment unavailable')
            return web.json_response({'success': False, 'error': 'Fayl yüklənmədi. Yenidən yoxlayın.'}, status=503, headers=headers)
    return handle
