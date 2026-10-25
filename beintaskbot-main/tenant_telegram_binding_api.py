"""Browser endpoint for a self-service, one-time Telegram binding."""
import asyncio
from dataclasses import dataclass
from collections.abc import Callable
from aiohttp import web


@dataclass(frozen=True)
class TelegramBindingContext:
    member: Callable[..., dict | None]
    request_binding: Callable[..., dict]
    binding_status: Callable[..., dict]
    origin: str
    bot_username: Callable[[], str]
    platform_error: type[Exception]


def handler(context: TelegramBindingContext):
    async def handle(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile:
            return web.json_response({'success': False, 'error': 'Platforma girişi tələb olunur.'}, status=401)
        try:
            if request.method == 'GET':
                result = await asyncio.to_thread(context.binding_status, tenant_id=str(profile['tenant_id']), actor_id=int(profile['telegram_id']))
            else:
                if request.headers.get('Origin', '').rstrip('/') != context.origin.rstrip('/'):
                    return web.json_response({'success': False, 'error': 'Sorğunun mənbəyi düzgün deyil.'}, status=403)
                result = await asyncio.to_thread(context.request_binding, tenant_id=str(profile['tenant_id']), actor_id=int(profile['telegram_id']), bot_username=context.bot_username())
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)
    return handle
