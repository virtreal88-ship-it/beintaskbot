"""Authenticated read-only HTTP surface for Business Analyst tools."""
import asyncio
import logging
from aiohttp import web

from tenant_ai_business_analytics import execute
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, canonical_origin, logger=None):
    log = logger or logging.getLogger(__name__)

    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            if request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)
            if request.content_type != 'application/json':
                raise TenantLinearError('Invalid analytics request.', 400)
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            data = await request.json()
            required = {'expected_tenant_id', 'expected_user_id', 'tool', 'arguments'}
            if not isinstance(data, dict) or set(data) != required:
                raise TenantLinearError('Invalid analytics request.', 400)
            if (str(data['expected_tenant_id'] or '') != str(profile.get('tenant_id'))
                    or str(data['expected_user_id'] or '') != str(profile.get('telegram_id'))):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
            if not isinstance(data['tool'], str) or len(data['tool']) > 80:
                raise TenantLinearError('Invalid analytics request.', 400)
            result = await asyncio.to_thread(execute, profile, data['tool'], data['arguments'])
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except Exception:
            log.error('Business analytics read failed')
            return web.json_response({'success': False, 'error': 'Business analytics is temporarily unavailable.'},
                                     status=503, headers=headers)

    return handle
