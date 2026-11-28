"""Read-only HTTP boundary for tenant CRM deals and sync status."""
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web
from worker_crm_capacity import CapacityUnavailable, record_read_io, session_io, with_capacity_response
from tenant_crm_request_context import require_expected_context, CRMContextError


@dataclass(frozen=True)
class DealApiContext:
    member: Callable[..., dict | None]
    policy: Callable[[dict], object]
    sync_crm: Callable[..., object]
    sync_status: Callable[[str, list[str]], list]
    list_deals: Callable[..., tuple[list, int]]
    get_deal: Callable[..., dict | None]
    platform_error: type[Exception]
    logger: object
    deal_details: Callable[..., object] | None = None


def _deal_access(context: DealApiContext, profile: dict | None) -> bool:
    if not profile:
        return False
    policy = context.policy(profile)
    return policy.kommo_access()


def handlers(context: DealApiContext) -> dict[str, Callable]:
    def context_error(request, profile):
        # member() resolves the current server session and fresh membership.
        # Intent is optional for legacy clients, but an incomplete/mismatched
        # pair must never enqueue a read for a newly switched account.
        try:
            require_expected_context(request, profile)
        except CRMContextError as exc:
            return web.json_response({'success': False, 'error': exc.code},
                                     status=exc.status, headers={'Cache-Control': 'no-store'})
        return None

    @with_capacity_response
    async def sync_status(request: web.Request) -> web.Response:
        profile = await session_io.run(context.member, request)
        policy = context.policy(profile or {})
        resources = []
        if policy.kommo_access() or policy.can_read_legacy_crm():
            resources.append("leads")
        if policy.allows("tasks"):
            resources.append("tasks")
        if not profile or not resources:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        denied = context_error(request, profile)
        if denied is not None:
            return denied
        try:
            jobs = await record_read_io.run(context.sync_status, str(profile["tenant_id"]), resources)
            return web.json_response({"success": True, "jobs": jobs})
        except CapacityUnavailable:
            raise
        except Exception:
            context.logger.error("Tenant CRM sync status unavailable")
            return web.json_response({"success": False, "error": "Sinxronizasiya vəziyyəti alınmadı."}, status=503)

    @with_capacity_response
    async def deals(request: web.Request) -> web.Response:
        profile = await session_io.run(context.member, request)
        if not _deal_access(context, profile):
            return web.json_response({"success": False, "error": "Sövdələşmələr üçün icazəniz yoxdur."}, status=403)
        denied = context_error(request, profile)
        if denied is not None:
            return denied
        should_refresh = str(request.rel_url.query.get("refresh") or "").lower() in {"1", "true", "yes"}
        sync_error = ""
        if should_refresh:
            try:
                await context.sync_crm(
                    profile, include_tasks=False,
                    force_full=str(request.rel_url.query.get("full") or "") == "1",
                )
            except context.platform_error as exc:
                sync_error = str(exc)
            except Exception:
                context.logger.exception("Tenant CRM deal sync failed: tenant=%s", profile["tenant_id"])
                sync_error = "Kommo məlumatları yenilənmədi. Son saxlanmış siyahı göstərilir."
        values = request.rel_url.query
        try:
            pipeline_ids = [int(value) for value in values.getall("pipeline_id", [])]
            status_ids = [int(value) for value in values.getall("status_id", [])]
            limit, offset = int(values.get("limit") or 100), int(values.get("offset") or 0)
        except ValueError:
            return web.json_response({"success": False, "error": "Filtr düzgün deyil."}, status=400)
        rows, total = await record_read_io.run(
            context.list_deals, tenant_id=profile["tenant_id"], search=str(values.get("search") or ""),
            pipeline_ids=pipeline_ids, status_ids=status_ids, limit=limit, offset=offset,
            scope=context.policy(profile).kommo_pipeline_scope(),
            creator_id=context.policy(profile).kommo_user_id(),
        )
        return web.json_response({"success": True, "deals": rows, "total": total,
                                  "stale": bool(sync_error), "warning": sync_error})

    @with_capacity_response
    async def deal(request: web.Request) -> web.Response:
        profile = await session_io.run(context.member, request)
        if not _deal_access(context, profile):
            return web.json_response({"success": False, "error": "Sövdələşmə üçün icazəniz yoxdur."}, status=403)
        denied = context_error(request, profile)
        if denied is not None:
            return denied
        try:
            lead_id = int(request.rel_url.query.get("lead_id") or 0)
        except ValueError:
            lead_id = 0
        if lead_id <= 0:
            return web.json_response({"success": False, "error": "Sövdələşmə seçilməyib."}, status=400)
        row = await record_read_io.run(
            context.get_deal, tenant_id=profile["tenant_id"], kommo_lead_id=lead_id)
        if not context.policy(profile).can_access_kommo_deal(row):
            return web.json_response({"success": False, "error": "Sövdələşmə seçilmiş siyahıda deyil."}, status=404)
        if context.deal_details:
            if request.rel_url.query.get('details') == '1':
                details = await context.deal_details(profile, row)
                return web.json_response({'success': True, 'details': details}, headers={'Cache-Control': 'no-store'})
            from tenant_kommo_deal_details import snapshot_fields
            row = {**row, **snapshot_fields(row)}
        return web.json_response({"success": True, "deal": row}, headers={'Cache-Control': 'no-store'})

    return {"sync_status": sync_status, "deals": deals, "deal": deal}
