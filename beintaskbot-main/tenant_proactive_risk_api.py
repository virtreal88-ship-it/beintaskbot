"""Owner view of risk findings and acknowledged, source-revalidated alerts."""
import asyncio
from aiohttp import web
from tenant_linear_policy import TenantLinearError
from tenant_proactive_risk_store import alert_detail, list_findings


def handler(member_from_request, logger):
    async def handle(request):
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            result = await asyncio.to_thread(list_findings, profile)
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status,
                                     headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.error('Tenant proactive risk preview unavailable')
            return web.json_response({'success': False, 'error': 'Risk yoxlaması əlçatan deyil.'}, status=503,
                                     headers={'Cache-Control': 'no-store'})
    return handle


def alert_handler(member_from_request, logger):
    async def handle(request):
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            result = await asyncio.to_thread(alert_detail, profile, request.match_info['alert_id'])
            return web.json_response({'success': True, 'alert': result}, headers={'Cache-Control': 'no-store'})
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status,
                                     headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.error('Tenant proactive risk detail unavailable')
            return web.json_response({'success': False, 'error': 'Bildiriş müvəqqəti əlçatan deyil.'}, status=503,
                                     headers={'Cache-Control': 'no-store'})
    return handle
