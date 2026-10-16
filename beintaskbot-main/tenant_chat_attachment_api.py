"""Bounded read-only attachment endpoint; fresh authority before returning bytes."""
import asyncio
import time
from collections import OrderedDict
from aiohttp import web
from tenant_chat_attachment_store import load, token
from tenant_chat_attachment_provider import download
from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session

SLOTS = asyncio.Semaphore(3)
REQUESTS = OrderedDict()


def admit(session):
    now = time.monotonic()
    for key in list(REQUESTS):
        if REQUESTS[key][1] <= now:
            del REQUESTS[key]
    keys = [(str(session['tenant_id']), str(session['telegram_id'])), (str(session['tenant_id']), '*')]
    if len(REQUESTS) > 2000 or any(REQUESTS.get(key, (0, 0))[0] >= cap for key, cap in zip(keys, (20, 60))):
        raise TenantLinearError('Çox sorğu göndərilib. Bir dəqiqə sonra yoxlayın.', 429)
    for key in keys:
        count, until = REQUESTS.get(key, (0, now + 60))
        REQUESTS[key] = (count + 1, until)


def handler(member_from_request, request_provider, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'Content-Security-Policy': "default-src 'none'; sandbox", 'Referrer-Policy': 'no-referrer'}
        try:
            session = require_read_session(request, member_from_request)
            admit(session)
            if SLOTS.locked():
                raise TenantLinearError('Fayllar yüklənir. Bir az sonra yoxlayın.', 429)
            async with SLOTS:
                lead, message = request.query.get('lead_id'), request.query.get('message_id')
                item = await asyncio.to_thread(load, session, lead, message)
                if item.get('provider') == 'connect':
                    from tenant_connect_media import download_media
                    content, mime = await download_media(item)
                else:
                    async with asyncio.timeout(15):
                        account = await request_provider(session['tenant_id'], 'GET', 'account', params={'with': 'drive_url'})
                    bearer = await asyncio.to_thread(token, item)
                    content, mime = await asyncio.to_thread(download, item['row'], account.get('drive_url'), bearer)
                current = await asyncio.to_thread(load, session, lead, message)
                if current != item:
                    raise TenantLinearError('Çat və ya bağlantı dəyişib. Yeniləyin.', 409)
            headers['Content-Disposition'] = 'attachment; filename="attachment"'
            return web.Response(body=content, content_type=mime, headers=headers)
        except TenantLinearError as error:
            if error.status == 429:
                headers['Retry-After'] = '60'
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Fayl yüklənmədi. Fayl icazəsini və faylın mövcudluğunu yoxlayın.'}, status=400, headers=headers)
        except Exception:
            logger.warning('Tenant attachment unavailable')
            return web.json_response({'success': False, 'error': 'Fayl yüklənmədi. Yenidən yoxlayın.'}, status=503, headers=headers)
    return handle
