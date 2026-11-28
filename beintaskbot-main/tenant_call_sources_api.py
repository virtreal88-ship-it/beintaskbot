"""Owner source pairing and authenticated same-origin PCM relay."""
import asyncio
import json
import time
from tenant_call_diagnostics import Arrivals, CLOSE_CODES, save_relay
from asyncio import wait as _wait_relay_tasks
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from tenant_ai_agents_api import _expected, _origin
from tenant_browser_calls import MANAGED_CALL_MAX_SECONDS
from tenant_browser_calls_api import HEADERS, body
from tenant_linear_policy import TenantLinearError
import tenant_call_sources as store


# The managed gateway ends the call first; this is a transport backstop,
# independent of the much shorter recording cap.
MANAGED_AUDIO_RELAY_MAX_SECONDS = MANAGED_CALL_MAX_SECONDS + 10


def close_evidence(socket):
    """Fixed transport enums only; never log exception text or peer payloads."""
    code=getattr(socket,'close_code',None)
    # aiohttp can return a WSCloseCode IntEnum. Persist its plain integer value.
    code=int(code) if isinstance(code,int) and code in CLOSE_CODES else 0
    error=socket.exception()
    kind='timeout' if isinstance(error,asyncio.TimeoutError) else 'connection_lost' if isinstance(error,ConnectionError) else 'transport_error' if error is not None else 'none'
    return code,kind


def handler(member,origin,logger,resource='source'):
    async def handle(request):
        try:
            profile=member(request)
            if not profile:raise TenantLinearError('Войдите в программу.',403)
            _expected(request.query,profile)
            if resource=='audio':
                if request.headers.get('Origin','').rstrip('/')!=origin.rstrip('/'):
                    raise TenantLinearError('call_origin_denied',403)
                return await relay(request,profile,member,logger)
            channel=request.match_info['channel_id']
            if request.method=='POST':
                _origin(request,origin)
                data=json.loads(await body(request,1024))
                if data!={'confirm_source_pairing':True}:
                    raise TenantLinearError('Подтвердите подключение звонков к выбранному номеру.',400)
                result=await asyncio.to_thread(store.provision,profile,channel)
            else:result=await asyncio.to_thread(store.source_status,profile,channel)
            return web.json_response({'result':result},headers=HEADERS)
        except TenantLinearError as e:
            return web.json_response({'error':str(e)},status=e.status,headers=HEADERS)
        except (ValueError,KeyError,TypeError):
            return web.json_response({'error':'call_source_request_invalid'},status=400,headers=HEADERS)
        except Exception:
            logger.warning('call_source_request_failed')
            return web.json_response({'error':'Сервер звонков временно недоступен.'},status=503,headers=HEADERS)
    return handle


async def relay(request,profile,member,logger):
    identity={k:str(profile.get(k)) for k in ('tenant_id','telegram_id')}
    call=request.match_info['call_id'];nonce=request.query.get('request_id')
    authority=await asyncio.to_thread(store.audio_authority,profile,call,nonce)
    gateway,provider,call=authority
    base,token=store.service()
    async with ClientSession(timeout=ClientTimeout(total=None,sock_connect=5),trust_env=False) as client:
        async with client.ws_connect(base+'/v1/sources/'+gateway+'/audio/'+provider+'/'+call,
                headers={'Authorization':'Bearer '+token},max_msg_size=6400,compress=0,heartbeat=10) as upstream:
            # Check again after network setup, before browser audio is admitted.
            if await asyncio.to_thread(store.audio_authority,profile,call,nonce)!=authority:
                raise TenantLinearError('call_identity_changed',409)
            socket=web.WebSocketResponse(max_msg_size=6400,compress=False,heartbeat=10)
            socket.headers.update(HEADERS)
            await socket.prepare(request)
            received={'browser':False,'provider':False}
            arrivals={'browser':Arrivals(),'provider':Arrivals()}
            async def transfer(source,target,side):
                start=asyncio.get_running_loop().time();size=0
                async for message in source:
                    if message.type!=WSMsgType.BINARY:return side+'_closed'
                    if not message.data or len(message.data)>6400 or len(message.data)%2:return side+'_invalid_pcm'
                    received[side]=True
                    now=asyncio.get_running_loop().time()
                    arrivals[side].add(now)
                    if now-start>=1:start=now;size=0
                    size+=len(message.data)
                    if size>65536:return side+'_rate_limit'
                    try:await asyncio.wait_for(target.send_bytes(message.data),2)
                    except asyncio.TimeoutError:return side+'_send_timeout'
                return side+'_closed'
            async def recheck():
                while True:
                    await asyncio.sleep(3)
                    fresh=member(request)
                    if not fresh or any(str(fresh.get(k))!=v for k,v in identity.items()):return 'membership_changed'
                    try:
                        if await asyncio.to_thread(store.audio_authority,fresh,call,nonce)!=authority:return 'authority_changed'
                    except TenantLinearError:return 'authority_denied'
            tasks=[asyncio.create_task(transfer(socket,upstream,'browser')),asyncio.create_task(transfer(upstream,socket,'provider')),asyncio.create_task(recheck())]
            code='relay_cancelled'
            try:
                done,_=await _wait_relay_tasks(
                    tasks,return_when=asyncio.FIRST_COMPLETED,timeout=MANAGED_AUDIO_RELAY_MAX_SECONDS)
                code='duration_limit' if not done else 'transport_error'
                for task in tasks:
                    if task in done and not task.cancelled() and task.exception() is None:
                        code=task.result();break
            finally:
                # Snapshot before cancelling peer reads: cancellation itself can
                # set an abnormal-close code and must not masquerade as cause.
                browser_code,browser_error=close_evidence(socket)
                provider_code,provider_error=close_evidence(upstream)
                for task in tasks:task.cancel()
                results=await asyncio.gather(*tasks,return_exceptions=True)
                # Only local enum codes and booleans, never exception strings,
                # call IDs, account identifiers, audio, transcripts or tokens.
                logger.info('call_audio_relay_end stage=stream code=%s input_received=%s output_received=%s',
                            code,received['browser'],received['provider'])
                logger.info('call_audio_transport_end browser_code=%s browser_error=%s provider_code=%s provider_error=%s',
                            browser_code,browser_error,provider_code,provider_error)
                evidence={'version':1,'code':code,'at_ms':int(time.time()*1000),
                          'browser_to_worker':arrivals['browser'].snapshot(),
                          'engine_to_worker':arrivals['provider'].snapshot(),
                          'browser_close':{'close_code':browser_code,'error':browser_error},
                          'engine_close':{'close_code':provider_code,'error':provider_error}}
                try:
                    await asyncio.to_thread(save_relay,profile,authority,nonce,evidence)
                except Exception:
                    logger.warning('call_diagnostics_save_failed stage=relay')
                finally:
                    await socket.close()
            return socket
