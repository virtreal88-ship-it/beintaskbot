"""Tenant-authenticated API for native deals; deliberately no provider sync."""
import asyncio
from datetime import date, datetime
from uuid import UUID
from aiohttp import web
from tenant_crm_native_deals import create_deal,get_deal,list_deals,update_deal,archive_deal,deal_options
from tenant_request_origin import require_json_post
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy


def _jsonable(value):
    if isinstance(value,UUID): return str(value)
    if isinstance(value,(date,datetime)): return value.isoformat()
    if isinstance(value,dict): return {k:_jsonable(v) for k,v in value.items()}
    if isinstance(value,list): return [_jsonable(v) for v in value]
    return value


def handler(member_from_request,origin,logger):
    async def handle(request):
        profile=member_from_request(request)
        if not profile:
            return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
        policy=TenantPolicy(profile)
        tenant_id=str(profile['tenant_id']); deal_id=request.match_info.get('id')
        if deal_id:
            try: UUID(deal_id)
            except (TypeError,ValueError): return web.json_response({'success':False,'error':'invalid_id'},status=400)
        try:
            if request.method=='GET':
                if not policy.allows_action('deals','view'):
                    return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
                if request.query.get('options')=='1':
                    result=await asyncio.to_thread(deal_options,tenant_id)
                    return web.json_response({'success':True,**_jsonable(result)},headers={'Cache-Control':'no-store'})
                result=await asyncio.to_thread(get_deal,tenant_id,deal_id) if deal_id else await asyncio.to_thread(list_deals,tenant_id,request.query.get('search',''),50)
                if deal_id and result is None: return web.json_response({'success':False,'error':'not_found'},status=404)
                return web.json_response({'success':True,'deal' if deal_id else 'deals':_jsonable(result)},headers={'Cache-Control':'no-store'})
            require_json_post(request,origin)
            data=await request.json()
            if not isinstance(data,dict): return web.json_response({'success':False,'error':'invalid_body'},status=400)
            permission_action='delete' if data.get('action')=='archive' else ('create' if not deal_id else 'update')
            if not policy.allows_action('deals',permission_action):
                return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
            if data.get('action')=='archive' and deal_id:
                result=await asyncio.to_thread(archive_deal,tenant_id,deal_id)
                return web.json_response({'success':True,'archived':result})
            fields={k:data.get(k) for k in ('title','pipeline_id','stage_id','contact_id','responsible_id','channel_id','conversation_id','description')}
            if deal_id: result=await asyncio.to_thread(update_deal,tenant_id,deal_id,**fields)
            else: result=await asyncio.to_thread(create_deal,tenant_id,**fields,deal_id=data.get('id'))
            if deal_id and result is None: return web.json_response({'success':False,'error':'not_found'},status=404)
            try:
                from tenant_crm_sync_outbox import enqueue_change
                await asyncio.to_thread(enqueue_change,tenant_id,'deal',str(result['id']))
            except Exception:
                logger.warning('CRM deal sync enqueue unavailable: tenant=%s',tenant_id)
            return web.json_response({'success':True,'deal':_jsonable(result)})
        except TenantLinearError:
            return web.json_response({'success':False,'error':'invalid_origin'},status=403,headers={'Cache-Control':'no-store'})
        except (ValueError,TypeError) as exc:
            return web.json_response({'success':False,'error':str(exc)[:120]},status=400)
        except Exception:
            logger.exception('Native CRM deal operation failed: tenant=%s',tenant_id)
            return web.json_response({'success':False,'error':'deal_operation_failed'},status=503,headers={'Cache-Control':'no-store'})
    return handle
