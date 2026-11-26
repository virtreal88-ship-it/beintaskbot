"""Private source-bound call engine supervisor. No public call or audio API.

Worker is the sole control/media client. Each source has an immutable identity,
separate SQLite session, signing key and command journal on the private volume.
"""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
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
        self.message_slots=asyncio.Semaphore(2)
        self.avatar_slots=asyncio.Semaphore(2)
        self.video_slots=asyncio.Semaphore(1)
        self.video_read_slots=asyncio.Semaphore(1)

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
        self.apply_messaging_transitions(os.environ.get('CALLS_NATIVE_MESSAGING_TRANSITIONS',''))
        for source in self.sources:self.launch(source)

    def apply_messaging_transitions(self,raw):
        """Operator manifest, applied only while the volume is exclusively held.

        Never called by a public/provision endpoint or against a running engine.
        The Worker admission fence must be prepared before this deployment.
        Device databases and all existing credentials remain in place.
        """
        if not raw:return
        if self.volume_lock is None or any(e.get('process') or e.get('task') for e in self.sources.values()):
            raise ValueError('native_transition_requires_exclusive_startup')
        try:items=json.loads(raw)
        except (ValueError,TypeError):raise ValueError('native_transition_manifest_invalid') from None
        # One source per maintenance deployment; never partially switch a batch.
        if not isinstance(items,list) or len(items)!=1:raise ValueError('native_transition_manifest_invalid')
        changes=[];seen=set()
        for item in items:
            if not isinstance(item,dict) or set(item)!={'gateway_id','tenant_id','channel_id','account_jid','public_key','generation'}:
                raise ValueError('native_transition_manifest_invalid')
            source=safe_uuid(item['gateway_id'])
            if source in seen or source not in self.sources:raise ValueError('native_transition_source_changed')
            seen.add(source);data=self.sources[source]['data'];b=data['binding']
            public=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data['signing_seed'])).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
            if any(item[k]!=b[k] for k in ('tenant_id','channel_id','account_jid')) or item['public_key']!=public:
                raise ValueError('native_transition_source_changed')
            generation=item['generation']
            if not isinstance(generation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',generation):raise ValueError('native_transition_manifest_invalid')
            if data.get('messaging'):
                if data['messaging']['generation']!=generation:raise ValueError('native_transition_generation_changed')
                continue
            changes.append((source,data,{**data,'messaging':{'generation':generation,'key':secrets.token_hex(32)}}))
        # Validate every requested binding before modifying any source.
        for source,previous,next_data in changes:
            database=self.root/source/'session.db'
            if database.is_symlink() or not database.is_file():raise ValueError('native_transition_device_missing')
            # Call-only engines did not enable encrypted message buffering.
            # Never interpret existing unencrypted retry data with the new key,
            # discard it or quietly let provider decryption fail after rollout.
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
                for table in ('whatsmeow_event_buffer','whatsmeow_retry_buffer'):
                    if db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone():
                        if db.execute('SELECT 1 FROM '+table+' WHERE plaintext IS NOT NULL LIMIT 1').fetchone():
                            raise ValueError('native_transition_buffer_migration_required')
            backup=self.root/source/'source.before-native.json'
            if backup.exists():
                if backup.is_symlink() or json.loads(backup.read_text())!=previous:raise ValueError('native_transition_backup_changed')
            else:
                fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as target:
                    json.dump(previous,target,separators=(',',':'));target.flush();os.fsync(target.fileno())
            atomic_json(self.root/source/'source.json',next_data)
            self.sources[source]['data']=next_data

    def validate_saved(self,source,data):
        safe_uuid(source);binding(data['binding'])
        if type(data.get('port')) is not int or not 18000<=data['port']<18000+MAX_SOURCES:
            raise ValueError('source_storage_invalid')
        for key in ('signing_seed','encryption_key','http_token'):
            if not re.fullmatch('[0-9a-f]{64}',data.get(key,'')):raise ValueError('source_storage_invalid')

    def configure(self,source):
        data=self.sources[source]['data'];folder=self.root/source;b=data['binding']
        gateway={'managed':True,'endpoint':self.endpoint,'token':b['gateway_token'],
                    'account_jid':b['account_jid'],'allowed_peers':[]}
        if data.get('messaging') is not None:
            cfg=data['messaging']
            if (set(cfg)!={'generation','key'} or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',cfg['generation'])
                    or not re.fullmatch(r'[0-9a-f]{64}',cfg['key'])):
                raise ValueError('native_messaging_configuration_invalid')
            gateway['messaging']={'scope':{'tenant':b['tenant_id'],'source':b['channel_id'],
                'account':b['account_jid'],'generation':cfg['generation']},
                'key':base64.b64encode(bytes.fromhex(cfg['key'])).decode('ascii')}
        atomic_json(folder/'gateway.json',gateway)
        atomic_json(folder/'recordings.json',{'capacity_dir':str(self.root),'managed':True,'account_jid':b['account_jid'],'allowed_peers':[],
                    'signing_seed':data['signing_seed'],'encryption_key':data['encryption_key']})
        fd=os.open(folder/'http-token',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as target:target.write(data['http_token'])

    def launch(self,source):
        entry=self.sources[source]
        if entry['data'].get('disabled'):return
        if entry['task'] is not None and not entry['task'].done():return
        self.configure(source)
        entry['task']=asyncio.create_task(self.run_source(source))

    async def run_source(self,source):
        entry=self.sources[source];folder=self.root/source;data=entry['data']
        while not self.stopping and not entry['data'].get('disabled') and entry['failures']<5:
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
            if self.stopping or entry['data'].get('disabled'):return
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
        try:
            payload=await request.json()
            if not isinstance(payload,dict):raise web.HTTPBadRequest(text='source_binding_invalid')
            generation=payload.pop('messaging_generation',None)
            resume=payload.pop('resume',False)
            if type(resume) is not bool:raise web.HTTPBadRequest(text='source_binding_invalid')
            if generation is not None and (not isinstance(generation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',generation)):
                raise web.HTTPBadRequest(text='native_messaging_configuration_invalid')
            spec=binding(payload)
        except (json.JSONDecodeError,UnicodeDecodeError):raise web.HTTPBadRequest(text='source_binding_invalid') from None
        async with self.lock:
            entry=self.sources.get(source)
            if entry:
                # Includes gateway token: reprovisioning must not rotate a live
                # identity, recording key, journal or company binding implicitly.
                old=json.dumps(entry['data']['binding'],sort_keys=True)
                if not hmac.compare_digest(old,json.dumps(spec,sort_keys=True)):
                    raise web.HTTPConflict(text='source_binding_changed')
                if generation is not None and entry['data'].get('messaging',{}).get('generation')!=generation:
                    # Existing devices require a separately fenced maintenance
                    # transition. Provision never restarts a live call engine.
                    raise web.HTTPConflict(text='source_messaging_transition_required')
                if entry['data'].get('disabled'):
                    if not resume:raise web.HTTPConflict(text='source_stopped')
                    if entry.get('process') is not None or (entry.get('task') is not None and not entry['task'].done()):
                        raise web.HTTPConflict(text='source_stop_unconfirmed')
                    data={**entry['data'],'disabled':False}
                    atomic_json(self.root/source/'source.json',data)
                    entry['data']=data;entry['failures']=0
            else:
                if len(self.sources)>=MAX_SOURCES:raise web.HTTPServiceUnavailable(text='calls_source_capacity')
                if any(e['data']['binding']['account_jid']==spec['account_jid'] for e in self.sources.values()):
                    raise web.HTTPConflict(text='source_account_already_bound')
                ports={e['data']['port'] for e in self.sources.values()}
                port=next(p for p in range(18000,18000+MAX_SOURCES) if p not in ports)
                data={'binding':spec,'port':port,'http_token':secrets.token_hex(32),
                      'signing_seed':secrets.token_hex(32),'encryption_key':secrets.token_hex(32)}
                if generation is not None:
                    data['messaging']={'generation':generation,'key':secrets.token_hex(32)}
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
        if entry['data'].get('disabled') and entry.get('process') is None and (entry.get('task') is None or entry['task'].done()):
            return web.json_response({'stopped':True,'connected':False,'connection_state':'stopped',
                'account_jid':entry['data']['binding']['account_jid'],
                'messaging_generation':entry['data'].get('messaging',{}).get('generation')},headers={'Cache-Control':'no-store'})
        result=await self.engine(source,'/internal/status')
        result['messaging_generation']=entry['data'].get('messaging',{}).get('generation')
        return web.json_response(result,headers={'Cache-Control':'no-store'})

    async def messages(self,request):
        source,entry=self.entry(request)
        if entry['data'].get('disabled'):raise web.HTTPConflict(text='source_stopped')
        data=entry['data'];cfg=data.get('messaging');b=data['binding']
        if not cfg:raise web.HTTPConflict(text='native_messaging_disabled')
        video=request.path.endswith('/media') and request.headers.get('X-Native-Media-Kind')=='video'
        slots=self.video_read_slots if video else self.avatar_slots if request.path.endswith('/avatar') else self.message_slots
        if slots.locked():raise web.HTTPTooManyRequests(text='native_messaging_busy')
        async with slots:
            return await self._messages(request,source,entry,cfg,b,video=video)

    async def send_video(self,request):
        _,entry=self.entry(request)
        data=entry['data']
        cfg,b=data.get('messaging'),data['binding']
        if data.get('disabled') or not cfg:raise web.HTTPConflict(text='source_stopped')
        length=request.content_length
        if request.content_type!='application/octet-stream' or request.headers.get('Content-Encoding') or not length:
            raise web.HTTPBadRequest(text='native_video_invalid')
        if length>100*1024*1024:
            raise web.HTTPRequestEntityTooLarge(max_size=100*1024*1024,actual_size=length)
        command=request.headers.get('X-Native-Video-Command','')
        if not command or len(command)>28000:raise web.HTTPBadRequest(text='native_video_invalid')
        try:payload=json.loads(base64.b64decode(command,validate=True))
        except (ValueError,UnicodeError):raise web.HTTPBadRequest(text='native_video_invalid') from None
        expected={'tenant':b['tenant_id'],'source':b['channel_id'],'account':b['account_jid'],'generation':cfg['generation']}
        if (not isinstance(payload,dict) or payload.get('scope')!=expected or payload.get('kind')!='video'
                or payload.get('mime')!='video/mp4' or payload.get('data') not in (None,'')):
            raise web.HTTPForbidden(text='native_video_scope_denied')
        if self.client is None:raise web.HTTPServiceUnavailable(text='calls_service_unavailable')
        if self.video_slots.locked():raise web.HTTPTooManyRequests(text='native_video_busy')
        async with self.video_slots:
            async with self.client.post(f'http://127.0.0.1:{data["port"]}/internal/messages/video',
                    data=request.content.iter_chunked(65536),headers={'Authorization':'Bearer '+data['http_token'],
                    'Content-Type':'application/octet-stream','Content-Length':str(length),'X-Native-Video-Command':command},
                    allow_redirects=False,timeout=ClientTimeout(total=130)) as upstream:
                chunks,size=[],0
                async for chunk in upstream.content.iter_chunked(4096):
                    size+=len(chunk)
                    if size>16384:raise web.HTTPBadGateway(text='native_response_invalid')
                    chunks.append(chunk)
                return web.Response(body=b''.join(chunks),status=upstream.status,content_type='application/json',
                    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    async def _messages(self,request,source,entry,cfg,b,*,video=False):
        data=entry['data']
        raw=await request.clone(client_max_size=15*1024*1024).read()
        if len(raw)>15*1024*1024:raise web.HTTPRequestEntityTooLarge(max_size=15*1024*1024,actual_size=len(raw))
        try:payload=json.loads(raw)
        except (ValueError,UnicodeError):raise web.HTTPBadRequest(text='native_message_invalid') from None
        expected={'tenant':b['tenant_id'],'source':b['channel_id'],'account':b['account_jid'],'generation':cfg['generation']}
        if not isinstance(payload,dict) or payload.get('scope')!=expected:
            raise web.HTTPForbidden(text='native_message_scope_denied')
        suffix=next((part for part in ('/media','/status','/group','/group-membership','/avatar','/contact') if request.path.endswith(part)),'')
        if self.client is None:raise web.HTTPServiceUnavailable(text='calls_service_unavailable')
        # No retries: a lost response may follow an accepted provider send.
        headers={'Authorization':'Bearer '+data['http_token'],'Content-Type':'application/json'}
        if video:headers['X-Native-Media-Kind']='video'
        async with self.client.post(f'http://127.0.0.1:{data["port"]}/internal/messages'+suffix,
                data=raw,headers=headers,
                allow_redirects=False,timeout=ClientTimeout(total=125 if suffix=='/media' else 35)) as upstream:
            if suffix=='/media' and upstream.status==200 and upstream.content_type=='video/mp4':
                length=upstream.content_length
                if not length or length>100*1024*1024:raise web.HTTPBadGateway(text='native_response_invalid')
                outgoing=web.StreamResponse(status=200,headers={'Content-Type':'application/octet-stream',
                    'Content-Length':str(length),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'attachment'})
                await outgoing.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await outgoing.write(chunk)
                await outgoing.write_eof()
                return outgoing
            chunks=[];size=0
            async for chunk in upstream.content.iter_chunked(65536):
                size+=len(chunk)
                if size>11*1024*1024:raise web.HTTPBadGateway(text='native_response_invalid')
                chunks.append(chunk)
            content_type='application/octet-stream' if suffix and upstream.status==200 else 'application/json'
            return web.Response(body=b''.join(chunks),status=upstream.status,content_type=content_type,
                headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'attachment'})


    async def disconnect(self,request):
        source,entry=self.entry(request)
        payload=await request.json()
        async with self.lock:
            data=entry['data'];b=data['binding']
            expected={'tenant':b['tenant_id'],'source':b['channel_id'],'account':b['account_jid'],
                      'generation':data.get('messaging',{}).get('generation')}
            if payload!={'scope':expected} or not expected['generation']:
                raise web.HTTPForbidden(text='source_scope_denied')
            if not data.get('disabled'):
                state=await self.engine(source,'/internal/status')
                diagnostics=state.get('diagnostics') or {};messages=state.get('messaging_diagnostics') or {}
                if (any(type(diagnostics.get(k)) is not int or diagnostics[k]!=0 for k in
                        ('active_registry_calls','provider_start_requested','audio_attached'))
                        or any(type(messages.get(k)) is not int or messages[k]!=0 for k in ('unknown_sends','pending_events'))):
                    raise web.HTTPConflict(text='source_operations_pending')
                data={**data,'disabled':True}
                atomic_json(self.root/source/'source.json',data);entry['data']=data
            # Persist disabled BEFORE stopping. Crash/redeployment cannot restart
            # this source. Retain device/journal/recordings for explicit resume.
            process=entry.get('process');task=entry.get('task')
            if process is not None and process.returncode is None:
                process.terminate()
            if task is not None and not task.done():
                done,_=await asyncio.wait([task],timeout=10)
                if not done:
                    process=entry.get('process')
                    if process is not None and process.returncode is None:process.kill()
                    done,_=await asyncio.wait([task],timeout=5)
                    if not done:raise web.HTTPServiceUnavailable(text='source_stop_unconfirmed')
            if entry.get('process') is not None:raise web.HTTPServiceUnavailable(text='source_stop_unconfirmed')
            return web.json_response({'stopped':True,'scope':expected},headers={'Cache-Control':'no-store'})

    async def audio(self,request):
        source,entry=self.entry(request)
        if entry['data'].get('disabled'):raise web.HTTPConflict(text='source_stopped')
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
    app=web.Application(client_max_size=4096,middlewares=[errors],handler_args={'max_field_size':32768})
    app.router.add_get('/healthz',lambda r:web.json_response({'ok':True}))
    app.router.add_post('/v1/sources/{source}',supervisor.provision)
    app.router.add_get('/v1/sources/{source}',supervisor.status)
    app.router.add_post('/v1/sources/{source}/disconnect',supervisor.disconnect)
    app.router.add_post('/v1/sources/{source}/messages',supervisor.messages)
    app.router.add_post('/v1/sources/{source}/messages/status',supervisor.messages)
    app.router.add_post('/v1/sources/{source}/messages/media',supervisor.messages)
    app.router.add_post('/v1/sources/{source}/messages/video',supervisor.send_video)
    app.router.add_post('/v1/sources/{source}/messages/group',supervisor.messages)
    app.router.add_post('/v1/sources/{source}/messages/group-membership',supervisor.messages)
    app.router.add_post('/v1/sources/{source}/messages/avatar',supervisor.messages)
    app.router.add_post('/v1/sources/{source}/messages/contact',supervisor.messages)
    app.router.add_get('/v1/sources/{source}/audio/{provider}/{call}',supervisor.audio)
    app.on_startup.append(supervisor.start);app.on_cleanup.append(supervisor.stop)
    return app


if __name__=='__main__':
    os.umask(0o077)
    supervisor=Supervisor(os.environ.get('CALLS_DATA_DIR','/data'),os.environ.get('CALLS_SERVICE_TOKEN',''),
                          os.environ.get('CALLS_WORKER_URL',''),os.environ.get('CALLS_ENGINE_BINARY','/app/call-engine'))
    web.run_app(make_app(supervisor),host=['0.0.0.0','::'],port=int(os.environ.get('PORT','8080')),access_log=None,print=None)
