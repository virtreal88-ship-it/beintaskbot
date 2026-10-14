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
    email_auth: object | None = None
    sso: object | None = None


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
    def request_ip(request: web.Request) -> str:
        # Railway terminates TLS; trust only the first forwarded address and
        # keep it solely for bounded abuse protection, never for identity.
        return str(request.headers.get('X-Forwarded-For') or request.remote or '').split(',',1)[0].strip()[:80]

    async def sso_authorize(request: web.Request) -> web.Response:
        """Issue a one-time code only for the already authenticated company."""
        if context.sso is None:
            return web.json_response({'error': 'sso_unavailable'}, status=503)
        profile = context.member(request)
        if not profile:
            return web.json_response({'error': 'login_required'}, status=401)
        client_id = str(request.query.get('client_id') or '')
        redirect_uri = str(request.query.get('redirect_uri') or '')
        challenge = str(request.query.get('code_challenge') or '')
        state = str(request.query.get('state') or '')
        if not state or len(state) > 512:
            return web.json_response({'error': 'invalid_request'}, status=400)
        try:
            code = await asyncio.to_thread(context.sso.issue_code, tenant_id=str(profile['tenant_id']),
                actor_id=int(profile['telegram_id']), client_id=client_id, redirect_uri=redirect_uri, code_challenge=challenge)
        except context.platform_error as exc:
            return web.json_response({'error': 'access_denied', 'message': str(exc)}, status=403)
        separator = '&' if '?' in redirect_uri else '?'
        raise web.HTTPFound(f'{redirect_uri}{separator}code={code}&state={state}')

    async def sso_token(request: web.Request) -> web.Response:
        if context.sso is None:
            return web.json_response({'error': 'sso_unavailable'}, status=503)
        try:
            data = await request.json()
            result = await asyncio.to_thread(context.sso.exchange_code, code=str((data or {}).get('code') or ''),
                verifier=str((data or {}).get('code_verifier') or ''), client_id=str((data or {}).get('client_id') or ''),
                redirect_uri=str((data or {}).get('redirect_uri') or ''), client_secret=str((data or {}).get('client_secret') or ''))
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except context.platform_error as exc:
            return web.json_response({'error': 'invalid_grant', 'message': str(exc)}, status=400)

    async def email_register(request: web.Request) -> web.Response:
        if context.email_auth is None:
            return web.json_response({'success': False, 'error': 'Email girişi hələ quraşdırılmayıb.'}, status=503)
        try:
            data = await request.json()
            result = await asyncio.to_thread(context.email_auth.request_registration,
                name=(data or {}).get('name'), company_name=(data or {}).get('company_name'),
                phone=(data or {}).get('phone'), email=(data or {}).get('email'), request_ip=request_ip(request))
            return web.json_response({'success': True, **result})
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)
        except Exception:
            context.logger.exception('Email registration failed')
            return web.json_response({'success': False, 'error': 'Qeydiyyat zamanı xəta baş verdi.'}, status=500)

    async def email_confirm(request: web.Request) -> web.Response:
        if context.email_auth is None: return web.json_response({'success': False}, status=503)
        try:
            data = await request.json(); result = await asyncio.to_thread(context.email_auth.complete_confirmation, token=(data or {}).get('token'), password=(data or {}).get('password'))
            response = web.json_response({'success': True, 'redirect': '/app'})
            _set_session(response, context, result['tenant_id'], result['actor_id'])
            return response
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)

    async def email_login(request: web.Request) -> web.Response:
        if context.email_auth is None: return web.json_response({'success': False}, status=503)
        try:
            data = await request.json(); result = await asyncio.to_thread(context.email_auth.login, email=(data or {}).get('email'), password=(data or {}).get('password'), request_ip=request_ip(request))
            if not result: return web.json_response({'success': False, 'error': 'Email və ya parol düzgün deyil.'}, status=401)
            response = web.json_response({'success': True, 'redirect': '/app'})
            _set_session(response, context, result['tenant_id'], result['actor_id'])
            return response
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)

    async def password_reset(request: web.Request) -> web.Response:
        if context.email_auth is None: return web.json_response({'success': True})
        try:
            data = await request.json(); await asyncio.to_thread(context.email_auth.request_password_reset, email=(data or {}).get('email'), request_ip=request_ip(request))
        except context.platform_error:
            # Keep this intentionally generic so an attacker cannot enumerate
            # customer addresses through the reset endpoint.
            pass
        except Exception:
            context.logger.exception('Password reset request failed')
        return web.json_response({'success': True, 'message': 'Bu email qeydiyyatdadırsa, bərpa keçidi göndərildi.'})

    async def password_reset_confirm(request: web.Request) -> web.Response:
        if context.email_auth is None: return web.json_response({'success': False}, status=503)
        try:
            data = await request.json(); await asyncio.to_thread(context.email_auth.complete_password_reset, token=(data or {}).get('token'), password=(data or {}).get('password'))
            return web.json_response({'success': True})
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)

    async def email_link_request(request: web.Request) -> web.Response:
        if context.email_auth is None:
            return web.json_response({'success': False}, status=503)
        if request.headers.get('Origin', '').rstrip('/') != context.origin.rstrip('/'):
            return web.json_response({'success': False, 'error': 'Sorğunun mənbəyi düzgün deyil.'}, status=403)
        profile = context.member(request)
        if not profile:
            return web.json_response({'success': False, 'error': 'Platforma girişi tələb olunur.'}, status=401)
        try:
            data = await request.json()
            result = await asyncio.to_thread(
                context.email_auth.request_email_link,
                tenant_id=str(profile['tenant_id']), actor_id=int(profile['telegram_id']),
                display_name=profile.get('display_name') or 'İstifadəçi',
                email=(data or {}).get('email'), request_ip=request_ip(request),
            )
            return web.json_response({'success': True, **result})
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)
        except Exception:
            context.logger.exception('Email link request failed')
            return web.json_response({'success': False, 'error': 'Email bağlantısı alınmadı.'}, status=500)

    async def email_link_confirm(request: web.Request) -> web.Response:
        if context.email_auth is None: return web.json_response({'success': False}, status=503)
        try:
            data = await request.json()
            result = await asyncio.to_thread(context.email_auth.complete_email_link,
                token=(data or {}).get('token'), password=(data or {}).get('password'))
            response = web.json_response({'success': True, 'redirect': '/app'})
            _set_session(response, context, result['tenant_id'], result['actor_id'])
            return response
        except context.platform_error as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=400)
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
        'sso_authorize': sso_authorize, 'sso_token': sso_token,
        'email_register': email_register, 'email_confirm': email_confirm,
        'email_login': email_login, 'password_reset': password_reset,
        'password_reset_confirm': password_reset_confirm,
        'email_link_request': email_link_request, 'email_link_confirm': email_link_confirm,
        "onboarding_page": lambda request: _page(context, "onboarding.html", "Platform onboarding page not found"),
        'auth_page': lambda request: _page(context, 'auth.html', 'Platform authentication page not found'),
        'confirm_page': lambda request: _page(context, 'auth-confirm.html', 'Platform confirmation page not found'),
        "app_page": lambda request: _page(context, "tenant-app.html", "Tenant CRM page not found"),
    }
