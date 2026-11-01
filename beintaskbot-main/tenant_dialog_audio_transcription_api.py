"""Ordinary authenticated single-audio action; GET never starts work."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _expected,_origin,_json_dumps
from tenant_linear_policy import TenantLinearError
import tenant_dialog_audio_transcription as flow


def handler(member,origin,logger):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        scope_verified=False
        try:
            profile=member(request)
            if not profile:raise flow.AudioError('access_denied','Нет доступа.',403)
            if request.method in {'GET','HEAD'}:
                _expected({'expected_tenant_id':request.headers.get('X-Tenant-Id'),'expected_user_id':request.headers.get('X-User-Id')},profile)
                scope_verified=True
                result=await asyncio.to_thread(flow.preview,profile,dict(request.query))
            else:
                _origin(request,origin)
                if request.content_type!='application/json':raise flow.AudioError('json_required','Ожидается JSON.',415)
                raw=await request.read()
                if len(raw)>16000:raise flow.AudioError('request_too_large','Слишком большой запрос.',413)
                data=json.loads(raw)
                if not isinstance(data,dict):raise ValueError()
                _expected(data,profile)
                result=await asyncio.to_thread(flow.execute,profile,{k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}})
            return web.json_response({'success':True,'result':result},headers=headers,dumps=_json_dumps)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),'code':getattr(error,'code','action_unavailable'),
                **({'scope_verified':True} if scope_verified and error.status==409 else {})},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.','code':'invalid_request'},status=400,headers=headers)
        except Exception:
            logger.error('Dialog audio action unavailable')
            return web.json_response({'success':False,'error':'Обновите статус попытки. Автоматического повтора ASR не будет.','code':'audio_action_unavailable'},status=503,headers=headers)
    return handle
