"""Tenant employee cards and Telegram invitation workflow."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from aiohttp import web

@dataclass(frozen=True)
class MemberApiContext:
    member: Callable[..., dict | None]
    telegram_identity: Callable[[dict], int | None]
    telegram_name: Callable[[dict], str]
    send_telegram: Callable[[int, str], object]
    origin: str
    list_members: Callable[..., list]
    upsert_member: Callable[..., object]
    deactivate_member: Callable[..., object]
    create_invite: Callable[..., dict]
    request_invite_acceptance: Callable[..., dict]
    list_invite_requests: Callable[..., list]
    decide_invite_request: Callable[..., dict]
    platform_error: type[Exception]


def handlers(context: MemberApiContext) -> dict[str, Callable]:
    async def members(request: web.Request) -> web.Response:
        profile = context.member(request, owner_only=True)
        if not profile:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        tenant_id, owner_id = profile["tenant_id"], int(profile["telegram_id"])
        if request.method == "GET":
            try:
                rows = await asyncio.to_thread(context.list_members, tenant_id=tenant_id, owner_id=owner_id)
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
            return web.json_response({"success": True, "members": rows})
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        action = str(data.get("action") or "upsert").strip().casefold()
        try:
            telegram_id = int(data.get("telegram_id") or 0)
            if action == "deactivate":
                await asyncio.to_thread(context.deactivate_member, tenant_id=tenant_id, owner_id=owner_id,
                                        telegram_id=telegram_id)
            elif action == "upsert":
                await asyncio.to_thread(
                    context.upsert_member, tenant_id=tenant_id, owner_id=owner_id, telegram_id=telegram_id,
                    display_name=str(data.get("display_name") or ""), role=str(data.get("role") or "worker"),
                    permissions=data.get("permissions") if isinstance(data.get("permissions"), list) else [],
                )
            else:
                raise context.platform_error("Naməlum əməliyyat.")
            rows = await asyncio.to_thread(context.list_members, tenant_id=tenant_id, owner_id=owner_id)
        except (TypeError, ValueError):
            return web.json_response({"success": False, "error": "Telegram ID düzgün deyil."}, status=400)
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        return web.json_response({"success": True, "members": rows})

    async def create(request: web.Request) -> web.Response:
        profile = context.member(request, owner_only=True)
        if not profile:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        try:
            invite = await asyncio.to_thread(
                context.create_invite, tenant_id=profile["tenant_id"], owner_id=int(profile["telegram_id"]),
                display_name=str(data.get("display_name") or ""), role=str(data.get("role") or "worker"),
                permissions=data.get("permissions") if isinstance(data.get("permissions"), list) else [],
            )
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        return web.json_response({"success": True, "invite": {
            **invite, "url": f"{context.origin}/invite/{invite['token']}"}})

    async def accept(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        telegram = data.get("telegram") if isinstance(data.get("telegram"), dict) else {}
        chat_id = context.telegram_identity(telegram)
        if not chat_id:
            return web.json_response({"success": False, "error": "Telegram təsdiqi etibarlı deyil."}, status=401)
        try:
            pending = await asyncio.to_thread(
                context.request_invite_acceptance, token=str(data.get("token") or ""), telegram_id=chat_id,
                display_name=context.telegram_name(telegram),
            )
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        if pending.get("status") == "pending":
            context.send_telegram(
                pending.get("owner_id"),
                f"🔐 Yeni giriş sorğusu\n\n👤 {pending.get('employee_name') or 'Əməkdaş'}\n"
                f"📌 Kart: {pending.get('card_name') or 'Əməkdaş'}\n\n"
                f"Təsdiqləmək üçün: {context.origin}/app",
            )
        return web.json_response({"success": True, "pending": True,
            "message": "Sorğu administratora göndərildi. Təsdiqdən sonra /login səhifəsindən daxil olun."})

    async def requests(request: web.Request) -> web.Response:
        profile = context.member(request, owner_only=True)
        if not profile:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        tenant_id, owner_id = profile["tenant_id"], int(profile["telegram_id"])
        if request.method == "GET":
            try:
                rows = await asyncio.to_thread(context.list_invite_requests, tenant_id=tenant_id, owner_id=owner_id)
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
            return web.json_response({"success": True, "requests": rows})
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        try:
            decided = await asyncio.to_thread(
                context.decide_invite_request, tenant_id=tenant_id, owner_id=owner_id,
                request_id=str(data.get("request_id") or ""),
                approve=str(data.get("action") or "").strip().casefold() == "approve",
            )
            rows = await asyncio.to_thread(context.list_invite_requests, tenant_id=tenant_id, owner_id=owner_id)
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        message = ("✅ Girişiniz təsdiqləndi. İndi crm.pro.az/login səhifəsindən daxil ola bilərsiniz."
                   if decided["approved"] else "❌ Giriş sorğunuz təsdiqlənmədi.")
        context.send_telegram(decided["telegram_id"], message)
        return web.json_response({"success": True, "requests": rows})

    return {"members": members, "create_invite": create, "accept_invite": accept,
            "invite_requests": requests}
