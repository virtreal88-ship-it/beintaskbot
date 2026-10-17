"""Tenant-isolated operator dialog projection and local display-name overrides."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_operator_dialogs import listing,history,save_display_name


def handler(member_from_request,origin,logger,resource):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            query=request.query
            if request.method=='GET':
                if resource=='dialogs':
                    result=await asyncio.to_thread(listing,profile,query.get('search',''),query.get('channel',''),query.get('agent',''),query.get('state',''),query.get('offset',0))
                elif resource=='history':
                    result=await asyncio.to_thread(history,profile,query.get('provider',''),query.get('lead_id'),query.get('channel_id',''),query.get('conversation_id',''),query.get('before'))
                else:
                    raise TenantLinearError('Метод не поддерживается.',405)
            else:
                _origin(request,origin)
                if resource!='display_name' or request.content_type!='application/json':
                    raise TenantLinearError('Метод не поддерживается.',405)
                if request.content_length is not None and request.content_length>20_000:
                    raise TenantLinearError('Слишком большой запрос.',413)
                raw=await request.read()
                if len(raw)>20_000: raise TenantLinearError('Слишком большой запрос.',413)
                data=json.loads(raw)
                if not isinstance(data,dict): raise TenantLinearError('Некорректный запрос.')
                _expected(data,profile)
                payload={key:value for key,value in data.items() if key not in {'expected_tenant_id','expected_user_id'}}
                result=await asyncio.to_thread(save_display_name,profile,payload)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (TypeError,ValueError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            logger.exception('Operator dialog read failed')
            return web.json_response({'success':False,'error':'Диалоги временно недоступны.'},status=503,headers=headers)
    return handle
