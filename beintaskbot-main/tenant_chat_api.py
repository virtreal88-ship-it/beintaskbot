"""Read-only HTTP boundary for a tenant-scoped cached CRM chat."""
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web
from tenant_crm_list_states import query as list_query,read as read_list,current_profile,CRMListError


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
        if not profile:
            return web.json_response({'success':False,'error':'crm_forbidden','code':'crm_forbidden'},
                                     status=403,headers={'Cache-Control':'no-store','Vary':'Cookie'})
        try:
            values=request.rel_url.query
            if any(key in values for key in ('listing','read','expected_tenant_id','expected_user_id')):
                params=list_query(request,profile)
                if params.get('listing') or not params.get('lead_id'):
                    raise CRMListError('crm_invalid_query')
                result=await asyncio.to_thread(read_list,profile,entity='deal',source='saved',
                    identifier=params['lead_id'],chat=True)
                refreshing=str(values.get('refresh') or '').casefold() in {'1','true','yes'}
                if refreshing:
                    # This is the existing, explicitly requested provider
                    # refresh. Ordinary read=1 never reaches this branch.
                    fresh=await asyncio.to_thread(current_profile,profile)
                    exact=await asyncio.to_thread(read_list,fresh,entity='deal',source='saved',identifier=params['lead_id'])
                    if not _can_read(context,fresh) or not context.policy(fresh).can_access_deal(exact['deal']):
                        raise CRMListError('crm_forbidden',403)
                    warning=''
                    if exact['deal'].get('provenance')!='connect':
                        try:
                            await context.sync_chat(fresh,int(params['lead_id']))
                        except context.platform_error as exc:
                            warning=str(exc)
                        except Exception:
                            context.logger.exception('Explicit CRM chat refresh failed: tenant=%s',fresh['tenant_id'])
                            warning='Çat tarixçəsi yenilənmədi. Son saxlanmış tarixçə göstərilir.'
                    result=await asyncio.to_thread(read_list,fresh,entity='deal',source='saved',
                        identifier=params['lead_id'],chat=True)
                    result.update(stale=bool(warning),warning=warning)
                return web.json_response({'success':True,**result},headers={'Cache-Control':'no-store','Vary':'Cookie'})
        except CRMListError as exc:
            return web.json_response({'success':False,'error':exc.code,'code':exc.code},status=exc.status,
                                     headers={'Cache-Control':'no-store','Vary':'Cookie'})
        except Exception:
            context.logger.exception('CRM cached chat read unavailable')
            return web.json_response({'success':False,'error':'crm_unavailable','code':'crm_unavailable'},
                                     status=503,headers={'Cache-Control':'no-store','Vary':'Cookie'})
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
        rows = await asyncio.to_thread(
            context.list_messages, tenant_id=profile["tenant_id"], kommo_lead_id=lead_id)
        return web.json_response(
            {"success": True, "messages": rows, "stale": bool(warning), "warning": warning})

    return {"chat": chat}
