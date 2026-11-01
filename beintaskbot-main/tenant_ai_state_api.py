"""Identity-bound, GET-only catalogue/state APIs. Errors never echo provider data."""
import asyncio
from aiohttp import web
from tenant_ai_channel_catalog import catalog
from tenant_ai_state import snapshot
from tenant_read_session import require_read_session
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, logger, resource='state'):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            if request.method not in {'GET', 'HEAD'}:
                return web.json_response({'success': False, 'error_code': 'method_not_allowed'}, status=405, headers=headers)
            profile = require_read_session(request, member_from_request)
            raw_limit = request.query.get('limit', '30')
            if not str(raw_limit).isascii() or not str(raw_limit).isdigit() or len(str(raw_limit)) > 3:
                return web.json_response({'success': False, 'error_code': 'invalid_limit'}, status=400, headers=headers)
            limit = max(1, min(int(raw_limit), 100))
            if resource=='technical' and any(key in request.query for key in ('channel_id','lead_id','request_id')):
                from tenant_manual_ai_run_diagnostics import read
                result=await asyncio.to_thread(read,profile,dict(request.query))
            elif resource == 'channels':
                result = await asyncio.to_thread(catalog, profile)
            else:
                result = await asyncio.to_thread(snapshot, profile, limit, technical=resource == 'technical')
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as error:
            code = 'identity_changed' if error.status == 409 else 'access_denied'
            return web.json_response({'success': False, 'error_code': code}, status=error.status, headers=headers)
        except Exception:
            logger.warning('AI state read unavailable')
            return web.json_response({'success': False, 'error_code': 'ai_state_unavailable'}, status=503, headers=headers)
    return handle
