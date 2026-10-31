"""Authenticated explicit consultation actions. No content-triggered dispatch."""
import asyncio
from aiohttp import web
from tenant_ai_agents_api import _origin,_expected
from tenant_linear_policy import TenantLinearError
import tenant_dialog_consultations as flow


def handler(member,origin,logger,resource):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            if request.method=='GET':
                if 'X-Tenant-Id' in request.headers or 'X-User-Id' in request.headers:
                    _expected({'expected_tenant_id':request.headers.get('X-Tenant-Id'),'expected_user_id':request.headers.get('X-User-Id')},profile)
                if resource=='customer_replies':result=await asyncio.to_thread(flow.customer_replies,profile,request.match_info['id'])
                elif resource=='destinations':result=await asyncio.to_thread(flow.destinations,profile)
                else:result=await asyncio.to_thread(flow.inbox,profile,request.query.get('channel_id',''),request.query.get('conversation_id',''))
            else:
                _origin(request,origin)
                if request.content_type!='application/json':raise TenantLinearError('Ожидается JSON.',415)
                raw=await request.read()
                if len(raw)>20000:raise TenantLinearError('Слишком большой запрос.',413)
                import json
                data=json.loads(raw)
                if not isinstance(data,dict):raise TenantLinearError('Некорректный запрос.',400)
                _expected(data,profile)
                payload={k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}}
                key=request.match_info.get('id')
                if resource=='developer_marker':result=await asyncio.to_thread(flow.developer_marker,profile,payload)
                elif resource=='prepare':result=await asyncio.to_thread(flow.prepare,profile,payload,origin)
                elif resource=='preview':result=await asyncio.to_thread(flow.preview,profile,payload)
                elif resource=='dispatch':
                    if set(payload)!={'request_id'}:raise TenantLinearError('Некорректный запрос.',400)
                    result=await asyncio.to_thread(flow.dispatch,profile,key,payload['request_id'])
                elif resource=='revoke':
                    if payload:raise TenantLinearError('Некорректный запрос.',400)
                    result=await asyncio.to_thread(flow.revoke,profile,key)
                elif resource=='draft':result=await asyncio.to_thread(flow.draft,profile,key,payload)
                else:result=await asyncio.to_thread(flow.action,profile,key,payload)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as exc:return web.json_response({'success':False,'error':str(exc)},status=exc.status,headers=headers)
        except (ValueError,TypeError,KeyError):return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            logger.error('Dialogue consultation action failed')
            return web.json_response({'success':False,'error':'Консультация временно недоступна.'},status=503,headers=headers)
    return handle
