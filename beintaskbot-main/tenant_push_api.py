"""HTTP boundary for tenant-scoped browser push devices."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web


@dataclass(frozen=True)
class PushApiContext:
    member: Callable[..., dict | None]
    list_devices: Callable[..., list]
    register_device: Callable[..., dict]
    disable_device: Callable[..., None]
    origin: str
    max_devices: int
    public_key: str
    private_key: str
    platform_error: type[Exception]
    logger: object


def handler(context: PushApiContext) -> Callable:
    async def handle(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile or profile.get("active") is not True:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        request_origin = request.headers.get("Origin", "").rstrip("/")
        if request.method != "GET" and (
                request.content_type != "application/json" or request_origin != context.origin.rstrip("/")):
            return web.json_response(
                {"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        headers = {"Cache-Control": "no-store"}
        delivery_enabled = bool(context.public_key and context.private_key)
        try:
            if request.method == "GET":
                devices = await asyncio.to_thread(context.list_devices, profile)
                return web.json_response({
                    "success": True, "devices": devices, "max_devices": context.max_devices,
                    "tenant_id": str(profile["tenant_id"]), "user_id": str(profile["telegram_id"]),
                    "public_key": context.public_key, "delivery_enabled": delivery_enabled,
                }, headers=headers)
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Sorğu düzgün deyil.")
            if (data.get("expected_tenant_id", str(profile["tenant_id"])) != str(profile["tenant_id"])
                    or str(data.get("expected_user_id", profile["telegram_id"])) != str(profile["telegram_id"])):
                return web.json_response(
                    {"success": False, "error": "Kabinet dəyişib. Səhifəni yeniləyin."},
                    status=409, headers=headers)
            if data.get("action") == "subscribe":
                device = await asyncio.to_thread(
                    context.register_device, profile, data.get("subscription"), data.get("label", ""))
                response = web.json_response(
                    {"success": True, "device": device, "delivery_enabled": delivery_enabled}, headers=headers)
                response.set_cookie(
                    "tenant_push_device", device["device_id"], max_age=31536000,
                    httponly=True, secure=True, samesite="Lax", path="/")
                return response
            if data.get("action") == "unsubscribe":
                await asyncio.to_thread(context.disable_device, profile, data.get("device_id"))
                return web.json_response({"success": True}, headers=headers)
            raise ValueError("Əməliyyat düzgün deyil.")
        except (ValueError, context.platform_error) as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400, headers=headers)
        except Exception:
            # Subscription URLs and keys are private; never log request/error bodies.
            context.logger.error("Tenant push device API failed: tenant=%s", profile["tenant_id"])
            return web.json_response(
                {"success": False, "error": "Cihaz ayarları saxlanmadı."}, status=500, headers=headers)

    return handle
