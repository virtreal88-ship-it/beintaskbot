"""Browser membership API and narrow outbound-gateway HTTPS protocol."""
import asyncio
import json
from contextlib import asynccontextmanager
from uuid import UUID

from aiohttp import web
from tenant_ai_agents_api import _origin, _expected
from tenant_linear_policy import TenantLinearError
from tenant_call_recording_bundle import MAX_BUNDLE, MAX_MANAGED_BUNDLE
import tenant_browser_calls as store

HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}
GATEWAY_SYNC_SLOTS=asyncio.Semaphore(4)
RECORDING_SLOTS=asyncio.Semaphore(1)
BROWSER_POLL_SLOTS=asyncio.Semaphore(2)
BROWSER_LIVE_SLOTS=asyncio.Semaphore(3)
BROWSER_COMMAND_SLOTS=asyncio.Semaphore(2)
BROWSER_WAIT_SLOTS=asyncio.Semaphore(4)


class CallServiceBusy(TenantLinearError):
    def __init__(self):
        super().__init__('Сервер занят. Повторите проверку звонка.', 503)


@asynccontextmanager
async def _browser_slot(slots, *, live=False):
    # Background polls and optional diagnostics cannot occupy the slots used
    # by call heartbeats or commands. Bound both the queue and the time in it.
    if slots.locked():
        if not live:
            raise TenantLinearError('Звонки заняты. Повторите позже.',429)
        if BROWSER_WAIT_SLOTS.locked():
            raise CallServiceBusy()
        async with BROWSER_WAIT_SLOTS:
            try:
                async with asyncio.timeout(1.5):
                    await slots.acquire()
            except TimeoutError:
                raise CallServiceBusy() from None
    else:
        await slots.acquire()
    try:
        yield
    finally:
        slots.release()


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
    def profile_for(request):
        profile=member(request)
        if not profile:raise TenantLinearError('Войдите в программу.',403)
        _expected(request.query,profile)
        return profile

    async def handle(request):
        try:
            if resource in {'sync','recording'}:
                if request.headers.get('Origin') or request.headers.get('Cookie'):raise TenantLinearError('gateway_access_denied',403)
                slots=RECORDING_SLOTS if resource=='recording' else GATEWAY_SYNC_SLOTS
                if slots.locked():raise TenantLinearError('Звонки заняты. Повторите позже.',429)
                async with slots:
                    cfg=await asyncio.to_thread(store.gateway_auth,request.headers.get('Authorization',''))
                    limit=(MAX_MANAGED_BUNDLE if cfg.get('managed') else MAX_BUNDLE) if resource=='recording' else 128*1024
                    raw=await asyncio.wait_for(body(request,limit),90)
                    if resource=='recording':
                        if request.content_type!='application/zip':raise TenantLinearError('recording_type_invalid',400)
                        result=await asyncio.to_thread(store.save_recording,cfg,request.query.get('call_id'),raw)
                    else:
                        result=await asyncio.to_thread(store.sync,cfg,json.loads(raw))
            else:
                if request.method=='GET':
                    request_id=request.query.get('request_id')
                    if request_id is not None and str(UUID(request_id))!=request_id:
                        raise TenantLinearError('call_identity_invalid',400)
                    slots=BROWSER_LIVE_SLOTS if request_id else BROWSER_POLL_SLOTS
                    async with _browser_slot(slots,live=bool(request_id)):
                        profile=await asyncio.to_thread(profile_for,request)
                        result=await asyncio.to_thread(store.status,profile,request_id,request.query.get('heartbeat')!='0')
                else:
                    _origin(request,origin)
                    data=json.loads(await asyncio.wait_for(body(request,28000),4))
                    if not isinstance(data,dict):
                        raise TenantLinearError('call_request_invalid',400)
                    command=data.get('op') in ('start','accept','reject','end')
                    slots=BROWSER_COMMAND_SLOTS if command else BROWSER_POLL_SLOTS
                    async with _browser_slot(slots,live=command):
                        profile=await asyncio.to_thread(profile_for,request)
                        result=await asyncio.to_thread(store.action,profile,data)
            return web.json_response({'result':result},headers=HEADERS)
        except CallServiceBusy as error:
            return web.json_response({'error':str(error),'code':'call_service_busy'},status=error.status,headers=HEADERS)
        except TenantLinearError as error:
            return web.json_response({'error':str(error)},status=error.status,headers=HEADERS)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'error':'call_request_invalid'},status=400,headers=HEADERS)
        except Exception as error:
            # Allowlisted database category only; never SQL, values or raw errors.
            code={'40P01':'db_deadlock','40001':'db_serialization','55P03':'db_lock_timeout',
                  '57014':'db_query_cancelled'}.get(getattr(error,'sqlstate',None),'internal')
            stage='status' if resource=='browser' and request.method=='GET' else resource
            logger.warning('browser_call_request_failed stage=%s code=%s',stage,code)
            return web.json_response({'error':'Не удалось подтвердить действие звонка.'},status=503,headers=HEADERS)
    return handle
