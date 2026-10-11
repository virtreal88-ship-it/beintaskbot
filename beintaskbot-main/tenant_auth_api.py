"""Tenant registration, login, session profile, logout, and auth pages."""
import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aiohttp import web


def telegram_display_name(payload: dict) -> str:
    parts = [str(payload.get(key) or "").strip() for key in ("first_name", "last_name")]
    return " ".join(part for part in parts if part)[:120] or "İstifadəçi"


@dataclass(frozen=True)
class AuthApiContext:
    member: Callable[..., dict | None]
    telegram_identity: Callable[[dict], int | None]
    issue_session: Callable[[str, int], str]
    tenants_for_user: Callable[[int], list]
    create_tenant: Callable[..., dict]
    list_integrations: Callable[..., list]
    list_members: Callable[..., list]
    capabilities: Callable[[dict], dict]
    disable_browser_push: Callable[[dict, str], object]
    role_permissions: Mapping
    platform_error: type[Exception]
    origin: str
    cookie_name: str
    cookie_ttl: int
    base_dir: str
    logger: object


def _set_session(response: web.Response, context: AuthApiContext, tenant_id: str, chat_id: int) -> None:
    response.set_cookie(
        context.cookie_name, context.issue_session(str(tenant_id), chat_id),
        max_age=context.cookie_ttl, httponly=True, secure=True, samesite="Lax", path="/",
    )


def _page(context: AuthApiContext, filename: str, missing: str) -> web.Response:
    candidates = (
        os.path.join(context.base_dir, "docs", filename),
        os.path.join(context.base_dir, filename),
    )
    html_path = next((path for path in candidates if os.path.isfile(path)), None)
    if not html_path:
        context.logger.error("%s; checked: %s", missing, ", ".join(candidates))
        return web.Response(status=404, text=missing)
    response = web.FileResponse(html_path)
    response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response


def handlers(context: AuthApiContext) -> dict[str, Callable]:
    async def register(request: web.Request) -> web.Response:
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
            existing = await asyncio.to_thread(context.tenants_for_user, chat_id)
            tenant = next((item for item in existing if item.get("role") == "owner" and item.get("status") == "onboarding"), None)
            if tenant is None:
                tenant = await asyncio.to_thread(
                    context.create_tenant, name=str(data.get("company_name") or "Yeni şirkət"),
                    industry=str(data.get("industry") or ""), owner_telegram_id=chat_id,
                    owner_name=telegram_display_name(telegram),
                )
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        except Exception:
            context.logger.exception("Platform tenant registration failed")
            return web.json_response({"success": False, "error": "Qeydiyyat zamanı xəta baş verdi."}, status=500)
        response = web.json_response({"success": True, "tenant": tenant, "redirect": "/setup"})
        _set_session(response, context, tenant["id"], chat_id)
        return response

    async def me(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile:
            return web.json_response({"success": False, "error": "Platforma girişi tələb olunur."}, status=401)
        try:
            telegram_id = int(profile["telegram_id"])
            integrations = await asyncio.to_thread(
                context.list_integrations, tenant_id=profile["tenant_id"], telegram_id=telegram_id)
            companies = await asyncio.to_thread(context.tenants_for_user, telegram_id)
            members = await asyncio.to_thread(
                context.list_members, tenant_id=profile["tenant_id"], owner_id=telegram_id,
            ) if profile.get("role") == "owner" else []
        except Exception:
            context.logger.exception("Platform session lookup failed")
            return web.json_response({"success": False, "error": "Platforma məlumatı yüklənmədi."}, status=500)
        safe_profile = {key: value for key, value in profile.items() if key != "workflow"}
        return web.json_response({
            "success": True, "member": safe_profile, "capabilities": context.capabilities(profile),
            "role_permissions": context.role_permissions, "integrations": integrations,
            "companies": companies, "members": members,
        })

    async def login(request: web.Request) -> web.Response:
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
            companies = await asyncio.to_thread(context.tenants_for_user, chat_id)
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=503)
        if not companies:
            return web.json_response({"success": False, "error": "Bu Telegram hesabı üçün giriş icazəsi yoxdur. Administrator sizi əməkdaş kartına əlavə etməlidir."}, status=403)
        requested_tenant = str(data.get("tenant_id") or "").strip()
        if requested_tenant:
            tenant = next((item for item in companies if str(item.get("id")) == requested_tenant), None)
            if not tenant:
                return web.json_response({"success": False, "error": "Bu şirkət üçün giriş icazəniz yoxdur."}, status=403)
        elif len(companies) == 1:
            tenant = companies[0]
        else:
            return web.json_response({"success": True, "select_company": True,
                                      "companies": [{"id": item["id"], "name": item["name"]} for item in companies]})
        destination = "/app" if str(tenant.get("status") or "") not in {"", "onboarding"} else "/setup"
        response = web.json_response({"success": True, "redirect": destination,
                                      "tenant": {"id": tenant["id"], "name": tenant["name"]}})
        _set_session(response, context, tenant["id"], chat_id)
        return response

    async def logout(request: web.Request) -> web.Response:
        if request.headers.get("Origin", "").rstrip("/") != context.origin.rstrip("/"):
            return web.json_response({"success": False, "error": "Sorğunun mənbəyi düzgün deyil."}, status=403)
        profile = context.member(request)
        device_id = request.cookies.get("tenant_push_device")
        if profile and device_id:
            try:
                await asyncio.to_thread(context.disable_browser_push, profile, device_id)
            except ValueError:
                pass
            except Exception:
                context.logger.error("Tenant browser push cleanup failed")
                return web.json_response({"success": False, "error": "Push cihazını ayırmaq alınmadı. Yenidən yoxlayın."}, status=503)
        response = web.json_response({"success": True}, headers={"Cache-Control": "no-store"})
        response.del_cookie("tenant_push_device", path="/")
        response.del_cookie(context.cookie_name, path="/")
        return response

    return {
        "register": register, "me": me, "login": login, "logout": logout,
        "onboarding_page": lambda request: _page(context, "onboarding.html", "Platform onboarding page not found"),
        "app_page": lambda request: _page(context, "tenant-app.html", "Tenant CRM page not found"),
    }
