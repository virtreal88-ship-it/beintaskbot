"""Same-origin dialog reminder API."""
import asyncio
import json

from aiohttp import web

from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_personal_dialog_reminders import SessionUnavailable, configured, enabled, execute


def _check_identity(data, profile):
    try:
        _expected(data, profile)
    except TenantLinearError as error:
        raise SessionUnavailable(str(error), error.status) from error


def handler(member_from_request, origin, logger, *, shared=False):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        gates = (configured, enabled, execute)
        if shared:
            import tenant_shared_dialog_reminders as team
            gates = (team.configured, team.enabled, team.execute)
        available, workspace_enabled, run = gates
        try:
            if not available():
                raise TenantLinearError('Напоминания недоступны.', 404)
            profile = member_from_request(request)
            if not profile:
                raise SessionUnavailable('Нет доступа.', 403)
            if not workspace_enabled(profile.get('tenant_id')):
                raise TenantLinearError('Напоминания недоступны.', 404)
            if request.method == 'GET':
                _check_identity(request.query, profile)
                result = await asyncio.to_thread(run, profile)
            else:
                _origin(request, origin)
                if request.content_type != 'application/json':
                    raise TenantLinearError('Ожидается JSON-запрос.', 415)
                if request.content_length is not None and request.content_length > 4096:
                    raise TenantLinearError('Некорректный размер запроса.', 413)
                try:
                    await request.content.readexactly(4097)
                except asyncio.IncompleteReadError as end:
                    raw = end.partial
                else:
                    raise TenantLinearError('Некорректный размер запроса.', 413)
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('invalid request')
                _check_identity(data, profile)
                payload = {key: value for key, value in data.items()
                           if key not in {'expected_tenant_id','expected_user_id'}}
                result = await asyncio.to_thread(run, profile, payload)
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as error:
            body = {'success': False, 'error': str(error)}
            if isinstance(error, SessionUnavailable):
                body['error_code'] = 'reminder_session_unavailable'
            return web.json_response(body,
                                     status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError, OverflowError):
            return web.json_response({'success': False, 'error': 'Некорректный запрос.'},
                                     status=400, headers=headers)
        except Exception as error:
            logger.warning('Dialog reminder unavailable (%s)', type(error).__name__)
            return web.json_response({'success': False, 'error': 'Напоминания временно недоступны.'},
                                     status=503, headers=headers)
    return handle
