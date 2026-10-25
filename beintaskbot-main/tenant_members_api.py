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
    provision_employee_account: Callable[..., dict] | None = None
    can_manage: Callable[[dict], bool] | None = None
    set_member_whatsapp_numbers: Callable[..., object] | None = None
    add_member_whatsapp_number: Callable[..., object] | None = None
    accept_email_invite: Callable[..., dict] | None = None
    issue_session: Callable[[str, int], str] | None = None
    cookie_name: str = 'csa_tenant_session'
    cookie_ttl: int = 28800

    def may_manage(self, profile: dict | None) -> bool:
        return bool(profile and (self.can_manage(profile) if self.can_manage else profile.get('role') == 'owner'))


def handlers(context: MemberApiContext) -> dict[str, Callable]:
    async def members(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not context.may_manage(profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        tenant_id, owner_id = profile["tenant_id"], int(profile["telegram_id"])
        if request.method == "GET":
            try:
                rows = await asyncio.to_thread(context.list_members, tenant_id=tenant_id, owner_id=owner_id)
            except context.platform_error as exc:
                return web.json_response({"success": False, "error": str(exc)}, status=400)
            return web.json_response({"success": True, "members": rows},headers={'Cache-Control':'no-store','Vary':'Cookie'})
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        action = str(data.get("action") or "upsert").strip().casefold()
        try:
            if action == "whatsapp_numbers":
                telegram_id = int(data.get("telegram_id") or 0)
                if request.headers.get('Origin','').rstrip('/') != context.origin.rstrip('/'):
                    return web.json_response({'success':False,'error':'Sorğunun mənbəyi düzgün deyil.'},status=403)
                if (str(data.get('expected_tenant_id') or '')!=str(tenant_id)
                        or str(data.get('expected_user_id') or '')!=str(owner_id)):
                    return web.json_response({'success':False,'error':'Şirkət dəyişib. Yeniləyin.'},status=409)
                if not context.set_member_whatsapp_numbers:
                    return web.json_response({'success':False,'error':'Bu funksiya əlçatan deyil.'},status=503)
                await asyncio.to_thread(context.set_member_whatsapp_numbers,tenant_id=tenant_id,
                    owner_id=owner_id,telegram_id=telegram_id,
                    numbers=data.get('numbers') if isinstance(data.get('numbers'),list) else None)
            elif action == "add_whatsapp_number":
                telegram_id = int(data.get("telegram_id") or 0)
                if request.headers.get('Origin','').rstrip('/') != context.origin.rstrip('/'):
                    return web.json_response({'success':False,'error':'Sorğunun mənbəyi düzgün deyil.'},status=403)
                if (str(data.get('expected_tenant_id') or '')!=str(tenant_id)
                        or str(data.get('expected_user_id') or '')!=str(owner_id)):
                    return web.json_response({'success':False,'error':'Şirkət dəyişib. Yeniləyin.'},status=409)
                if not context.add_member_whatsapp_number:
                    return web.json_response({'success':False,'error':'Bu funksiya əlçatan deyil.'},status=503)
                await asyncio.to_thread(context.add_member_whatsapp_number,tenant_id=tenant_id,
                    owner_id=owner_id,telegram_id=telegram_id,phone=str(data.get('phone') or ''))
            elif action == "deactivate":
                telegram_id = int(data.get("telegram_id") or 0)
                await asyncio.to_thread(context.deactivate_member, tenant_id=tenant_id, owner_id=owner_id,
                                        telegram_id=telegram_id)
            elif action == "upsert":
                telegram_id = int(data.get("telegram_id") or 0)
                await asyncio.to_thread(
                    context.upsert_member, tenant_id=tenant_id, owner_id=owner_id, telegram_id=telegram_id,
                    display_name=str(data.get("display_name") or ""), role=str(data.get("role") or "worker"),
                    permissions=data.get("permissions") if isinstance(data.get("permissions"), list) else [],
                )
            elif action == 'provision_account':
                if request.headers.get('Origin','').rstrip('/') != context.origin.rstrip('/'):
                    return web.json_response({'success':False,'error':'Sorğunun mənbəyi düzgün deyil.'},status=403)
                if not context.provision_employee_account:
                    return web.json_response({'success':False,'error':'Bu funksiya əlçatan deyil.'},status=503)
                await asyncio.to_thread(context.provision_employee_account,tenant_id=tenant_id,owner_id=owner_id,
                    first_name=str(data.get('first_name') or ''),last_name=str(data.get('last_name') or ''),login=str(data.get('login') or ''),
                    password=str(data.get('password') or ''),role=str(data.get('role') or 'worker'),
                    permissions=data.get('permissions') if isinstance(data.get('permissions'),list) else [],password_confirm=str(data.get('confirm_password') or ''),
                    email=str(data.get('email') or ''),phone=str(data.get('phone') or ''))
            else:
                raise context.platform_error("Naməlum əməliyyat.")
            rows = await asyncio.to_thread(context.list_members, tenant_id=tenant_id, owner_id=owner_id)
        except (TypeError, ValueError):
            return web.json_response({"success": False, "error": "Telegram ID düzgün deyil."}, status=400)
        except context.platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        return web.json_response({"success": True, "members": rows},headers={'Cache-Control':'no-store','Vary':'Cookie'})

    async def create(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not context.may_manage(profile):
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        try:
            invite_args={}
            if str(data.get('email') or '').strip(): invite_args['invited_email']=str(data.get('email') or '')
            invite = await asyncio.to_thread(
                context.create_invite, tenant_id=profile["tenant_id"], owner_id=int(profile["telegram_id"]),
                display_name=str(data.get("display_name") or ""), role=str(data.get("role") or "worker"),
                permissions=data.get("permissions") if isinstance(data.get("permissions"), list) else [],
                **invite_args,
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

    async def accept_email(request: web.Request) -> web.Response:
        profile=context.member(request)
        if not profile or not profile.get('email') or not context.accept_email_invite:
            return web.json_response({'success':False,'error':'Dəvəti qəbul etmək üçün email hesabı ilə daxil olun.'},status=401)
        if request.headers.get('Origin','').rstrip('/') != context.origin.rstrip('/'):
            return web.json_response({'success':False,'error':'Sorğunun mənbəyi düzgün deyil.'},status=403)
        try:
            data=await request.json();data=data if isinstance(data,dict) else {}
            result=await asyncio.to_thread(context.accept_email_invite,token=str(data.get('token') or ''),
                actor_id=int(profile['telegram_id']),email=str(profile['email']),display_name=str(profile.get('display_name') or ''))
            response=web.json_response({'success':True,'tenant':{'id':result['tenant_id'],'name':result['tenant_name']},'redirect':'/app'})
            if context.issue_session:
                response.set_cookie(context.cookie_name,context.issue_session(result['tenant_id'],int(profile['telegram_id'])),
                    max_age=context.cookie_ttl,httponly=True,secure=True,samesite='Lax',path='/')
            return response
        except context.platform_error as exc:
            return web.json_response({'success':False,'error':str(exc)},status=400)

    async def requests(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not context.may_manage(profile):
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

    return {"members": members, "create_invite": create, "accept_invite": accept, "accept_email_invite": accept_email,
            "invite_requests": requests}
