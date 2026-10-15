"""Owner-only Connect product access management, isolated by tenant."""
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web


def _json_response(payload, *, status=200):
    # aiohttp's json_response accepts a custom serializer via `dumps`, not a
    # `default` argument. The Connect entitlement payload contains datetimes.
    return web.json_response(payload, status=status,
                             dumps=lambda value: json.dumps(value, default=str))


@dataclass(frozen=True)
class ConnectAccessContext:
    member: Callable[..., dict | None]
    access: Callable[..., dict]
    update_entitlement: Callable[..., dict]
    set_member: Callable[..., None]
    platform_error: type[Exception]


def handler(context: ConnectAccessContext):
    async def endpoint(request: web.Request) -> web.Response:
        profile = context.member(request, owner_only=True)
        if not profile:
            return _json_response({'success': False, 'error': 'Yalnız şirkət sahibi Connect girişlərini idarə edə bilər.'}, status=403)
        tenant_id, owner_id = str(profile['tenant_id']), int(profile['telegram_id'])
        if request.method == 'GET':
            try:
                result = await asyncio.to_thread(context.access, tenant_id=tenant_id, owner_id=owner_id)
                return _json_response({'success': True, **result})
            except context.platform_error as exc:
                return _json_response({'success': False, 'error': str(exc)}, status=400)
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        action = str(data.get('action') or '').strip().casefold()
        try:
            if action == 'entitlement':
                await asyncio.to_thread(context.update_entitlement, tenant_id=tenant_id, owner_id=owner_id,
                    status=str(data.get('status') or 'inactive'), admin_access=bool(data.get('admin_access')))
            elif action == 'member':
                await asyncio.to_thread(context.set_member, tenant_id=tenant_id, owner_id=owner_id,
                    actor_id=int(data.get('actor_id') or 0), role=str(data.get('role') or ''), active=True)
            elif action == 'remove':
                await asyncio.to_thread(context.set_member, tenant_id=tenant_id, owner_id=owner_id,
                    actor_id=int(data.get('actor_id') or 0), role=None, active=False)
            else:
                return _json_response({'success': False, 'error': 'Naməlum əməliyyat.'}, status=400)
            result = await asyncio.to_thread(context.access, tenant_id=tenant_id, owner_id=owner_id)
            return _json_response({'success': True, **result})
        except (TypeError, ValueError):
            return _json_response({'success': False, 'error': 'Əməkdaş seçimi etibarlı deyil.'}, status=400)
        except context.platform_error as exc:
            return _json_response({'success': False, 'error': str(exc)}, status=400)
    return endpoint
