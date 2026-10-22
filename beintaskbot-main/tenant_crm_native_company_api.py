"""Tenant-authorized native customer company API."""
import asyncio
from datetime import date,datetime
from uuid import UUID
from aiohttp import web
from tenant_crm_native_companies import create_company,get_company,list_companies,update_company,archive_company,link_entity,unlink_entity
from tenant_request_origin import require_json_post
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy


def _jsonable(v):
    if isinstance(v,UUID):return str(v)
    if isinstance(v,(date,datetime)):return v.isoformat()
    if isinstance(v,dict):return {k:_jsonable(x) for k,x in v.items()}
    if isinstance(v,list):return [_jsonable(x) for x in v]
    return v


def handler(member_from_request,origin,logger):
    async def handle(request):
        profile=member_from_request(request)
        if not profile:
            return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
        policy=TenantPolicy(profile)
        tenant=str(profile['tenant_id']); ident=request.match_info.get('id')
        if ident:
            try:UUID(ident)
            except (TypeError,ValueError):return web.json_response({'success':False,'error':'invalid_id'},status=400)
        try:
            if request.method=='GET':
                if not policy.allows_action('customers','view'):
                    return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
                result=await asyncio.to_thread(get_company,tenant,ident) if ident else await asyncio.to_thread(list_companies,tenant,request.query.get('search',''),50)
                if ident and result is None:return web.json_response({'success':False,'error':'not_found'},status=404)
                return web.json_response({'success':True,('company' if ident else 'companies'):_jsonable(result)},headers={'Cache-Control':'no-store'})
            require_json_post(request,origin); body=await request.json()
            if not isinstance(body,dict):return web.json_response({'success':False,'error':'invalid_body'},status=400)
            permission_action=('delete' if body.get('action') in ('archive','unlink_contact','unlink_deal') else
                               'assign' if body.get('action') in ('link_contact','link_deal') else
                               'create' if not ident else 'update')
            if not policy.allows_action('customers',permission_action):
                return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
            if body.get('action')=='archive' and ident:
                result=await asyncio.to_thread(archive_company,tenant,ident)
            elif body.get('action') in ('link_contact','link_deal') and ident:
                result=await asyncio.to_thread(link_entity,tenant,ident,'contact' if body['action']=='link_contact' else 'deal',body.get('entity_id'))
            elif body.get('action') in ('unlink_contact','unlink_deal') and ident:
                result=await asyncio.to_thread(unlink_entity,tenant,ident,'contact' if body['action']=='unlink_contact' else 'deal',body.get('entity_id'))
            elif ident and body.get('action')=='update':
                result=await asyncio.to_thread(update_company,tenant,ident,body.get('name'),body.get('phone',''),body.get('email',''),body.get('description',''),body.get('responsible_id'))
            elif not ident:
                result=await asyncio.to_thread(create_company,tenant,body.get('name'),body.get('phone',''),body.get('email',''),body.get('description',''),body.get('responsible_id'),company_id=body.get('id'))
            else:return web.json_response({'success':False,'error':'method_not_allowed'},status=405)
            if result is None or result is False:return web.json_response({'success':False,'error':'not_found'},status=404)
            return web.json_response({'success':True,'data':_jsonable(result)},headers={'Cache-Control':'no-store'})
        except TenantLinearError:return web.json_response({'success':False,'error':'invalid_origin'},status=403)
        except (ValueError,TypeError):return web.json_response({'success':False,'error':'invalid_company'},status=400)
        except Exception:
            logger.exception('Native company operation failed tenant=%s',tenant)
            return web.json_response({'success':False,'error':'company_operation_failed'},status=503)
    return handle
