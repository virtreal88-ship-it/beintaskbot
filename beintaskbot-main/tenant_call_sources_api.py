"""Owner source pairing and authenticated same-origin PCM relay."""
import asyncio
import json
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from tenant_ai_agents_api import _expected, _origin
from tenant_browser_calls_api import HEADERS, body
from tenant_linear_policy import TenantLinearError
import tenant_call_sources as store


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
            async def transfer(source,target):
                start=asyncio.get_running_loop().time();size=0
                async for message in source:
                    if message.type!=WSMsgType.BINARY or not message.data or len(message.data)>6400 or len(message.data)%2:break
                    now=asyncio.get_running_loop().time()
                    if now-start>=1:start=now;size=0
                    size+=len(message.data)
                    if size>65536:break
                    await asyncio.wait_for(target.send_bytes(message.data),2)
            async def recheck():
                while True:
                    await asyncio.sleep(3)
                    fresh=member(request)
                    if not fresh or any(str(fresh.get(k))!=v for k,v in identity.items()):return
                    if await asyncio.to_thread(store.audio_authority,fresh,call,nonce)!=authority:return
            tasks=[asyncio.create_task(transfer(socket,upstream)),asyncio.create_task(transfer(upstream,socket)),asyncio.create_task(recheck())]
            try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED,timeout=130)
            finally:
                for task in tasks:task.cancel()
                results=await asyncio.gather(*tasks,return_exceptions=True)
                if any(isinstance(r,Exception) for r in results):logger.info('call_audio_relay_closed')
                await socket.close()
            return socket
