"""Browser membership API and narrow outbound-gateway HTTPS protocol."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _origin, _expected
from tenant_linear_policy import TenantLinearError
from tenant_call_recording_bundle import MAX_BUNDLE
import tenant_browser_calls as store

HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}
SLOTS=asyncio.Semaphore(4)


async def body(request,limit):
    if request.content_length is None or not 0<request.content_length<=limit:
        raise TenantLinearError('request_size_invalid',413)
    chunks=[];size=0
    async for part in request.content.iter_chunked(65536):
        size+=len(part)
        if size>limit:raise TenantLinearError('request_size_invalid',413)
        chunks.append(part)
    return b''.join(chunks)


def handler(member,origin,logger,resource='browser'):
    async def handle(request):
        try:
            if SLOTS.locked():raise TenantLinearError('Звонки заняты. Повторите позже.',429)
            if resource in {'sync','recording'}:
                if request.headers.get('Origin') or request.headers.get('Cookie'):raise TenantLinearError('gateway_access_denied',403)
                cfg=store.gateway_auth(request.headers.get('Authorization',''))
                async with SLOTS:
                    raw=await body(request,MAX_BUNDLE if resource=='recording' else 128*1024)
                    if resource=='recording':
                        if request.content_type!='application/zip':raise TenantLinearError('recording_type_invalid',400)
                        result=await asyncio.to_thread(store.save_recording,cfg,request.query.get('call_id'),raw)
                    else:
                        result=await asyncio.to_thread(store.sync,cfg,json.loads(raw))
            else:
                profile=member(request)
                if not profile:raise TenantLinearError('Войдите в программу.',403)
                _expected(request.query,profile)
                async with SLOTS:
                    if request.method=='GET':result=await asyncio.to_thread(store.status,profile,request.query.get('request_id'),request.query.get('heartbeat')!='0')
                    else:
                        _origin(request,origin)
                        result=await asyncio.to_thread(store.action,profile,json.loads(await body(request,28000)))
            return web.json_response({'result':result},headers=HEADERS)
        except TenantLinearError as error:
            return web.json_response({'error':str(error)},status=error.status,headers=HEADERS)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'error':'call_request_invalid'},status=400,headers=HEADERS)
        except Exception:
            logger.warning('browser_call_request_failed')
            return web.json_response({'error':'Не удалось подтвердить действие звонка.'},status=503,headers=HEADERS)
    return handle
