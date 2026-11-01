"""Same-origin personal notification list and explicit read action."""
import asyncio
import json
from aiohttp import web

from tenant_ai_agents_api import _origin, _expected
from tenant_linear_policy import TenantLinearError
import tenant_notification_center as center


def handler(member, origin, logger, resource='list'):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = member(request)
            if not profile:
                raise TenantLinearError('Нет доступа.', 403)
            if request.method == 'GET' and resource == 'list':
                if 'X-Tenant-Id' in request.headers or 'X-User-Id' in request.headers:
                    _expected({'expected_tenant_id': request.headers.get('X-Tenant-Id'),
                               'expected_user_id': request.headers.get('X-User-Id')}, profile)
                result = await asyncio.to_thread(center.listing, profile,
                    request.query.get('status', 'unread'), request.query.get('limit', 20), request.query.get('before', ''))
            elif request.method == 'POST' and resource == 'read':
                _origin(request, origin)
                if request.content_type != 'application/json':
                    raise TenantLinearError('Ожидается JSON.', 415)
                raw = await request.read()
                if len(raw) > 2000:
                    raise TenantLinearError('Слишком большой запрос.', 413)
                data = json.loads(raw)
                if not isinstance(data, dict) or set(data) != {'expected_tenant_id', 'expected_user_id'}:
                    raise TenantLinearError('Некорректный запрос.', 400)
                _expected(data, profile)
                result = await asyncio.to_thread(center.mark_read, profile, request.match_info['notification_id'])
            else:
                raise TenantLinearError('Метод не поддерживается.', 405)
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=exc.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Некорректный запрос.'}, status=400, headers=headers)
        except Exception:
            logger.exception('Personal notification center failed')
            return web.json_response({'success': False, 'error': 'Уведомления временно недоступны.'}, status=503, headers=headers)
    return handle
