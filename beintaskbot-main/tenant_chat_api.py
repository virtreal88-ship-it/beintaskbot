"""Read-only HTTP boundary for a tenant-scoped cached CRM chat."""
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web
from worker_crm_capacity import dialog_io, session_io, with_capacity_response


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
    if policy.kommo_access():
        return True
    if policy.action_scope("dialogs","view") is not None:
        return policy.action_scope("dialogs","view")!="none"
    return policy.can_read_legacy_crm()


def _lifecycle_active(profile):
    from tenant_platform import _connect
    from tenant_dialog_rollout import guard
    with _connect() as conn:
        with conn.cursor() as cur:
            return guard(cur,profile['tenant_id']).enforced


def _legacy_messages_guarded(context, profile, lead_id):
    """Keep legacy output unchanged, but serialize its final read with activation.

    The earlier mode check only selects presentation. If activation won in
    between, reauthorize through the current transactional reader instead.
    """
    from tenant_platform import _connect
    from tenant_dialog_rollout import guard
    with _connect() as conn:
        with conn.cursor() as cur:
            if guard(cur,profile['tenant_id']).enforced:
                from tenant_chat_history_store import page
                return page(profile,lead_id)
            return {'messages':context.list_messages(tenant_id=profile['tenant_id'],kommo_lead_id=lead_id)}


def handlers(context: ChatApiContext) -> dict[str, Callable]:
    from tenant_chat_recent import handler
    recent = handler(context)
    @with_capacity_response
    async def chat(request: web.Request) -> web.Response:
        profile = await session_io.run(context.member, request)
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
        if request.rel_url.query.get('recent') == '1':
            return await recent(request, lead_id)
        from tenant_linear_policy import TenantLinearError
        try:
            lifecycle_active=await dialog_io.run(_lifecycle_active,profile)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status)
        if lifecycle_active or context.policy(profile).kommo_access() or context.policy(profile).action_scope('dialogs','view') is not None:
            # The old lead-only reader cannot inherit a deal grant to bypass
            # source restrictions. Reuse the current transactional page reader.
            from tenant_chat_history_store import page
            from tenant_linear_policy import TenantLinearError
            try:
                result=await dialog_io.run(page,profile,lead_id)
                warning = ''
                if str(request.rel_url.query.get('refresh') or '').casefold() in {'1','true','yes'}:
                    deal = await dialog_io.run(context.get_deal, tenant_id=profile['tenant_id'], kommo_lead_id=lead_id)
                    if context.policy(profile).can_read_kommo_chat(deal):
                        try:
                            await context.sync_chat(profile,lead_id)
                        except context.platform_error as error:
                            warning = str(error)
                        except Exception:
                            context.logger.exception('Kommo chat refresh failed')
                            warning = 'Çat tarixçəsi yenilənmədi. Son saxlanmış tarixçə göstərilir.'
                        # Re-authorize after the provider call, including membership revocation.
                        result=await dialog_io.run(page,profile,lead_id)
                return web.json_response({'success':True,**result,'stale':bool(warning),'warning':warning})
            except TenantLinearError as error:
                return web.json_response({'success':False,'error':str(error)},status=error.status)
        deal = await dialog_io.run(
            context.get_deal, tenant_id=profile["tenant_id"], kommo_lead_id=lead_id)
        if not context.policy(profile).can_access_deal(deal):
            return web.json_response(
                {"success": False, "error": "Sövdələşmə seçilmiş siyahıda deyil."}, status=404)
        raw = deal.get("raw") if isinstance(deal, dict) else None
        connect_native = isinstance(raw, dict) and raw.get("source") == "connect"
        warning = ""
        if (not connect_native and
                str(request.rel_url.query.get("refresh") or "").casefold() in {"1", "true", "yes"}):
            try:
                await context.sync_chat(profile, lead_id)
            except context.platform_error as exc:
                warning = str(exc)
            except Exception:
                context.logger.exception(
                    "Tenant chat sync failed: tenant=%s lead=%s", profile["tenant_id"], lead_id)
                warning = "Çat tarixçəsi yenilənmədi. Son saxlanmış tarixçə göstərilir."
        from tenant_linear_policy import TenantLinearError
        try:
            result = await dialog_io.run(_legacy_messages_guarded,context,profile,lead_id)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status)
        return web.json_response(
            {"success": True, **result, "stale": bool(warning), "warning": warning})

    return {"chat": chat}
