"""Authenticated personal Kommo board preferences HTTP boundary."""
import asyncio
from dataclasses import dataclass
from collections.abc import Callable
from aiohttp import web


@dataclass(frozen=True)
class DealBoardContext:
    member: Callable
    preferences: Callable
    origin: str
    platform_error: type[Exception]


def handler(context: DealBoardContext):
    def response(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    async def handle(request):
        if request.method not in {'GET', 'POST'}:
            return response({'success': False, 'error': 'Method not allowed'}, 405)
        profile = context.member(request)
        if not profile or profile.get('active') is False:
            return response({'success': False, 'error': 'Platforma girişi tələb olunur.'}, 401)
        hidden = None
        if request.method == 'POST':
            if request.headers.get('Origin', '').rstrip('/') != context.origin.rstrip('/'):
                return response({'success': False, 'error': 'Sorğunun mənbəyi düzgün deyil.'}, 403)
            try:
                data = await request.json()
                if not isinstance(data, dict) or set(data) != {'hidden_stage_keys'} or not isinstance(data['hidden_stage_keys'], list):
                    raise ValueError()
                hidden = data['hidden_stage_keys']
            except Exception:
                return response({'success': False, 'error': 'Sütun seçimi düzgün deyil.'}, 400)
        try:
            result = await asyncio.to_thread(context.preferences, tenant_id=str(profile['tenant_id']),
                                            actor_id=int(profile['telegram_id']), hidden=hidden)
            return response({'success': True, **result})
        except context.platform_error as exc:
            return response({'success': False, 'error': str(exc)}, 400)
        except Exception:
            return response({'success': False, 'error': 'Sütun ayarları yüklənmədi və ya saxlanmadı.'}, 503)
    return handle
