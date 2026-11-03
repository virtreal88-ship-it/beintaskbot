"""Read-only HTTP boundary for tenant CRM deals and sync status."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web
from tenant_crm_list_states import query as list_query,read as read_list,current_profile,CRMListError


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


def _deal_access(context: DealApiContext, profile: dict | None) -> bool:
    if not profile:
        return False
    policy = context.policy(profile)
    return policy.allows("deals") or policy.allows("customers")


def handlers(context: DealApiContext) -> dict[str, Callable]:
    def list_error(exc):
        return web.json_response({'success':False,'error':exc.code,'code':exc.code},status=exc.status,
                                 headers={'Cache-Control':'no-store','Vary':'Cookie'})

    async def intent(request,profile,*,exact=False):
        values=request.rel_url.query
        if not any(key in values for key in ('listing','read','expected_tenant_id','expected_user_id')):
            return profile,None
        params=list_query(request,profile)
        if (exact and params.get('listing')) or (not exact and params.get('read')):
            raise CRMListError('crm_invalid_query')
        if params.get('listing') and 'lead_id' in params:
            raise CRMListError('crm_invalid_query')
        if exact and not params.get('lead_id'):
            raise CRMListError('crm_invalid_query')
        return profile,params
    async def sync_status(request: web.Request) -> web.Response:
        profile = context.member(request)
        policy = context.policy(profile or {})
        resources = []
        if policy.allows("deals") or policy.allows("customers") or policy.task_scope().get("kind") == "pipelines":
            resources.append("leads")
        if policy.allows("tasks"):
            resources.append("tasks")
        if not profile or not resources:
            return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
        try:
            jobs = await asyncio.to_thread(context.sync_status, str(profile["tenant_id"]), resources)
            return web.json_response({"success": True, "jobs": jobs})
        except Exception:
            context.logger.error("Tenant CRM sync status unavailable")
            return web.json_response({"success": False, "error": "Sinxronizasiya vəziyyəti alınmadı."}, status=503)

    async def deals(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile:
            return list_error(CRMListError('crm_forbidden',403))
        try:
            profile,params=await intent(request,profile)
            if params is not None:
                refreshing=str(request.rel_url.query.get('refresh') or '').lower() in {'1','true','yes'}
                if not refreshing:
                    result=await asyncio.to_thread(read_list,profile,entity='deal',source='saved',
                        search=params.get('search',''),pipeline_ids=params['pipeline_ids'],
                        status_ids=params['status_ids'],limit=params.get('limit'),offset=params.get('offset',0))
                    return web.json_response({'success':True,**result},headers={'Cache-Control':'no-store','Vary':'Cookie'})
                profile=await asyncio.to_thread(current_profile,profile)
        except CRMListError as exc:
            return list_error(exc)
        except Exception:
            context.logger.exception('CRM deal list read unavailable')
            return list_error(CRMListError('crm_unavailable',503))
        if not _deal_access(context, profile):
            return web.json_response({"success": False, "error": "Sövdələşmələr üçün icazəniz yoxdur."}, status=403)
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
        rows, total = await asyncio.to_thread(
            context.list_deals, tenant_id=profile["tenant_id"], search=str(values.get("search") or ""),
            pipeline_ids=pipeline_ids, status_ids=status_ids, limit=limit, offset=offset,
            scope=context.policy(profile).pipeline_scope(),
        )
        return web.json_response({"success": True, "deals": rows, "total": total,
                                  "stale": bool(sync_error), "warning": sync_error})

    async def deal(request: web.Request) -> web.Response:
        profile = context.member(request)
        if not profile:
            return list_error(CRMListError('crm_forbidden',403))
        try:
            profile,params=await intent(request,profile,exact=True)
            if params is not None:
                result=await asyncio.to_thread(read_list,profile,entity='deal',source='saved',identifier=params['lead_id'])
                return web.json_response({'success':True,**result},headers={'Cache-Control':'no-store','Vary':'Cookie'})
        except CRMListError as exc:
            return list_error(exc)
        except Exception:
            context.logger.exception('CRM deal detail read unavailable')
            return list_error(CRMListError('crm_unavailable',503))
        if not _deal_access(context, profile):
            return web.json_response({"success": False, "error": "Sövdələşmə üçün icazəniz yoxdur."}, status=403)
        try:
            lead_id = int(request.rel_url.query.get("lead_id") or 0)
        except ValueError:
            lead_id = 0
        if lead_id <= 0:
            return web.json_response({"success": False, "error": "Sövdələşmə seçilməyib."}, status=400)
        row = await asyncio.to_thread(
            context.get_deal, tenant_id=profile["tenant_id"], kommo_lead_id=lead_id)
        if not context.policy(profile).can_access_deal(row):
            return web.json_response({"success": False, "error": "Sövdələşmə seçilmiş siyahıda deyil."}, status=404)
        return web.json_response({"success": True, "deal": row})

    return {"sync_status": sync_status, "deals": deals, "deal": deal}
