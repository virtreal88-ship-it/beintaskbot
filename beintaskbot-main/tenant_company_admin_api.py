"""Same-origin owner-only API for managing a user's own companies."""
import asyncio
from aiohttp import web

from tenant_company_admin import list_owned, manage
from tenant_platform import TenantPlatformError
from tenant_request_origin import require_json_post
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, origin: str, logger):
    async def handle(request: web.Request) -> web.Response:
        try:
            profile = member_from_request(request)
            if not profile or profile.get('role') not in {'owner', 'admin'}:
                return web.json_response({'success': False, 'error': 'Şirkət sahibi girişi tələb olunur.'}, status=403)
            if request.method == 'GET':
                if getattr(request, 'query', {}).get('resource') == 'summary_language':
                    from tenant_summary_language import read
                    result = {'summary_language': await asyncio.to_thread(read, profile)}
                else:
                    result = {'companies': await asyncio.to_thread(list_owned, profile)}
            else:
                require_json_post(request, origin)
                data = await request.json()
                if not isinstance(data, dict):
                    return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400)
                if data.get('action') == 'summary_language':
                    from tenant_ai_agents_api import _expected
                    from tenant_summary_language import save
                    _expected(data, profile)
                    payload = {key: value for key, value in data.items()
                               if key not in {'action', 'expected_tenant_id', 'expected_user_id'}}
                    result = {'summary_language': await asyncio.to_thread(save, profile, payload)}
                else:
                    result = await asyncio.to_thread(manage, profile, data)
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TenantLinearError as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=exc.status,
                                     headers={'Cache-Control': 'no-store'})
        except TenantPlatformError as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400, headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Company management failed')
            return web.json_response({'success': False, 'error': 'Şirkətlər yüklənmədi.'}, status=503, headers={'Cache-Control': 'no-store'})
    return handle
