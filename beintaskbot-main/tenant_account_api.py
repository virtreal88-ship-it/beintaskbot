"""Self-service tenant account notification settings."""
import asyncio
from dataclasses import dataclass
from collections.abc import Callable
from aiohttp import web


@dataclass(frozen=True)
class NotificationSettingsContext:
    member: Callable[..., dict | None]
    preferences: Callable[..., dict]
    origin: str
    platform_error: type[Exception]


def handler(context: NotificationSettingsContext):
    async def handle(request: web.Request) -> web.Response:
        profile=context.member(request)
        if not profile: return web.json_response({'success':False,'error':'Platforma girişi tələb olunur.'},status=401)
        try:
            value=None
            if request.method!='GET':
                if request.headers.get('Origin','').rstrip('/')!=context.origin.rstrip('/'):
                    return web.json_response({'success':False,'error':'Sorğunun mənbəyi düzgün deyil.'},status=403)
                data=await request.json(); data=data if isinstance(data,dict) else {}
                value=data.get('preferences')
            result=await asyncio.to_thread(context.preferences,tenant_id=str(profile['tenant_id']),
                actor_id=int(profile['telegram_id']),value=value)
            return web.json_response({'success':True,**result},headers={'Cache-Control':'no-store'})
        except context.platform_error as exc:
            return web.json_response({'success':False,'error':str(exc)},status=400)
        except Exception:
            return web.json_response({'success':False,'error':'Bildiriş ayarları yüklənmədi.'},status=503)
    return handle
