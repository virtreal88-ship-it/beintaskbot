"""Authenticated same-origin API for local dialog reply templates."""
import asyncio
import json

from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_dialog_reply_templates import feature_ready, list_templates, mutate_template
from tenant_linear_policy import TenantLinearError
from tenant_beinsystems_operator_access import is_operator_workspace


def handler(member_from_request, origin, logger):
    async def handle(request):
        headers = {"Cache-Control": "no-store"}
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError("Нет доступа.", 403)
            if (not is_operator_workspace(profile.get("tenant_id"))
                    or not feature_ready(profile)):
                raise TenantLinearError("Шаблоны недоступны для этого workspace.", 404)
            if request.method == "GET":
                _expected(request.query, profile)
                result = await asyncio.to_thread(list_templates, profile)
            elif request.method == "POST":
                _origin(request, origin)
                if request.content_type != "application/json":
                    raise TenantLinearError("Ожидается JSON-запрос.", 415)
                if request.content_length is not None and request.content_length > 20_000:
                    raise TenantLinearError("Слишком большой запрос.", 413)
                raw = await request.read()
                if len(raw) > 20_000:
                    raise TenantLinearError("Слишком большой запрос.", 413)
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise TenantLinearError("Некорректный запрос.")
                _expected(data, profile)
                payload = {key: value for key, value in data.items()
                           if key not in {"expected_tenant_id", "expected_user_id"}}
                result = await asyncio.to_thread(mutate_template, profile, payload)
            else:
                raise TenantLinearError("Метод не поддерживается.", 405)
            return web.json_response({"success": True, "result": result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({"success": False, "error": str(error)},
                                     status=error.status, headers=headers)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return web.json_response({"success": False, "error": "Некорректный запрос."},
                                     status=400, headers=headers)
        except Exception as error:
            logger.error("Reply template API failed (%s)", type(error).__name__)
            return web.json_response({"success": False, "error": "Шаблоны временно недоступны."},
                                     status=503, headers=headers)
    return handle
