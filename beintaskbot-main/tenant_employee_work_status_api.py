"""Authenticated, self-only work availability for the shared profile."""
import asyncio
import json
from aiohttp import web

from tenant_ai_agents_api import _expected, _origin
from tenant_dialog_presence import get_status, set_status
from tenant_linear_policy import TenantLinearError

_SCOPE = {"expected_tenant_id", "expected_user_id"}
_HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}


def handler(member_from_request, origin, logger):
    async def handle(request):
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError("Нет доступа.", 403)
            if request.method == "GET":
                if set(request.query) - _SCOPE:
                    raise TenantLinearError("Некорректный запрос.")
                _expected(request.query, profile)
                result = await asyncio.to_thread(get_status, profile, profile_only=True)
            else:
                _origin(request, origin)
                if request.method != "POST" or request.content_type != "application/json":
                    raise TenantLinearError("Метод не поддерживается.", 405)
                if request.content_length is not None and request.content_length > 2048:
                    raise TenantLinearError("Слишком большой запрос.", 413)
                body = bytearray()
                async for chunk in request.content.iter_chunked(2049):
                    body.extend(chunk)
                    if len(body) > 2048:
                        raise TenantLinearError("Слишком большой запрос.", 413)
                data = json.loads(body)
                if not isinstance(data, dict) or set(data) != _SCOPE | {"status"}:
                    raise TenantLinearError("Некорректный запрос.")
                _expected(data, profile)
                result = await asyncio.to_thread(set_status, profile, {"status": data["status"]}, profile_only=True)
            return web.json_response({"success": True, "result": result}, headers=_HEADERS)
        except TenantLinearError as error:
            return web.json_response({"success": False, "error": str(error)}, status=error.status, headers=_HEADERS)
        except (TypeError, ValueError, KeyError):
            return web.json_response({"success": False, "error": "Некорректный запрос."}, status=400, headers=_HEADERS)
        except Exception:
            logger.exception("Employee work status endpoint unavailable")
            return web.json_response({"success": False, "error": "Рабочий статус временно недоступен."}, status=503, headers=_HEADERS)
    return handle
