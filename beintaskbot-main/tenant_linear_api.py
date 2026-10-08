"""Owner-only same-origin configuration boundary, not a publication endpoint."""
import asyncio
from aiohttp import web
from tenant_linear import read, choices, command
from tenant_linear_policy import owner, TenantLinearError


def linear_handler(member_from_request, canonical_origin, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member_from_request(request)
            owner(profile)
            if request.method == 'GET':
                query = request.rel_url.query
                result = await asyncio.to_thread(choices, profile, query.get('team_id', ''), query.get('after')) if query.get('catalog') == '1' else await asyncio.to_thread(read, profile)
            else:
                if request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
                    raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantLinearError('Sorğu düzgün deyil.')
                if str(data.get('expected_tenant_id') or '') != str(profile['tenant_id']) or str(data.get('expected_user_id') or '') != str(profile['telegram_id']):
                    raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
                result = await asyncio.to_thread(command, profile, data)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant Linear configuration failed')
            return web.json_response({'success': False, 'error': 'Linear ayarları əlçatan deyil.'}, status=503, headers=headers)
    return handle
