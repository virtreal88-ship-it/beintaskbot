"""Read-only owner diagnostic response with safe correlation ID."""
import asyncio
import uuid
from aiohttp import web
from tenant_diagnostics import snapshot
from tenant_read_session import require_read_session
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, logger):
    async def handle(request):
        request_id = str(uuid.uuid4())
        headers = {'Cache-Control': 'no-store', 'X-Request-ID': request_id}
        try:
            session = require_read_session(request, member_from_request)
            result = await asyncio.to_thread(snapshot, session)
            return web.json_response({'success': True, 'request_id': request_id, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error), 'request_id': request_id},
                                     status=error.status, headers=headers)
        except Exception:
            logger.error('Tenant diagnostics unavailable request=%s', request_id)
            return web.json_response({'success': False, 'error': 'Diaqnostika yüklənmədi.', 'request_id': request_id},
                                     status=503, headers=headers)
    return handle
