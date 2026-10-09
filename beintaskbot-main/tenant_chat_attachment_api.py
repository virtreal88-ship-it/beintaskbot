"""Bounded read-only attachment endpoint; fresh authority before returning bytes."""
import asyncio
from aiohttp import web
from tenant_chat_attachment_store import load, token
from tenant_chat_attachment_provider import download
from tenant_linear_policy import TenantLinearError

SLOTS = asyncio.Semaphore(3)


def handler(member_from_request, request_provider, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'Content-Security-Policy': "default-src 'none'; sandbox", 'Referrer-Policy': 'no-referrer'}
        try:
            session = member_from_request(request)
            if not session:
                raise TenantLinearError('İcazə yoxdur.', 403)
            if (request.query.get('expected_tenant_id'), request.query.get('expected_user_id')) != (
                    str(session['tenant_id']), str(session['telegram_id'])):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
            if SLOTS.locked():
                raise TenantLinearError('Fayllar yüklənir. Bir az sonra yoxlayın.', 429)
            async with SLOTS:
                lead, message = request.query.get('lead_id'), request.query.get('message_id')
                item = await asyncio.to_thread(load, session, lead, message)
                async with asyncio.timeout(15):
                    account = await request_provider(session['tenant_id'], 'GET', 'account', params={'with': 'drive_url'})
                bearer = await asyncio.to_thread(token, item)
                content, mime = await asyncio.to_thread(download, item['row'], account.get('drive_url'), bearer)
                current = await asyncio.to_thread(load, session, lead, message)
                if current != item:
                    raise TenantLinearError('Çat və ya Kommo bağlantısı dəyişib. Yeniləyin.', 409)
            headers['Content-Disposition'] = 'attachment; filename="attachment"'
            return web.Response(body=content, content_type=mime, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Fayl yüklənmədi. Kommo fayl icazəsini və faylın mövcudluğunu yoxlayın (maksimum 20 MB).'}, status=400, headers=headers)
        except Exception:
            logger.warning('Tenant attachment unavailable')
            return web.json_response({'success': False, 'error': 'Fayl yüklənmədi. Yenidən yoxlayın.'}, status=503, headers=headers)
    return handle
