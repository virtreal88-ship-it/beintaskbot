"""Chromium -> real local HTTP/store/webhook, synthetic Connect provider only."""
import asyncio
import hashlib
import hmac
import json
import os
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from aiohttp import web
from test_tenant_support_inbox import database,db,A,CA,PROFILE
from test_tenant_consultation_group import setup_case,GROUP
import tenant_consultation_group as flow
import tenant_internal_consultations as consultations
import tenant_connect_transport as transport
from tenant_support_inbox_api import handler

ROOT=Path(__file__).resolve().parents[1]

def test_chromium_real_local_backend_signed_reply_and_unsent_draft(db,monkeypatch):
    if not (ROOT/'.support-test-deps/node_modules/playwright').exists() or not Path(os.environ.get('CHROMIUM_EXECUTABLE_PATH','/usr/bin/chromium')).exists():
        pytest.skip('Install Playwright and Chromium to run browser integration')
    consult,case=setup_case(db,monkeypatch);image=str(uuid.uuid4());image2=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image')",(image,A,'connect:'+str(uuid.uuid4()),'connect:'+CA))
    from tenant_support_inbox_store import record_case_message_membership
    with db.cursor() as cur:record_case_message_membership(cur,A,case,CA,'client',[db.execute('SELECT external_id FROM saas_crm_messages WHERE id=%s::uuid',(image,))[0]['external_id']],'support-group-e2e-initial-photo')
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",(A,b'synthetic'));db.commit()
    monkeypatch.setattr(consultations,'_connect',lambda:db)
    monkeypatch.setattr(transport,'_connect',lambda:db)
    monkeypatch.setattr(transport,'_fernet',lambda:SimpleNamespace(decrypt=lambda _:b'{"webhook_secret":"synthetic-secret"}'))
    monkeypatch.setattr('tenant_ai_agent_connect.enqueue',lambda *_args,**_kwargs:None)
    sends=[]
    def send(route,body,key):
        sends.append((route['conversation_id'],body))
        return {'message_id':'synthetic-'+str(len(sends)),'external_message_id':'PROVIDER_'+str(len(sends))}
    monkeypatch.setattr(transport,'send_group_with_receipt',send)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16);port=sock.getsockname()[1];origin='http://127.0.0.1:'+str(port)
    loop=asyncio.new_event_loop();ready=threading.Event();state={}
    async def start():
        app=web.Application();member=lambda _:PROFILE
        async def index(_):return web.Response(text='<section id="view-support"><div id="operatorSupport"></div></section><div id="modalContent"></div><script>window.tenantOperator={hasRight:()=>true}</script><script src="/i18n.js"></script><script src="/support.js"></script>',content_type='text/html')
        async def asset(request):return web.Response(text=(ROOT/'docs'/('tenant-operator-i18n.js' if request.path=='/i18n.js' else 'tenant-support-inbox.js')).read_text(),content_type='application/javascript')
        async def profile(_):return web.json_response({'member':{**PROFILE,'role':'owner'}})
        async def reply(_):
            state['reply_count']=state.get('reply_count',0)+1;index=str(state['reply_count'])
            event={'id':'e2e-event-'+index,'type':'message.inbound.received','data':{'id':'e2e-reply-'+index,'channelId':CA,'conversationId':GROUP,'isGroup':True,'fromMe':False,'type':'text','text':'CASE:'+consult+' We are investigating'}}
            raw=json.dumps(event).encode();sig='sha256='+hmac.new(b'synthetic-secret',raw,hashlib.sha256).hexdigest()
            return web.json_response({'accepted':await asyncio.to_thread(transport.ingest,raw,sig,event)})
        async def client_attachment(_):
            external_id='connect:'+str(uuid.uuid4())
            db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image')",(image2,A,external_id,'connect:'+CA))
            from tenant_support_inbox_store import record_case_message_membership
            with db.cursor() as cur:record_case_message_membership(cur,A,case,CA,'client',[external_id],'support-group-e2e-followup')
            db.commit()
            return web.json_response({'accepted':True})
        app.router.add_get('/app',index);app.router.add_get('/i18n.js',asset);app.router.add_get('/support.js',asset);app.router.add_get('/api/platform/me',profile);app.router.add_post('/simulate/reply',reply);app.router.add_post('/simulate/client-attachment',client_attachment)
        import logging
        for suffix,resource,methods in [('support-tickets','tickets',('GET',)),('support-tickets/{ticket_id}','ticket',('GET',)),('internal-consultations','consultation',('GET',)),('internal-consultations/groups','consultation_groups',('GET',)),('internal-consultations/{consultation_id}/group','consultation_group',('GET','POST'))]:
            for method in methods:app.router.add_route(method,'/api/platform/ai/'+suffix,handler(member,origin,logging.getLogger(__name__),resource))
        runner=web.AppRunner(app);await runner.setup();await web.SockSite(runner,sock).start();state['runner']=runner;ready.set()
    def run():asyncio.set_event_loop(loop);loop.run_until_complete(start());loop.run_forever()
    thread=threading.Thread(target=run,daemon=True);thread.start();assert ready.wait(10)
    try:
        env={**os.environ,'GROUP_E2E_ORIGIN':origin,'GROUP_E2E_CASE':case,'GROUP_E2E_CONSULT':consult,'GROUP_E2E_IMAGE':image,'GROUP_E2E_IMAGE2':image2}
        completed=subprocess.run(['node',str(ROOT/'tests/support_group_local_e2e.cjs')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=40)
        assert completed.returncode==0,completed.stdout+'\n'+completed.stderr
        assert len(sends)==2 and all(send[0]==GROUP for send in sends)
        assert image2 in sends[1][1] and image not in sends[1][1]
        result=flow.read(PROFILE,consult)
        assert result['customer_draft']=='We are checking.' and result['customer_message_sent'] is False
        assert len(result['replies'])==2
    finally:
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(),loop).result(10)
        loop.call_soon_threadsafe(loop.stop);thread.join(timeout=10);loop.close()
