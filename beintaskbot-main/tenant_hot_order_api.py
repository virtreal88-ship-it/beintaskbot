"""HTTP boundary for tenant hot-order commands and review lists."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web


@dataclass(frozen=True)
class HotOrderApiContext:
    member: Callable[..., dict | None]
    policy: Callable[[dict], object]
    list_orders: Callable[..., dict]
    create_order: Callable[..., dict]
    change_order: Callable[..., dict]
    completion_command: Callable[..., dict]
    origin: str
    hot_order_error: type[Exception]
    logger: object


def handler(context: HotOrderApiContext) -> Callable:
    async def handle(request: web.Request) -> web.Response:
        headers = {"Cache-Control": "no-store"}
        try:
            profile = context.member(request)
            if not profile or not context.policy(profile).allows("hot_orders"):
                return web.json_response(
                    {"success": False, "error": "İcazə yoxdur."}, status=403, headers=headers)
            if request.method == "GET":
                filters = {"approvals_only": True} if request.rel_url.query.get("approvals") == "1" else {}
                result = await asyncio.to_thread(
                    context.list_orders, profile,
                    limit=int(request.rel_url.query.get("limit", "50")),
                    offset=int(request.rel_url.query.get("offset", "0")), **filters)
                return web.json_response({"success": True, **result}, headers=headers)
            request_origin = request.headers.get("Origin", "").rstrip("/")
            if request.content_type != "application/json" or request_origin != context.origin.rstrip("/"):
                return web.json_response(
                    {"success": False, "error": "Sorğunun mənbəyi düzgün deyil."},
                    status=403, headers=headers)
            data = await request.json()
            if not isinstance(data, dict):
                raise context.hot_order_error("Sorğu düzgün deyil.")
            if (str(data.get("expected_tenant_id") or "") != str(profile["tenant_id"])
                    or str(data.get("expected_user_id") or "") != str(profile["telegram_id"])):
                return web.json_response(
                    {"success": False, "error": "Kabinet dəyişib. Səhifəni yeniləyin."},
                    status=409, headers=headers)
            action = data.get("action")
            if action == "create":
                order = await asyncio.to_thread(context.create_order, profile, data)
            elif action in {"claim", "release", "cancel"}:
                order = await asyncio.to_thread(
                    context.change_order, profile, order_id=data.get("order_id"), action=action)
            elif action in {"complete", "approve", "reject"}:
                order = await asyncio.to_thread(context.completion_command, profile, data)
            else:
                raise context.hot_order_error("Əməliyyat düzgün deyil.")
            return web.json_response({"success": True, "order": order}, headers=headers)
        except context.hot_order_error as exc:
            return web.json_response(
                {"success": False, "error": str(exc)}, status=getattr(exc, "status", 400), headers=headers)
        except ValueError:
            return web.json_response(
                {"success": False, "error": "Sorğu düzgün deyil."}, status=400, headers=headers)
        except Exception:
            context.logger.error("Tenant hot-order API failed")
            return web.json_response(
                {"success": False, "error": "Sifariş əməliyyatı tamamlanmadı."},
                status=503, headers=headers)

    return handle
