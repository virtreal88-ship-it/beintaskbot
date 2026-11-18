"""Private source-bound call engine supervisor. No public call or audio API.

Worker is the sole control/media client. Each source has an immutable identity,
separate SQLite session, signing key and command journal on the private volume.
"""
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import uuid
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
ACCOUNT = re.compile(r'^[1-9][0-9]{5,21}@s\.whatsapp\.net$')
PROVIDER = re.compile(r'^[A-Za-z0-9_-]{5,160}$')
MAX_SOURCES = 16


def safe_uuid(value):
    if not isinstance(value,str) or not UUID.fullmatch(value):
        raise web.HTTPBadRequest(text='source_identity_invalid')
    return str(uuid.UUID(value))


def binding(value):
    if not isinstance(value,dict) or set(value)!={'tenant_id','channel_id','account_jid','gateway_token'}:
        raise web.HTTPBadRequest(text='source_binding_invalid')
    result={**value,'tenant_id':safe_uuid(value['tenant_id']),'channel_id':safe_uuid(value['channel_id'])}
    if (not isinstance(result['account_jid'],str) or not ACCOUNT.fullmatch(result['account_jid'])
            or not isinstance(result['gateway_token'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',result['gateway_token'])):
        raise web.HTTPBadRequest(text='source_binding_invalid')
    return result


def atomic_json(path,value):
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as target:
        json.dump(value,target,separators=(',',':'));target.flush();os.fsync(target.fileno())
    os.replace(temporary,path)


class Supervisor:
    def __init__(self,root,token,worker,binary):
        parsed=urlsplit(worker)
        if (not isinstance(token,str) or len(token)<32 or parsed.scheme!='https' or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/')):
            raise ValueError('calls_service_configuration_invalid')
        self.root=Path(root).resolve();self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.token_hash=hashlib.sha256(token.encode()).digest()
        self.endpoint=worker.rstrip('/')+'/api/platform/call-gateway'
        self.binary=binary;self.sources={};self.lock=asyncio.Lock();self.client=None;self.volume_lock=None
        self.stopping=False

    async def start(self,app):
        # Linux volume fencing: two replicas/processes must never restore the
        # same WhatsApp device or SQLite store at the same time.
        import fcntl
        self.volume_lock=open(self.root/'.engine.lock','a')
        fcntl.flock(self.volume_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.client=ClientSession(timeout=ClientTimeout(total=20),trust_env=False)
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or not UUID.fullmatch(folder.name):continue
            if folder.is_symlink():raise ValueError('source_storage_invalid')
            metadata=json.loads((folder/'source.json').read_text())
            self.validate_saved(folder.name,metadata)
            self.sources[folder.name]={'data':metadata,'process':None,'task':None,'failures':0}
        ports=[x['data']['port'] for x in self.sources.values()]
        if len(ports)!=len(set(ports)) or len(ports)>MAX_SOURCES:raise ValueError('source_storage_invalid')
        for source in self.sources:self.launch(source)

    def validate_saved(self,source,data):
        safe_uuid(source);binding(data['binding'])
        if type(data.get('port')) is not int or not 18000<=data['port']<18000+MAX_SOURCES:
            raise ValueError('source_storage_invalid')
        for key in ('signing_seed','encryption_key','http_token'):
            if not re.fullmatch('[0-9a-f]{64}',data.get(key,'')):raise ValueError('source_storage_invalid')

    def configure(self,source):
        data=self.sources[source]['data'];folder=self.root/source;b=data['binding']
        atomic_json(folder/'gateway.json',{'managed':True,'endpoint':self.endpoint,'token':b['gateway_token'],
                    'account_jid':b['account_jid'],'allowed_peers':[]})
        atomic_json(folder/'recordings.json',{'capacity_dir':str(self.root),'managed':True,'account_jid':b['account_jid'],'allowed_peers':[],
                    'signing_seed':data['signing_seed'],'encryption_key':data['encryption_key']})
        fd=os.open(folder/'http-token',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as target:target.write(data['http_token'])

    def launch(self,source):
        entry=self.sources[source]
        if entry['task'] is not None and not entry['task'].done():return
        self.configure(source)
        entry['task']=asyncio.create_task(self.run_source(source))

    async def run_source(self,source):
        entry=self.sources[source];folder=self.root/source;data=entry['data']
        while not self.stopping and entry['failures']<5:
            started=asyncio.get_running_loop().time()
            try:
                entry['process']=await asyncio.create_subprocess_exec(self.binary,
                    '-addr',f'127.0.0.1:{data["port"]}','-db',str(folder/'session.db'),'-static','',
                    '-lab-token-file',str(folder/'http-token'),'-recording-config',str(folder/'recordings.json'),
                    '-app-gateway-config',str(folder/'gateway.json'),
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                await entry['process'].wait()
            except OSError:
                print('calls_engine_spawn_failed',flush=True)
            finally:entry['process']=None
            if self.stopping:return
            if asyncio.get_running_loop().time()-started>300:entry['failures']=0
            entry['failures']+=1
            print('calls_engine_restarting',flush=True)
            await asyncio.sleep(min(30,2**entry['failures']))

    async def stop(self,app):
        self.stopping=True
        for entry in self.sources.values():
            process=entry['process']
            if process and process.returncode is None:process.send_signal(signal.SIGTERM)
        tasks=[entry['task'] for entry in self.sources.values() if entry['task']]
        if tasks:
            _,pending=await asyncio.wait(tasks,timeout=20)
            for entry in self.sources.values():
                process=entry['process']
                if process and process.returncode is None:process.kill()
            for task in pending:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        if self.client:await self.client.close()
        if self.volume_lock:self.volume_lock.close()

    def authorize(self,request):
        raw=request.headers.get('Authorization','')
        candidate=hashlib.sha256(raw[7:].encode()).digest()
        if (request.headers.get('Origin') or request.headers.get('Cookie') or not raw.startswith('Bearer ')
                or not hmac.compare_digest(candidate,self.token_hash)):
            raise web.HTTPForbidden(text='calls_service_access_denied')

    def entry(self,request):
        self.authorize(request);source=safe_uuid(request.match_info['source'])
        entry=self.sources.get(source)
        if not entry:raise web.HTTPNotFound(text='source_not_found')
        return source,entry

    async def engine(self,source,path,method='GET'):
        entry=self.sources[source];data=entry['data']
        if self.client is None:raise web.HTTPServiceUnavailable(text='calls_service_unavailable')
        try:
            async with self.client.request(method,f'http://127.0.0.1:{data["port"]}'+path,
                    headers={'Authorization':'Bearer '+data['http_token']},allow_redirects=False) as response:
                raw=await response.content.read(32769)
                if response.status!=200 or len(raw)>32768:raise ValueError()
                out=json.loads(raw)
                if not isinstance(out,dict):raise ValueError()
                return out
        except (ValueError,OSError,asyncio.TimeoutError):
            raise web.HTTPServiceUnavailable(text='calls_engine_unavailable') from None

    async def provision(self,request):
        self.authorize(request);source=safe_uuid(request.match_info['source'])
        try:spec=binding(await request.json())
        except (json.JSONDecodeError,UnicodeDecodeError):raise web.HTTPBadRequest(text='source_binding_invalid') from None
        async with self.lock:
            entry=self.sources.get(source)
            if entry:
                # Includes gateway token: reprovisioning must not rotate a live
                # identity, recording key, journal or company binding implicitly.
                old=json.dumps(entry['data']['binding'],sort_keys=True)
                if not hmac.compare_digest(old,json.dumps(spec,sort_keys=True)):
                    raise web.HTTPConflict(text='source_binding_changed')
            else:
                if len(self.sources)>=MAX_SOURCES:raise web.HTTPServiceUnavailable(text='calls_source_capacity')
                if any(e['data']['binding']['account_jid']==spec['account_jid'] for e in self.sources.values()):
                    raise web.HTTPConflict(text='source_account_already_bound')
                ports={e['data']['port'] for e in self.sources.values()}
                port=next(p for p in range(18000,18000+MAX_SOURCES) if p not in ports)
                data={'binding':spec,'port':port,'http_token':secrets.token_hex(32),
                      'signing_seed':secrets.token_hex(32),'encryption_key':secrets.token_hex(32)}
                folder=self.root/source
                folder.mkdir(mode=0o700,exist_ok=False)
                atomic_json(folder/'source.json',data)
                entry={'data':data,'process':None,'task':None,'failures':0};self.sources[source]=entry
            self.launch(source)
        # Readiness is retried by the owner settings UI. Reservation is durable;
        # a timeout never provisions a second device or overwrites credentials.
        for attempt in range(6):
            try:await self.engine(source,'/internal/setup','POST');break
            except web.HTTPServiceUnavailable:
                if attempt==5:break
                await asyncio.sleep(.5)
        public=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(entry['data']['signing_seed'])).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        return web.json_response({'public_key':public},headers={'Cache-Control':'no-store'})

    async def status(self,request):
        source,entry=self.entry(request)
        result=await self.engine(source,'/internal/status')
        return web.json_response(result,headers={'Cache-Control':'no-store'})

    async def audio(self,request):
        source,entry=self.entry(request)
        provider=request.match_info['provider'];call=safe_uuid(request.match_info['call'])
        if not PROVIDER.fullmatch(provider):raise web.HTTPBadRequest(text='call_identity_invalid')
        data=entry['data'];upstream=None
        try:
            upstream=await self.client.ws_connect(f'http://127.0.0.1:{data["port"]}/internal/audio/{provider}/{call}',
                headers={'Authorization':'Bearer '+data['http_token']},max_msg_size=6400,compress=0)
            downstream=web.WebSocketResponse(max_msg_size=6400,compress=False,heartbeat=10)
            await downstream.prepare(request)
            tasks=[asyncio.create_task(copy_audio(upstream,downstream,'engine')),asyncio.create_task(copy_audio(downstream,upstream,'worker'))]
            code='cancelled'
            try:
                done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                for task in tasks:
                    if task in done and not task.cancelled():
                        code=task.result();break
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                print('calls_audio_proxy_end code='+code,flush=True)
                await downstream.close()
            return downstream
        except (OSError,asyncio.TimeoutError):raise web.HTTPServiceUnavailable(text='calls_audio_unavailable') from None
        finally:
            if upstream:await upstream.close()


async def copy_audio(src,dst,side):
    # Local allowlisted stage only. Never log a frame, URL, identifier or error.
    side='engine' if side=='engine' else 'worker'
    try:
        async for message in src:
            if message.type!=WSMsgType.BINARY:return side+'_closed'
            if not message.data or len(message.data)>6400 or len(message.data)%2:return side+'_invalid_pcm'
            await asyncio.wait_for(dst.send_bytes(message.data),2)
    except asyncio.TimeoutError:return side+'_send_timeout'
    except Exception:return side+'_transport_error'
    return side+'_closed'


def make_app(supervisor):
    @web.middleware
    async def errors(request,handler):
        try:return await handler(request)
        except web.HTTPException:raise
        except Exception:
            # No exception text, request headers, phone numbers or audio.
            print('calls_service_request_failed',flush=True)
            return web.json_response({'error':'calls_service_unavailable'},status=503)
    app=web.Application(client_max_size=4096,middlewares=[errors])
    app.router.add_get('/healthz',lambda r:web.json_response({'ok':True}))
    app.router.add_post('/v1/sources/{source}',supervisor.provision)
    app.router.add_get('/v1/sources/{source}',supervisor.status)
    app.router.add_get('/v1/sources/{source}/audio/{provider}/{call}',supervisor.audio)
    app.on_startup.append(supervisor.start);app.on_cleanup.append(supervisor.stop)
    return app


if __name__=='__main__':
    os.umask(0o077)
    supervisor=Supervisor(os.environ.get('CALLS_DATA_DIR','/data'),os.environ.get('CALLS_SERVICE_TOKEN',''),
                          os.environ.get('CALLS_WORKER_URL',''),os.environ.get('CALLS_ENGINE_BINARY','/app/call-engine'))
    web.run_app(make_app(supervisor),host=['0.0.0.0','::'],port=int(os.environ.get('PORT','8080')),access_log=None,print=None)
