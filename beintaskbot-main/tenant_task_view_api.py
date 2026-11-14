"""Authenticated, same-origin boundary for personal task-source preferences."""
import asyncio
from dataclasses import dataclass
from collections.abc import Callable
from aiohttp import web
from tenant_task_flow import query, TaskFlowError


@dataclass(frozen=True)
class TaskViewContext:
    member: Callable
    preferences: Callable
    origin: str
    platform_error: type[Exception]
    invalid_message: str = 'Tapşırıq mənbəyi düzgün deyil.'
    failure_message: str = 'Tapşırıq görünüşü yüklənmədi və ya saxlanmadı.'


def handler(context: TaskViewContext):
    def response(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    async def handle(request):
        if request.method not in {'GET', 'POST'}:
            return response({'success': False, 'error': 'Method not allowed'}, 405)
        profile = context.member(request)
        if not profile or profile.get('active') is not True:
            return response({'success': False, 'error': 'Platforma girişi tələb olunur.'}, 401)
        try:
            query(request, profile, required=True)
        except TaskFlowError as exc:
            return response({'success': False, 'error': exc.code}, exc.status)
        source = None
        if request.method == 'POST':
            if request.headers.get('Origin', '').rstrip('/') != context.origin.rstrip('/'):
                return response({'success': False, 'error': 'Sorğunun mənbəyi düzgün deyil.'}, 403)
            try:
                data = await request.json()
                if (not isinstance(data, dict) or set(data) != {'source'}
                        or not isinstance(data['source'], str) or data['source'] not in ('native', 'kommo')):
                    raise ValueError()
                source = data['source']
            except Exception:
                return response({'success': False, 'error': context.invalid_message}, 400)
        try:
            result = await asyncio.to_thread(context.preferences, tenant_id=str(profile['tenant_id']),
                                            actor_id=int(profile['telegram_id']), source=source)
            return response({'success': True, **result})
        except context.platform_error as exc:
            return response({'success': False, 'error': str(exc)}, 400)
        except Exception:
            return response({'success': False, 'error': context.failure_message}, 503)
    return handle
