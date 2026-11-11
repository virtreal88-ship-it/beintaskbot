"""Chromium acceptance for the unmodified production AI controller.

Synthetic HTTP boundary runs locally without a provider/database. This is not cookie/PostgreSQL authorization evidence. It uses the production
HTML DOM/controller and browser assertions. Other
app controllers are omitted; this is not a full application-bootstrap test.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import uuid

from aiohttp import web
import pytest

ROOT=Path(__file__).resolve().parents[1]
A='00000000-0000-0000-0000-000000000065'
B='00000000-0000-0000-0000-000000000066'
OWN='00000000-0000-0000-0000-000000000301'
PEER='00000000-0000-0000-0000-000000000302'
SOURCE='00000000-0000-0000-0000-000000000201'
OTHER='00000000-0000-0000-0000-000000000299'
MODES={'read':{'view':'all','create':'none','update':'none'},
       'create':{'view':'none','create':'self','update':'none'},
       'mixed':{'view':'all','create':'none','update':'self'},
       'revoked':{'view':'none','create':'none','update':'none'},
       'foreign':{'view':'none','create':'none','update':'none'}}


def chromium():
    path=os.environ.get('CHROMIUM_EXECUTABLE_PATH') or shutil.which('chromium')
    if not path:pytest.skip('Local Chromium executable required')
    return path


class SyntheticBoundary:
    backend='synthetic HTTP boundary (not backend authorization proof)'
    own,other_source,tenant,actor=OWN,OTHER,A,31
    def __init__(self):
        self.mode='read';self.current_tenant=A
        self.rows=[{'id':OWN,'tenant_id':A,'name':'Own agent','instructions':'Own private instructions','version':1,'enabled':False,'scopes':[]},
                   {'id':PEER,'tenant_id':A,'name':'Peer agent','instructions':'Peer instructions','version':1,'enabled':False,'scopes':[]}]
    def set_mode(self,name):self.mode=name;self.current_tenant=B if name=='foreign' else A
    def profile(self,request):
        return {'member':{'tenant_id':self.current_tenant,'telegram_id':31,'role':'admin','active':True},
                'capabilities':{'modules':{'ai_agents':False},'pages':{'ai_agents':True},'ai_documents':{'scoped':True,**{key:value!='none' for key,value in MODES[self.mode].items()}}}}
    def cookie(self,response):pass
    def facts(self):return {'created':sum(row['name']=='Created through Chromium' for row in self.rows),'own_name':self.rows[0]['name']}
    async def api(self,request):
        rights=MODES[self.mode]
        def response(value,status=200):return web.json_response({'success':status==200,**({'result':value} if status==200 else {'error':value})},status=status)
        if request.method=='GET':
            if request.path.endswith('/options'):
                return response({'scoped':True,'connect_channels':[{'id':SOURCE,'name':'Assigned source'}],'pipelines':[],'stages':[],'channels':[],'models':[]})
            if rights['view']=='none':return response('read denied',403)
            return response([{**row,'capabilities':{'update':rights['update']!='none' and row['id']!=PEER}} for row in self.rows])
        body=await request.json()
        if body.get('expected_tenant_id')!=self.current_tenant:return response('identity changed',409)
        action='create' if request.method=='POST' else 'update'
        if rights[action]=='none':return response('write denied',404 if action=='update' else 403)
        if any(source.get('channel_id')!=SOURCE for source in body.get('scopes',[])):return response('source denied',403)
        if action=='create':
            row={'id':str(uuid.uuid4()),'tenant_id':A,'name':body['name'],'instructions':body['instructions'],'scopes':body.get('scopes',[]),'enabled':False,'version':1}
            self.rows.append(row)
        else:
            row=next(row for row in self.rows if row['id']==request.match_info['agent_id'])
            if row['id']==PEER:return response('not owned',404)
            row.update({key:body[key] for key in ('name','instructions','scopes') if key in body});row['version']+=1
        return response({'id':row['id'],'version':row['version']})


def run_browser(boundary,width,*,runner='ai_scoped_documents_http_browser.cjs'):
    executable=chromium() if runner=='ai_scoped_documents_http_browser.cjs' else ''
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    origin='http://127.0.0.1:'+str(sock.getsockname()[1])
    loop=asyncio.new_event_loop();ready=threading.Event()
    state={'list_reads':0,'receipts':[],'shared':[],'pending':0,'released':0,'hold':False}
    errors=[]
    async def start():
        state['release']=asyncio.Event()
        @web.middleware
        async def observe(request,handler):
            response=await handler(request)
            if request.path=='/api/platform/ai/agents' and request.method=='GET':
                state['list_reads']+=1
                if state['hold']:
                    state['hold']=False;state['pending']=1
                    await state['release'].wait();state['released']=1
            if request.path.startswith('/api/platform/ai/agents') and request.method in {'POST','PATCH'} and response.status==200:
                state['receipts'].append(sorted(json.loads(response.body)['result']))
            return response
        app=web.Application(middlewares=[observe])
        config={'backend':boundary.backend,'own':boundary.own,'otherSource':boundary.other_source,'tenant':boundary.tenant,'actor':boundary.actor}
        async def page(request):
            html=(ROOT/'docs/tenant-app.html').read_text()
            html=re.sub(r'<script\b[^>]*>[\s\S]*?</script>','',html,flags=re.I)
            # Preserve real HTML/styles but block every external resource.
            html=re.sub(r'<link\b[^>]*>','',html,flags=re.I)
            # Shared app bootstrap is deliberately omitted; provide its full
            # translation interface, without loading other app controllers.
            bootstrap="window.AI_ACCEPTANCE_ERRORS=[];window.addEventListener('error',e=>AI_ACCEPTANCE_ERRORS.push(e.message));window.tenantI18n={language:'en',t:key=>key,source:value=>value,date:value=>String(value),capture:()=>{}};document.querySelectorAll('.view').forEach(n=>n.classList.remove('active'));document.getElementById('view-ai_agents').classList.add('active');window.AI_ACCEPTANCE_CONFIG="+json.dumps(config)+';'
            html=html.replace('</body>','<script>'+bootstrap+'</script><script src="/assets/tenant-ai-agents.js"></script><script src="/test/acceptance.js"></script></body>')
            response=web.Response(text=html,content_type='text/html',headers={'Content-Security-Policy':"default-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:"})
            boundary.cookie(response)
            return response
        async def me(request):
            value=boundary.profile(request)
            return web.json_response(value,dumps=lambda value:json.dumps(value,default=str))
        async def control(request):
            command=request.match_info['command']
            if command=='mode':boundary.set_mode(request.query['name'])
            elif command=='revoke':boundary.set_mode('revoked')
            elif command=='hold':state['hold']=True;state['release'].clear();state['pending']=0;state['released']=0
            elif command=='release':state['release'].set()
            elif command!='state':raise web.HTTPNotFound()
            response=web.json_response({key:value for key,value in state.items() if key not in {'release','runner'} }|boundary.facts())
            boundary.cookie(response)
            return response
        async def asset(request):return web.Response(text=(ROOT/'docs/tenant-ai-agents.js').read_text(),content_type='application/javascript')
        async def acceptance(request):return web.Response(text=(ROOT/'tests/ai_scoped_documents_browser_acceptance.js').read_text(),content_type='application/javascript')
        app.router.add_get('/app',page);app.router.add_get('/api/platform/me',me)
        app.router.add_get('/assets/tenant-ai-agents.js',asset);app.router.add_get('/test/acceptance.js',acceptance)
        app.router.add_get('/test/{command}',control)
        if hasattr(boundary,'register'):boundary.register(app,origin)
        else:
            app.router.add_get('/api/platform/ai/agents/options',boundary.api)
            app.router.add_get('/api/platform/ai/agents',boundary.api)
            app.router.add_post('/api/platform/ai/agents',boundary.api)
            app.router.add_patch('/api/platform/ai/agents/{agent_id}',boundary.api)
        async def forbidden(request):
            if request.path.startswith('/api/'):
                state['shared'].append(request.path)
                return web.json_response({'success':False,'error':'Unexpected shared API'},status=501)
            raise web.HTTPNotFound()
        app.router.add_route('*','/{tail:.*}',forbidden)
        state['runner']=web.AppRunner(app);await state['runner'].setup();await web.SockSite(state['runner'],sock).start();ready.set()
    def serve():
        asyncio.set_event_loop(loop)
        try:loop.run_until_complete(start());loop.run_forever()
        except BaseException as error:errors.append(error);ready.set()
    thread=threading.Thread(target=serve,daemon=True);thread.start();assert ready.wait(10)
    try:
        assert not errors,errors
        process=subprocess.run(['node',str(ROOT/'tests'/runner)],cwd=ROOT,
            env={**os.environ,'AI_RUNTIME_ORIGIN':origin,'AI_RUNTIME_WIDTH':str(width),'CHROMIUM_EXECUTABLE_PATH':executable},capture_output=True,text=True,timeout=70)
        assert process.returncode==0,process.stdout+'\n'+process.stderr
        assert state['shared']==[]
        return process.stdout
    finally:
        if 'runner' in state:
            loop.call_soon_threadsafe(state['release'].set)
            asyncio.run_coroutine_threadsafe(state['runner'].cleanup(),loop).result(10)
        loop.call_soon_threadsafe(loop.stop);thread.join(10);loop.close();sock.close()
        assert not thread.is_alive()


@pytest.mark.parametrize('width',[390,1100])
def test_production_ai_controller_in_chromium_synthetic_http(width):
    """Browser/DOM/transport evidence only: this API boundary is synthetic."""
    assert 'PASS synthetic HTTP boundary' in run_browser(SyntheticBoundary(),width)
