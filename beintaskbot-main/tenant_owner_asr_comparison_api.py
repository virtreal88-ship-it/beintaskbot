"""Authenticated UI action for one bounded owner comparison; no public grants."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
import tenant_owner_asr_comparison as comparison


def handler(member,origin,logger):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            if request.method in {'GET','HEAD'}:
                _expected({'expected_tenant_id':request.headers.get('X-Tenant-Id'),'expected_user_id':request.headers.get('X-User-Id')},profile)
                result=await asyncio.to_thread(comparison.preview,profile,dict(request.query))
            else:
                _origin(request,origin)
                if request.content_type!='application/json':raise TenantLinearError('Ожидается JSON.',415)
                raw=await request.read()
                if len(raw)>4000:raise TenantLinearError('Слишком большой запрос.',413)
                data=json.loads(raw)
                if not isinstance(data,dict):raise TenantLinearError('Некорректный запрос.',400)
                _expected(data,profile)
                payload={k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}}
                result=await asyncio.to_thread(comparison.execute,profile,payload)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос сравнения.'},status=400,headers=headers)
        except Exception:
            # No source/provider/error body or credential is logged or returned.
            logger.error('Owner ASR comparison action unavailable')
            return web.json_response({'success':False,'error':'Сравнение временно недоступно. Обновите статус; повторных ASR-вызовов не будет.'},status=503,headers=headers)
    return handle
