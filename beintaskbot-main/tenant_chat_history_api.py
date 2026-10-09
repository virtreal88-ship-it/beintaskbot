"""Separate cache pagination API; no provider calls or external writes."""
import asyncio
from aiohttp import web
from tenant_chat_history_store import page
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, logger):
    async def handle(request):
        headers = {'Cache-Control':'no-store'}
        try:
            session = member_from_request(request)
            if not session:
                raise TenantLinearError('İcazə yoxdur.', 403)
            if (request.query.get('expected_tenant_id'), request.query.get('expected_user_id')) != (
                    str(session['tenant_id']), str(session['telegram_id'])):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
            result = await asyncio.to_thread(page, session, request.query.get('lead_id'),
                request.query.get('cursor', ''), request.query.get('limit', '120'))
            return web.json_response({'success':True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False, 'error':str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success':False, 'error':'Tarixçə səhifəsi düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant history page unavailable')
            return web.json_response({'success':False, 'error':'Tarixçə yüklənmədi. Yenidən yoxlayın.'}, status=503, headers=headers)
    return handle
