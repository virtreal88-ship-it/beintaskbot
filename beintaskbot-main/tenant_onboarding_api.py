"""Owner-only onboarding HTTP handlers with explicit, replaceable adapters."""
import asyncio
import json
import logging
from collections.abc import Callable, Awaitable
from aiohttp import web

SYSTEM_PROMPT = (
    "Sən CRM Smart Assistant üçün ilkin quraşdırma köməkçisisən. Azərbaycan dilində qısa, anlayışlı cavab ver. "
    "İstifadəçinin sözlərindən yalnız bu JSON sahələrini təklif et: company_name, industry, team, sources, pipeline, "
    "modules, notifications, notes, completed_steps. Heç vaxt giriş məlumatı, token və ya parol istəmə. "
    'Cavabı yalnız JSON kimi qaytar: {"reply":"...","patch":{...}}. Reply yalnız bir növbəti, qısa sual olsun; '
    "bir cavabda çox sual vermə. İlk cavabdan company_name və industry-ni çıxarmağa çalış. "
    "modules yalnız deals,tasks,customers,hot_orders,finance boolean açarlarından; notifications yalnız new_lead,incoming_message,task_assigned,task_overdue boolean açarlarından ibarət olsun."
)


def safe_patch(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    keys = ("company_name", "industry", "team", "sources", "pipeline", "modules",
            "notifications", "notes", "completed_steps")
    return {key: value[key] for key in keys if key in value}


def fallback(message: str, current: dict | None = None) -> tuple[str, dict]:
    text = str(message or "").strip()
    onboarding = current if isinstance(current, dict) else {}
    if not onboarding.get("team"):
        question = "Əla. Komandada kimlər işləyir: menecerlər, satış əməkdaşları və ya ustalar? Təxminən neçə nəfərdir?"
    elif not onboarding.get("sources"):
        question = "Müştəriləriniz əsasən haradan yazır: WhatsApp, Instagram, TikTok, sayt, telefon və ya başqa kanal?"
    else:
        question = "Başa düşdüm. Hansı müraciətləri daha tez görmək istəyirsiniz: yeni satış, təkrar müraciət, yoxsa təcili sifariş?"
    return question, {"notes": [text] if text else []}


def handlers(
    member: Callable[..., dict | None],
    ask: Callable[[str, str], str],
    update: Callable[..., dict],
    finish: Callable[..., dict],
    platform_error: type[Exception],
    logger: logging.Logger,
) -> tuple[Callable[[web.Request], Awaitable[web.Response]],
           Callable[[web.Request], Awaitable[web.Response]]]:
    async def assistant(request: web.Request) -> web.Response:
        profile = member(request, owner_only=True)
        if not profile:
            return web.json_response({"success": False, "error": "Yalnız şirkət sahibi quraşdırmanı dəyişə bilər."}, status=403)
        try:
            data = await request.json()
        except Exception:
            data = {}
        message = str((data or {}).get("message") or "").strip()[:2500]
        if not message:
            return web.json_response({"success": False, "error": "Mesajı yazın."}, status=400)
        current = profile.get("onboarding") if isinstance(profile.get("onboarding"), dict) else {}
        prompt = f"Mövcud quraşdırma: {json.dumps(current, ensure_ascii=False)}\nİstifadəçinin cavabı: {message}"
        reply, patch = fallback(message, current)
        try:
            raw = await asyncio.to_thread(ask, SYSTEM_PROMPT, prompt)
            parsed = json.loads(raw[raw.find("{"):raw.rfind("}") + 1]) if "{" in raw and "}" in raw else {}
            if isinstance(parsed, dict):
                reply = str(parsed.get("reply") or reply).strip()[:1200]
                patch = safe_patch(parsed.get("patch")) or patch
        except Exception:
            # Provider exceptions can contain credentials or private setup text.
            logger.warning("Platform onboarding AI fallback used")
        try:
            tenant = await asyncio.to_thread(update, tenant_id=profile["tenant_id"], owner_id=int(profile["telegram_id"]), patch=patch)
        except platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        return web.json_response({"success": True, "reply": reply, "patch": patch, "tenant": tenant})

    async def complete(request: web.Request) -> web.Response:
        profile = member(request, owner_only=True)
        if not profile:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        try:
            tenant = await asyncio.to_thread(finish, tenant_id=profile["tenant_id"], owner_id=int(profile["telegram_id"]))
        except platform_error as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)
        return web.json_response({"success": True, "tenant": tenant})

    return assistant, complete
