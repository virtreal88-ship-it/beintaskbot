"""Authenticated same-origin APIs, expected company/user checked before provider I/O."""
import asyncio
from aiohttp import web
from tenant_chat_ai_settings import read, save
from tenant_chat_ai_service import run
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post


def handler(member_from_request, canonical_origin, logger, *, settings=False):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            if request.method == 'GET' and settings:
                result = await asyncio.to_thread(read, profile)
            else:
                require_json_post(request, canonical_origin)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantLinearError('Sorğu düzgün deyil.')
                if str(data.get('expected_tenant_id') or '') != str(profile['tenant_id']) or str(data.get('expected_user_id') or '') != str(profile['telegram_id']):
                    raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
                result = await asyncio.to_thread(save, profile, data) if settings else await run(profile, data, logger)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error), 'keep_request': getattr(error, 'keep_request', False)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant chat AI service unavailable')
            return web.json_response({'success': False, 'error': 'AI xidməti əlçatan deyil.'}, status=503, headers=headers)
    return handle
