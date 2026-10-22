"""Authenticated testing API; virtual routes have no transport/action dependency."""
import asyncio
from aiohttp import web
from tenant_linear_policy import TenantLinearError
from tenant_ai_testing_store import create,read,listing,control
from tenant_ai_testing_service import turn


def handler(member_from_request,origin,logger,resource,request_provider=None):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            id=request.match_info.get('session_id')
            if request.method in {'GET','HEAD'}:
                if request.query.get('expected_tenant_id') and request.query['expected_tenant_id']!=str(profile['tenant_id']):raise TenantLinearError('Компания изменилась.',409)
                result=await asyncio.to_thread(read,profile,id) if id else await asyncio.to_thread(listing,profile)
            else:
                if request.headers.get('Origin','').rstrip('/')!=origin.rstrip('/') or request.content_type!='application/json':raise TenantLinearError('Недопустимый источник запроса.',403)
                data=await request.json()
                if not isinstance(data,dict):raise TenantLinearError('Некорректный запрос.')
                if str(data.pop('expected_tenant_id',''))!=str(profile['tenant_id']) or str(data.pop('expected_user_id',''))!=str(profile['telegram_id']):raise TenantLinearError('Компания или пользователь изменились.',409)
                if resource=='turn':result=await turn(profile,id,data)
                elif resource=='control':result=await asyncio.to_thread(control,profile,id,data)
                else:result=await asyncio.to_thread(create,profile,data)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError):return web.json_response({'success':False,'error':'Некорректные параметры теста.'},status=400,headers=headers)
        except web.HTTPException:raise
        except Exception:
            logger.exception('AI testing request failed')
            return web.json_response({'success':False,'error':'Тестирование временно недоступно.'},status=503,headers=headers)
    return handle
