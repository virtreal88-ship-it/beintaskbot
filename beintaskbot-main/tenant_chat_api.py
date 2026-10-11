"""Read-only HTTP boundary for a tenant-scoped cached CRM chat."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web


@dataclass(frozen=True)
class ChatApiContext:
    member: Callable[..., dict | None]
    policy: Callable[[dict], object]
    get_deal: Callable[..., dict | None]
    list_messages: Callable[..., list]
    sync_chat: Callable[..., object]
    platform_error: type[Exception]
    logger: object


def _can_read(context: ChatApiContext, profile: dict | None) -> bool:
    if not profile:
        return False
    policy = context.policy(profile)
    return policy.allows("deals") or policy.allows("customers")


def handlers(context: ChatApiContext) -> dict[str, Callable]:
    async def chat(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not _can_read(context, profile):
            return web.json_response(
                {"success": False, "error": "Çat tarixçəsi üçün icazəniz yoxdur."}, status=403)
        try:
            lead_id = int(request.rel_url.query.get("lead_id") or 0)
        except (TypeError, ValueError):
            lead_id = 0
        if lead_id <= 0:
            return web.json_response(
                {"success": False, "error": "Sövdələşmə seçilməyib."}, status=400)
        deal = await asyncio.to_thread(
            context.get_deal, tenant_id=profile["tenant_id"], kommo_lead_id=lead_id)
        if not context.policy(profile).can_access_deal(deal):
            return web.json_response(
                {"success": False, "error": "Sövdələşmə seçilmiş siyahıda deyil."}, status=404)
        warning = ""
        if str(request.rel_url.query.get("refresh") or "").casefold() in {"1", "true", "yes"}:
            try:
                await context.sync_chat(profile, lead_id)
            except context.platform_error as exc:
                warning = str(exc)
            except Exception:
                context.logger.exception(
                    "Tenant chat sync failed: tenant=%s lead=%s", profile["tenant_id"], lead_id)
                warning = "Çat tarixçəsi yenilənmədi. Son saxlanmış tarixçə göstərilir."
        rows = await asyncio.to_thread(
            context.list_messages, tenant_id=profile["tenant_id"], kommo_lead_id=lead_id)
        return web.json_response(
            {"success": True, "messages": rows, "stale": bool(warning), "warning": warning})

    return {"chat": chat}
