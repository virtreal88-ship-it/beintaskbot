"""Separate cache pagination API; no provider calls or external writes."""
import asyncio
from aiohttp import web
from tenant_chat_history_store import page
from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session


def handler(member_from_request, logger):
    async def handle(request):
        headers = {'Cache-Control':'no-store'}
        try:
            session = require_read_session(request, member_from_request)
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
