"""Optional two-repository HTTP test; synthetic provider and localhost databases only."""
import asyncio
import hashlib
import hmac
import json
import os
import select
import socket
import subprocess
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import patch

import psycopg
from cryptography.fernet import Fernet
from psycopg.rows import dict_row
from aiohttp import ClientSession, FormData, web
from aiohttp.test_utils import TestClient, TestServer

BASE=os.environ.get('VOICE_TEST_POSTGRES_URL')
CONNECT_ROOT=os.environ.get('VOICE_CROSS_STACK_CONNECT_ROOT')
TENANT='a9c11a16-01d5-469a-a6c1-9a726975ae42'
SESSION={'tenant_id':TENANT,'telegram_id':20}


@unittest.skipUnless(BASE and CONNECT_ROOT,'Requires local PostgreSQL and the separate Connect checkout')
class CrossStackVoice(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        parsed=urlsplit(BASE)
        if parsed.hostname not in {'127.0.0.1','localhost','::1'}:raise RuntimeError('localhost test database only')
        cls.name='test_voice_worker_full_'+uuid.uuid4().hex
        with psycopg.connect(BASE,autocommit=True) as admin:admin.execute('CREATE DATABASE "'+cls.name+'"')
        cls.dsn=urlunsplit((parsed.scheme,parsed.netloc,'/'+cls.name,parsed.query,parsed.fragment))
        env={**os.environ,'CONNECT_TEST_POSTGRES_URL':BASE,'DATABASE_URL':BASE,'NODE_ENV':'test'}
        cls.process=subprocess.Popen(['node','test/voice-cross-stack-fixture.mjs'],cwd=CONNECT_ROOT,env=env,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            if not select.select([cls.process.stdout],[],[],30)[0]:raise RuntimeError('Synthetic Connect fixture did not start')
            cls.fixture=json.loads(cls.process.stdout.readline())
            if urlsplit(cls.fixture['address']).hostname not in {'127.0.0.1','localhost','::1'}:raise RuntimeError('local Connect fixture only')
            if urlsplit(cls.fixture['disabledAddress']).hostname not in {'127.0.0.1','localhost','::1'}:raise RuntimeError('local Connect fixture only')
            cls.key=Fernet.generate_key()
            cls.environment=patch.dict(os.environ,{'DATABASE_URL':cls.dsn,'TENANT_ENCRYPTION_KEY':cls.key.decode(),
                'AI_AGENTS_ENABLED':'false','AI_SIMULATION_ENABLED':'false','CUSTOMER_VOICE_ENABLED':'true'})
            cls.environment.start()
            import tenant_platform_schema as platform_schema
            import tenant_connect_transport as transport
            import tenant_ai_agents_schema as ai_schema
            import tenant_chat_send_schema as send_schema
            import tenant_dialog_consultations as consultations
            platform_schema._schema_ready=False;ai_schema._ready=False
            cls.transport=transport;cls.old_connect_url=transport.CONNECT_URL;transport.CONNECT_URL=cls.fixture['address']
            with psycopg.connect(cls.dsn,row_factory=dict_row) as conn:
                platform_schema._ensure_schema(conn);transport._schema(conn);ai_schema.ensure_schema(conn);send_schema.ensure_schema(conn);consultations.schema(conn);conn.commit()
                conn.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Synthetic','active',20)",(TENANT,TENANT))
                conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,active) VALUES(%s,20,'Employee','owner',true)",(TENANT,))
                conn.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,active) VALUES(%s,21,'No access','operator',true)",(TENANT,))
                secret=Fernet(cls.key).encrypt(json.dumps({'api_key':cls.fixture['token'],'webhook_secret':'synthetic-only-secret'}).encode())
                conn.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s,'connect','connected',%s)",(TENANT,secret))
                conn.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active) VALUES(%s,%s,'Synthetic','connected',true)",(TENANT,cls.fixture['channelId']))
                conn.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s,%s,%s,7)",(TENANT,cls.fixture['channelId'],cls.fixture['conversationId']))
                conn.execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name) VALUES(%s,7,'Synthetic')",(TENANT,));conn.commit()
        except BaseException:
            cls._cleanup();raise

    @classmethod
    def _cleanup(cls):
        if hasattr(cls,'transport'):cls.transport.CONNECT_URL=cls.old_connect_url
        if hasattr(cls,'environment'):cls.environment.stop()
        if getattr(cls,'process',None):
            cls.process.terminate()
            try:cls.process.wait(timeout=15)
            except subprocess.TimeoutExpired:cls.process.kill();cls.process.wait(timeout=5)
            if cls.process.stdout:cls.process.stdout.close()
            if cls.process.stderr:cls.process.stderr.close()
        if getattr(cls,'name',None):
            with psycopg.connect(BASE,autocommit=True) as admin:admin.execute('DROP DATABASE IF EXISTS "'+cls.name+'" WITH (FORCE)')
        import tenant_platform_schema as platform_schema
        platform_schema._schema_ready=False

    @classmethod
    def tearDownClass(cls):cls._cleanup()

    async def asyncSetUp(self):
        from tenant_customer_voice_api import handler,receipt_handler,status_handler,capability_handler
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.origin='http://127.0.0.1:'+str(port)
        app=web.Application();member=lambda request:{'tenant_id':TENANT,'telegram_id':int(request.headers.get('X-Test-Actor','20'))}
        send=handler(member,self.origin,MockLogger());receipt=receipt_handler(member,MockLogger());status=status_handler(member,MockLogger())
        app.router.add_post('/send',send);app.router.add_get('/receipt',receipt);app.router.add_get('/status',status)
        app.router.add_post('/api/platform/crm/chat/send-voice',send)
        app.router.add_get('/api/platform/crm/chat/voice-receipt',receipt)
        app.router.add_get('/api/platform/crm/chat/voice-status',status)
        app.router.add_get('/api/platform/crm/chat/voice-capability',capability_handler(member,MockLogger()))
        docs=Path(__file__).resolve().parents[1]/'docs'
        app.router.add_static('/assets',docs)
        async def page(_):
            scripts=['tenant-voice-recorder.js','tenant-microphone-lease.js','tenant-customer-voice-store.js','tenant-customer-voice-ui.js']
            setup={'provider':'connect','lead_id':7,'channel_id':self.fixture['channelId'],'conversation_id':self.fixture['conversationId']}
            html='''<!doctype html><meta charset="utf-8"><div id="view-dialogs" class="active"><div class="operator-composer"><textarea id="chatText"></textarea><div class="operator-composer-actions"><button id="sendChat">send</button></div></div></div><script>
              window.tenantI18n={language:'ru'};window.fixtureRoute=ROUTE;
              window.tenantOperator={member:{tenant_id:'TENANT',telegram_id:20},get currentDialog(){return window.fixtureRoute},openDialog:async()=>{window.refreshed=true}};
              const held=[];Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{const c=new AudioContext(),o=c.createOscillator(),d=c.createMediaStreamDestination();o.connect(d);o.start();held.push([c,o]);return d.stream;}});
              </script>'''.replace('ROUTE',json.dumps(setup)).replace('TENANT',TENANT)
            html+=''.join('<script src="/assets/'+name+'"></script>' for name in scripts)
            html+="<script>document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'))</script>"
            return web.Response(text=html,content_type='text/html')
        app.router.add_get('/app',page)
        self.client=TestClient(TestServer(app,host='127.0.0.1',port=port));await self.client.start_server()
        self.control=ClientSession()

    async def asyncTearDown(self):
        await self.control.close();await self.client.close()

    def form(self,request_id,user='20'):
        data=FormData();data.add_field('command',json.dumps({'request_id':request_id,'lead_id':7,
            'expected_tenant_id':TENANT,'expected_user_id':user}))
        data.add_field('file',(Path(__file__).parent/'fixtures/connect-voice-tone.ogg').read_bytes(),filename='voice.ogg',content_type='audio/ogg')
        return data

    async def control_post(self,path,body):
        async with self.control.post(self.fixture['address']+'/fixture/'+path,json=body) as response:
            self.assertEqual(response.status,200);return await response.json()

    async def count(self):
        async with self.control.get(self.fixture['address']+'/fixture/count') as response:return (await response.json())['calls']

    async def test_one_send_and_verified_history_status(self):
        await self.control_post('mode',{'mode':'accept'});request_id=str(uuid.uuid4());before=await self.count()
        response=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        payload=await response.json();self.assertEqual(response.status,200,f'{payload}');self.assertTrue(payload['provider_accepted'])
        duplicate=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        self.assertTrue((await duplicate.json())['provider_accepted']);self.assertEqual(await self.count(),before+1)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            rows=conn.execute("SELECT * FROM saas_crm_messages WHERE direction='outgoing' AND message_type='audio'").fetchall()
        matches=[row for row in rows if row['raw'].get('voice_receipt_request_id')==request_id]
        self.assertEqual(len(matches),1);self.assertEqual(matches[0]['author_name'],'Employee')
        await self.control_post('read',{'requestId':request_id})
        query={'lead_id':'7','message_row_id':str(matches[0]['id']),'expected_tenant_id':TENANT,'expected_user_id':'20'}
        refreshed=await self.client.get('/status',params=query);self.assertEqual((await refreshed.json())['delivery_state'],'read')
        # Keep this shared synthetic conversation isolated from later agent
        # handoff tests; the receipt assertions above already exercised it.
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DELETE FROM saas_crm_messages WHERE id=%s',(matches[0]['id'],))
            conn.execute('DELETE FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s',(TENANT,self.fixture['channelId'],self.fixture['conversationId']))
            conn.commit()

    async def test_unknown_stays_unknown_and_never_retries(self):
        await self.control_post('mode',{'mode':'unknown'});request_id=str(uuid.uuid4());before=await self.count()
        response=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        payload=await response.json();self.assertIn(response.status,(200,503),f'{payload}')
        if response.status==200:self.assertEqual(payload.get('state'),'unknown')
        else:self.assertTrue(payload.get('keep_request'))
        query={'lead_id':'7','request_id':request_id,'expected_tenant_id':TENANT,'expected_user_id':'20'}
        pending=await self.client.get('/receipt',params=query);pending_payload=await pending.json()
        self.assertEqual(pending.status,200,f'{pending_payload}');self.assertEqual(pending_payload.get('state'),'unknown')
        duplicate=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        duplicate_payload=await duplicate.json();self.assertEqual(duplicate.status,409,f'{duplicate_payload}')
        self.assertTrue(duplicate_payload.get('keep_request'))
        refreshed=await self.client.get('/receipt',params=query);self.assertEqual((await refreshed.json()).get('state'),'unknown')
        blocked=await self.client.post('/send',data=self.form(str(uuid.uuid4())),headers={'Origin':self.origin})
        blocked_payload=await blocked.json();self.assertEqual(blocked.status,409,f'{blocked_payload}')
        self.assertEqual(blocked_payload.get('error_code'),'voice_attribution_unresolved')
        self.assertEqual(await self.count(),before+1)
        self.assertEqual((await self.control_post('cleanup-unknown',{'requestId':request_id}))['deleted'],1)

    async def test_unknown_voice_does_not_block_an_unrelated_text(self):
        from tenant_chat_send_service import run as send_text
        await self.control_post('mode',{'mode':'unknown'});request_id=str(uuid.uuid4());before=await self.count()
        response=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        payload=await response.json();self.assertIn(response.status,(200,503),f'{payload}')
        if response.status==200:self.assertEqual(payload.get('state'),'unknown')
        else:self.assertTrue(payload.get('keep_request'))
        self.assertEqual(await self.count(),before+1,f'unknown voice was not submitted once: {payload}')
        # Exercise the actual text service and PostgreSQL receipt reservation;
        # only its external transport is synthetic.
        with patch('tenant_chat_send_service.send_connect',return_value='SYNTHETIC_TEXT'), patch('tenant_chat_send_service.record_connect_outbound'):
            result=await send_text(SESSION,{'request_id':str(uuid.uuid4()),'lead_id':7,'text':'Synthetic text'},None,None,MockLogger())
        self.assertEqual(result['state'],'accepted');self.assertEqual(await self.count(),before+1)
        duplicate=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        self.assertNotEqual(duplicate.status,200);self.assertEqual(await self.count(),before+1)
        self.assertEqual((await self.control_post('cleanup-unknown',{'requestId':request_id}))['deleted'],1)

    async def test_kill_switch_preserves_unknown_read_only_without_send(self):
        await self.control_post('mode',{'mode':'unknown'});request_id=str(uuid.uuid4());before=await self.count()
        response=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        payload=await response.json();self.assertIn(response.status,(200,503),f'{payload}')
        if response.status==200:self.assertEqual(payload.get('state'),'unknown')
        else:self.assertTrue(payload.get('keep_request'))
        query={'lead_id':'7','request_id':request_id,'expected_tenant_id':TENANT,'expected_user_id':'20'}
        with patch.dict(os.environ,{'CUSTOMER_VOICE_ENABLED':'false'}):
            self.transport.CONNECT_URL=self.fixture['disabledAddress']
            try:
                pending=await self.client.get('/receipt',params=query);pending_payload=await pending.json()
                self.assertEqual(pending.status,200,f'{pending_payload}');self.assertEqual(pending_payload.get('state'),'unknown')
                rejected=await self.client.post('/send',data=self.form(str(uuid.uuid4())),headers={'Origin':self.origin})
                self.assertEqual(rejected.status,404);self.assertEqual(await self.count(),before+1)
            finally:self.transport.CONNECT_URL=self.fixture['address']
        self.assertEqual((await self.control_post('cleanup-unknown',{'requestId':request_id}))['deleted'],1)

    async def test_signed_ambiguous_audio_pauses_ai_without_claiming_author_and_replay_does_not_repause(self):
        await self.control_post('mode',{'mode':'unknown'});request_id=str(uuid.uuid4())
        response=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin})
        payload=await response.json();self.assertIn(response.status,(200,503),f'{payload}')
        if response.status==200:self.assertEqual(payload.get('state'),'unknown')
        else:self.assertTrue(payload.get('keep_request'))
        provider_id='PHONE'+uuid.uuid4().hex[:18].upper()
        observation=await self.control_post('outbound',{'providerId':provider_id})
        self.assertEqual(observation['result']['source'],'unknown');event=observation['event']
        self.assertEqual(event['data']['source'],'unknown');self.assertTrue(event['data']['authorshipUncertain'])
        self.assertNotIn('originClientMessageId',event['data'])
        channel=self.fixture['channelId'];conversation=self.fixture['conversationId']
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason)
                VALUES(%s,'connect',%s,%s,'active','test') ON CONFLICT(tenant_id,provider,channel_id,conversation_id)
                DO UPDATE SET state='active',reason='test' ''',(TENANT,channel,conversation));conn.commit()
            conn.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason)
                VALUES(%s,'connect',%s,'994599999999@s.whatsapp.net','active','other') ON CONFLICT DO NOTHING''',(TENANT,channel));conn.commit()
        raw=json.dumps(event,separators=(',',':')).encode();signature='sha256='+hmac.new(b'synthetic-only-secret',raw,hashlib.sha256).hexdigest()
        self.assertTrue(await asyncio.to_thread(self.transport.ingest,raw,signature,event))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            state=conn.execute("SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(TENANT,channel,conversation)).fetchone()
            other=conn.execute("SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id='994599999999@s.whatsapp.net'",(TENANT,channel)).fetchone()
            row=conn.execute("SELECT author_name,raw FROM saas_crm_messages WHERE tenant_id=%s AND external_id=%s",(TENANT,'connect:'+event['data']['id'])).fetchone()
            conn.execute("UPDATE saas_ai_conversation_state SET state='active',reason='manual_resume' WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(TENANT,channel,conversation));conn.commit()
        self.assertEqual(state,{'state':'paused','reason':'unverified_outbound_audio'})
        self.assertEqual(other,{'state':'active','reason':'other'})
        self.assertEqual(row['author_name'],'Unknown sender');self.assertNotIn('verified_employee_id',row['raw']);self.assertFalse(row['raw'].get('ai_authored',False))
        self.assertTrue(await asyncio.to_thread(self.transport.ingest,raw,signature,event))
        replay_event={**event,'id':str(uuid.uuid4())};replay_raw=json.dumps(replay_event,separators=(',',':')).encode()
        replay_signature='sha256='+hmac.new(b'synthetic-only-secret',replay_raw,hashlib.sha256).hexdigest()
        self.assertTrue(await asyncio.to_thread(self.transport.ingest,replay_raw,replay_signature,replay_event))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            replay=conn.execute("SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(TENANT,channel,conversation)).fetchone()
        self.assertEqual(replay,{'state':'active','reason':'manual_resume'})
        ai=await self.control_post('outbound',{'providerId':'KNOWN_AI'+uuid.uuid4().hex[:15].upper()});ai_event=ai['event']
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,
                body,message_type,media_url,happened_at,raw) VALUES(%s,%s,7,%s,'outgoing',%s,'AI Agent','','audio','',now(),%s::jsonb)''',
                (uuid.uuid4(),TENANT,'connect:'+ai_event['data']['id'],'connect:'+channel,json.dumps({'run_id':'synthetic-run'})));conn.commit()
        ai_raw=json.dumps(ai_event,separators=(',',':')).encode();ai_signature='sha256='+hmac.new(b'synthetic-only-secret',ai_raw,hashlib.sha256).hexdigest()
        self.assertTrue(await asyncio.to_thread(self.transport.ingest,ai_raw,ai_signature,ai_event))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            ai_state=conn.execute("SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(TENANT,channel,conversation)).fetchone()
        self.assertEqual(ai_state,{'state':'active','reason':'manual_resume'})
        self.assertEqual((await self.control_post('cleanup-unknown',{'requestId':request_id}))['deleted'],1)

    async def test_missing_dialog_access_stops_before_provider(self):
        request_id=str(uuid.uuid4());data=self.form(request_id,'21')
        before=await self.count()
        response=await self.client.post('/send',data=data,headers={'Origin':self.origin,'X-Test-Actor':'21'})
        self.assertEqual(response.status,403);self.assertEqual(await self.count(),before)

    async def test_b_verified_alias_returns_receipt_to_original_worker_scope(self):
        await self.control_post('mode',{'mode':'accept'});alias=(await self.control_post('alias',{}))['alias']
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND lead_id=7',(alias,TENANT));conn.commit()
        try:
            before=await self.count();request_id=str(uuid.uuid4())
            response=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin});payload=await response.json()
            self.assertEqual(response.status,200,payload);self.assertTrue(payload['provider_accepted']);self.assertEqual(await self.count(),before+1)
            async with self.control.get(self.fixture['address']+'/v1/channels/'+self.fixture['channelId']+'/messages/'+payload['message_id']+'/media',
                    params={'conversationId':alias},headers={'x-api-key':self.fixture['token']}) as media:
                content=await media.read();self.assertEqual(media.status,200,content[:300]);self.assertTrue(content.startswith(b'OggS'))
        finally:
            with psycopg.connect(self.dsn) as conn:
                conn.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s AND lead_id=7',(self.fixture['conversationId'],TENANT));conn.commit()

    async def test_early_evolution_echo_and_ack_reconcile_one_worker_history_row(self):
        await self.control_post('mode',{'mode':'wait'});request_id=str(uuid.uuid4());before=await self.count()
        pending=asyncio.create_task(self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin}))
        try:
            for _ in range(200):
                if await self.count()>before:break
                await asyncio.sleep(.02)
            self.assertEqual(await self.count(),before+1)
            echo=await self.control_post('early-echo',{})
            row_id=str(uuid.uuid4())
            # Emulate the already-ingested Worker echo; Connect's real synthetic
            # Evolution webhook and early ACK were exercised above.
            with psycopg.connect(self.dsn) as conn:
                conn.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,
                    body,message_type,media_url,happened_at,raw) VALUES(%s,%s,7,%s,'outgoing',%s,'Unknown sender','','audio','',now(),%s::jsonb)''',
                    (row_id,TENANT,'connect:'+echo['messageId'],'connect:'+self.fixture['channelId'],
                     json.dumps({'externalMessageId':echo['providerId'],'source':'unknown','status':'read'})))
                conn.commit()
            await self.control_post('release',{})
            response=await pending;payload=await response.json()
            self.assertEqual(response.status,200,payload);self.assertTrue(payload['provider_accepted'])
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
                rows=conn.execute("SELECT * FROM saas_crm_messages WHERE external_id=%s",('connect:'+echo['messageId'],)).fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(str(rows[0]['id']),row_id)
            self.assertEqual(rows[0]['author_name'],'Employee');self.assertEqual(rows[0]['raw']['voice_receipt_request_id'],request_id)
            self.assertEqual(rows[0]['raw']['status'],'read');self.assertEqual(await self.count(),before+1)
        finally:
            if not pending.done():await self.control_post('release',{});await pending

    async def test_access_revoked_during_send_prevents_history_until_restored(self):
        await self.control_post('mode',{'mode':'wait'});request_id=str(uuid.uuid4());before=await self.count()
        pending=asyncio.create_task(self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin}))
        try:
            for _ in range(200):
                if await self.count()>before:break
                await asyncio.sleep(.02)
            self.assertEqual(await self.count(),before+1)
            with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=20',(TENANT,));conn.commit()
            await self.control_post('release',{})
            response=await pending;result=await response.json();self.assertEqual(response.status,503);self.assertTrue(result['keep_request'])
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
                rows=conn.execute("SELECT raw FROM saas_crm_messages WHERE message_type='audio'").fetchall()
            self.assertFalse(any(row['raw'].get('voice_receipt_request_id')==request_id for row in rows))
        finally:
            with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s AND telegram_id=20',(TENANT,));conn.commit()
            if not pending.done():await self.control_post('release',{});await pending
        query={'lead_id':'7','request_id':request_id,'expected_tenant_id':TENANT,'expected_user_id':'20'}
        recovered=await self.client.get('/receipt',params=query);self.assertTrue((await recovered.json())['provider_accepted'])
        self.assertEqual(await self.count(),before+1)

    async def test_browser_to_worker_and_connect_with_synthetic_audio(self):
        await self.control_post('mode',{'mode':'accept'});before=await self.count()
        env={**os.environ,'VOICE_WORKER_FIXTURE_URL':self.origin}
        process=await asyncio.create_subprocess_exec('node',str(Path(__file__).parent/'test_customer_voice_cross_stack.cjs'),
            cwd=str(Path(__file__).resolve().parents[1]),env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,stderr=await asyncio.wait_for(process.communicate(),timeout=30)
        self.assertEqual(process.returncode,0,(stdout+stderr).decode(errors='replace')[-2000:])
        self.assertEqual(await self.count(),before+1)

    async def test_z_unknown_audio_with_single_agent_cannot_be_auto_resumed(self):
        from tenant_ai_agent_connect import reconcile_unassigned_connect_dialog
        channel=self.fixture['channelId'];conversation=self.fixture['conversationId'];agent=str(uuid.uuid4())
        await self.control_post('mode',{'mode':'unknown'});request_id=str(uuid.uuid4())
        submission=await self.client.post('/send',data=self.form(request_id),headers={'Origin':self.origin});submission_data=await submission.json()
        self.assertIn(submission.status,(200,503),f'{submission_data}')
        if submission.status==200:self.assertEqual(submission_data.get('state'),'unknown')
        else:self.assertTrue(submission_data.get('keep_request'))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s AND kommo_lead_id=7 AND channel=%s",(TENANT,'connect:'+channel))
            conn.execute("DELETE FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(TENANT,channel,conversation))
            conn.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by,enabled,mode,role,scopes) VALUES(%s,%s,'Synthetic',20,true,'automatic','primary',%s::jsonb)",
                         (agent,TENANT,json.dumps([{'provider':'connect','channel_id':channel}])));conn.commit()
        observation=await self.control_post('outbound',{'providerId':'SINGLE_AGENT'+uuid.uuid4().hex[:14].upper()});event=observation['event']
        raw=json.dumps(event,separators=(',',':')).encode();signature='sha256='+hmac.new(b'synthetic-only-secret',raw,hashlib.sha256).hexdigest()
        self.assertTrue(await asyncio.to_thread(self.transport.ingest,raw,signature,event))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            row=conn.execute('SELECT state,reason,agent_id::text FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s',(TENANT,channel,conversation)).fetchone()
            with conn.cursor() as cur:reconcile_unassigned_connect_dialog(cur,TENANT,channel,conversation)
            after=conn.execute('SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s',(TENANT,channel,conversation)).fetchone();conn.commit()
        self.assertEqual(row,{'state':'paused','reason':'unverified_outbound_audio','agent_id':agent})
        self.assertEqual(after,{'state':'paused','reason':'unverified_outbound_audio'})
        self.assertEqual((await self.control_post('cleanup-unknown',{'requestId':request_id}))['deleted'],1)


class MockLogger:
    def error(self,*args,**kwargs):pass
    def warning(self,*args,**kwargs):pass
