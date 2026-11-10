"""Tenant employee cards and Telegram invitation workflow."""
import asyncio
import re
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
    source_session: Callable[..., dict | None] | None = None
    assign_member_password: Callable[..., dict] | None = None
    assign_member_email: Callable[..., dict] | None = None
    assign_member_profile: Callable[..., dict] | None = None

    def may_manage(self, profile: dict | None) -> bool:
        return bool(profile and (self.can_manage(profile) if self.can_manage else profile.get('role') == 'owner'))


def handlers(context: MemberApiContext) -> dict[str, Callable]:
    def failure(exc):
        return web.json_response({'success': False, 'error': str(exc),
            'code': getattr(exc, 'code', 'member_operation_failed'),
            'fields': getattr(exc, 'fields', {})}, status=getattr(exc, 'status', 400))

    def invalid(fields=None):
        return web.json_response({'success': False, 'error': 'Məlumatlar düzgün deyil.',
            'code': 'member_card_invalid', 'fields': fields or {}}, status=400)

    def strict_context(request, data, tenant_id, owner_id):
        if request.headers.get('Origin', '').rstrip('/') != context.origin.rstrip('/'):
            return web.json_response({'success': False, 'error': 'Sorğunun mənbəyi düzgün deyil.',
                                     'code': 'member_origin_invalid', 'fields': {}}, status=403)
        if any(key not in data or data[key] in (None, '')
               for key in ('expected_tenant_id', 'expected_user_id')):
            return invalid({'context': 'invalid'})
        if (str(data['expected_tenant_id']) != str(tenant_id)
                or str(data['expected_user_id']) != str(owner_id)):
            return web.json_response({'success': False, 'error': 'Şirkət və ya istifadəçi dəyişib. Yeniləyin.',
                                     'code': 'member_context_changed', 'fields': {}}, status=409)
        return None

    async def members(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not context.may_manage(profile):
            return web.json_response({'success': False, 'error': 'İcazə yoxdur.',
                                     'code': 'member_access_revoked', 'fields': {}}, status=403)
        tenant_id, owner_id = profile["tenant_id"], int(profile["telegram_id"])
        if request.method == "GET":
            try:
                rows = await asyncio.to_thread(context.list_members, tenant_id=tenant_id, owner_id=owner_id)
            except context.platform_error as exc:
                return failure(exc)
            return web.json_response({"success": True, "members": rows},headers={'Cache-Control':'no-store','Vary':'Cookie'})
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        action = str(data.get("action") or "upsert").strip().casefold()
        try:
            if action == 'update_member_card':
                rejected = strict_context(request, data, tenant_id, owner_id)
                if rejected is not None:
                    return rejected
                if set(data) - {'action', 'telegram_id', 'expected_version', 'expected_tenant_id',
                                'expected_user_id', 'changes'}:
                    return invalid({'changes': 'unknown_field'})
                changes = data.get('changes')
                if (type(data.get('telegram_id')) is bool or not str(data.get('telegram_id') or '').isdigit()
                        or int(data['telegram_id']) <= 0):
                    return invalid({'telegram_id': 'invalid'})
                if (not isinstance(data.get('expected_version'), str) or not data['expected_version']
                        or len(data['expected_version']) > 100):
                    return invalid({'expected_version': 'invalid'})
                if (not isinstance(changes, dict) or not changes
                        or set(changes) - {'display_name', 'role', 'permissions', 'active', 'whatsapp_numbers'}):
                    return invalid({'changes': 'invalid'})
                result = await asyncio.to_thread(context.upsert_member, tenant_id=tenant_id,
                    owner_id=owner_id, telegram_id=int(data['telegram_id']),
                    expected_version=data['expected_version'], changes=changes)
                # The write's committed receipt is independent of a later
                # readback, which may legitimately lose authority meanwhile.
                return web.json_response({'success': True, 'applied': {
                    'telegram_id': result['telegram_id'], 'card_version': result['card_version']}},
                    headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'})
            elif action == 'assign_member_password':
                rejected = strict_context(request, data, tenant_id, owner_id)
                if rejected is not None:
                    return rejected
                if profile.get('role') != 'owner' or profile.get('active') is not True:
                    return web.json_response({'success': False, 'code': 'member_access_revoked', 'fields': {}}, status=403)
                if set(data) - {'action','telegram_id','expected_version','expected_tenant_id','expected_user_id','password','confirm_password'}:
                    return invalid({'changes': 'unknown_field'})
                if (type(data.get('telegram_id')) is bool or not str(data.get('telegram_id') or '').isdigit()
                        or int(data['telegram_id']) <= 0 or not isinstance(data.get('expected_version'), str)
                        or not 1 <= len(data['expected_version']) <= 100
                        or not isinstance(data.get('password'), str) or not 12 <= len(data['password']) <= 256
                        or not isinstance(data.get('confirm_password'), str) or len(data['confirm_password']) > 256):
                    return invalid({'password': 'invalid'})
                source = context.source_session(request) if context.source_session else None
                if not source:
                    return web.json_response({'success': False, 'code': 'member_access_revoked', 'fields': {}}, status=401)
                if not context.assign_member_password:
                    return web.json_response({'success': False, 'code': 'member_password_unavailable', 'fields': {}}, status=503)
                result = await asyncio.to_thread(context.assign_member_password,
                    tenant_id=tenant_id, owner_id=owner_id, telegram_id=int(data['telegram_id']),
                    password=data['password'], confirm_password=data['confirm_password'],
                    expected_version=data['expected_version'], source_session=source)
                return web.json_response({'success': True, 'applied': result},
                    headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'})
            elif action == 'assign_member_email':
                rejected = strict_context(request, data, tenant_id, owner_id)
                if rejected is not None:
                    return rejected
                if profile.get('role') != 'owner' or profile.get('active') is not True:
                    return web.json_response({'success': False, 'code': 'member_access_revoked', 'fields': {}}, status=403)
                if set(data) - {'action','telegram_id','expected_version','expected_tenant_id','expected_user_id','email'}:
                    return invalid({'changes': 'unknown_field'})
                if (type(data.get('telegram_id')) is bool or not str(data.get('telegram_id') or '').isdigit()
                        or int(data['telegram_id']) <= 0 or not isinstance(data.get('expected_version'), str)
                        or not 1 <= len(data['expected_version']) <= 100
                        or not isinstance(data.get('email'), str) or not 3 <= len(data['email']) <= 254):
                    return invalid({'new_email': 'invalid'})
                source = context.source_session(request) if context.source_session else None
                if not source:
                    return web.json_response({'success': False, 'code': 'member_access_revoked', 'fields': {}}, status=401)
                if not context.assign_member_email:
                    return web.json_response({'success': False, 'code': 'member_email_unavailable', 'fields': {}}, status=503)
                result = await asyncio.to_thread(context.assign_member_email,
                    tenant_id=tenant_id, owner_id=owner_id, telegram_id=int(data['telegram_id']),
                    email=data['email'],
                    expected_version=data['expected_version'], source_session=source)
                return web.json_response({'success': True, 'applied': result},
                    headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'})
            elif action == 'assign_member_profile':
                rejected = strict_context(request, data, tenant_id, owner_id)
                if rejected is not None:
                    return rejected
                if profile.get('role') != 'owner' or profile.get('active') is not True:
                    return web.json_response({'success': False, 'code': 'member_access_revoked', 'fields': {}}, status=403)
                if set(data) - {'action','telegram_id','expected_version','expected_tenant_id','expected_user_id','changes'}:
                    return invalid({'changes': 'unknown_field'})
                if (type(data.get('telegram_id')) is bool or not str(data.get('telegram_id') or '').isdigit()
                        or int(data['telegram_id']) <= 0 or not isinstance(data.get('expected_version'), str)
                        or not 1 <= len(data['expected_version']) <= 100
                        or not isinstance(data.get('changes'), dict) or not data['changes']
                        or set(data['changes']) - {'first_name','last_name','login','phone','avatar_url'}
                        or any(not isinstance(v, str) for v in data['changes'].values())):
                    return invalid({'changes': 'invalid'})
                source = context.source_session(request) if context.source_session else None
                if not source:
                    return web.json_response({'success': False, 'code': 'member_access_revoked', 'fields': {}}, status=401)
                if not context.assign_member_profile:
                    return web.json_response({'success': False, 'code': 'member_profile_unavailable', 'fields': {}}, status=503)
                result = await asyncio.to_thread(context.assign_member_profile,
                    tenant_id=tenant_id, owner_id=owner_id, telegram_id=int(data['telegram_id']),
                    changes=data['changes'],
                    expected_version=data['expected_version'], source_session=source)
                return web.json_response({'success': True, 'applied': result},
                    headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'})
            elif action == "whatsapp_numbers":
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
            elif action in {'provision_account', 'provision_member_account'}:
                if action == 'provision_member_account':
                    rejected = strict_context(request, data, tenant_id, owner_id)
                    if rejected is not None:
                        return rejected
                    if set(data) - {'action', 'expected_tenant_id', 'expected_user_id', 'first_name',
                                    'last_name', 'login', 'password', 'confirm_password', 'role',
                                    'permissions', 'email', 'phone'}:
                        return invalid({'changes': 'unknown_field'})
                    for key in ('first_name', 'last_name', 'login', 'password', 'confirm_password', 'email', 'phone'):
                        if key in data and not isinstance(data[key], str):
                            return invalid({key: 'invalid'})
                    if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', str(data.get('login') or '').strip()):
                        return invalid({'login': 'invalid'})
                    if (not isinstance(data.get('role', 'worker'), str)
                            or data.get('role', 'worker') not in {'admin', 'manager', 'operator', 'worker', 'master'}):
                        return invalid({'role': 'invalid'})
                    if (not isinstance(data.get('permissions', []), list)
                            or any(not isinstance(value, str) for value in data.get('permissions', []))):
                        return invalid({'permissions': 'invalid'})
                if request.headers.get('Origin','').rstrip('/') != context.origin.rstrip('/'):
                    return web.json_response({'success':False,'error':'Sorğunun mənbəyi düzgün deyil.'},status=403)
                if not context.provision_employee_account:
                    return web.json_response({'success':False,'error':'Bu funksiya əlçatan deyil.'},status=503)
                result = await asyncio.to_thread(context.provision_employee_account,tenant_id=tenant_id,owner_id=owner_id,
                    first_name=str(data.get('first_name') or ''),last_name=str(data.get('last_name') or ''),login=str(data.get('login') or ''),
                    password=str(data.get('password') or ''),role=str(data.get('role') or 'worker'),
                    permissions=data.get('permissions') if isinstance(data.get('permissions'),list) else [],password_confirm=str(data.get('confirm_password') or ''),
                    email=str(data.get('email') or ''),phone=str(data.get('phone') or ''))
                if action == 'provision_member_account':
                    return web.json_response({'success': True, 'applied': {
                        'telegram_id': result['actor_id'], 'card_version': result['card_version'], 'created': True}},
                        headers={'Cache-Control': 'no-store', 'Vary': 'Cookie'})
            else:
                raise context.platform_error("Naməlum əməliyyat.")
            rows = await asyncio.to_thread(context.list_members, tenant_id=tenant_id, owner_id=owner_id)
        except (TypeError, ValueError):
            return web.json_response({"success": False, "error": "Telegram ID düzgün deyil."}, status=400)
        except context.platform_error as exc:
            return failure(exc)
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
        source = context.source_session(request) if context.source_session else None
        if not source:
            return web.json_response({'success':False,'error':'Platforma girişi tələb olunur.'},status=401)
        try:
            data=await request.json();data=data if isinstance(data,dict) else {}
            result=await asyncio.to_thread(context.accept_email_invite,token=str(data.get('token') or ''),
                actor_id=int(profile['telegram_id']),email=str(profile['email']),display_name=str(profile.get('display_name') or ''),
                source_session=source)
            response=web.json_response({'success':True,'tenant':{'id':result['tenant_id'],'name':result['tenant_name']},'redirect':'/app'})
            if context.issue_session:
                response.set_cookie(context.cookie_name,context.issue_session(result['tenant_id'],int(profile['telegram_id']),
                    source_session=source),
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
