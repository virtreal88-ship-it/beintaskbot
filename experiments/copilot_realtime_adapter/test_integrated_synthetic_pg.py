"""Single-checkout synthetic integration; no API, credentials or audio storage.

Requires disposable localhost VOICE_AUTH_PG_URL. All gate overrides exist only
inside this test; they are not a production admission or safety guarantee.
"""
import asyncio
import base64
import json
import threading
import uuid
from pathlib import Path
from unittest.mock import patch
from test_real_authority_pg import RealVoiceAuthority
from session import Session
from test_session import Lease
from tenant_operator_realtime_wire import SyntheticRealtimeWire
from websocket import make_handler
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import tenant_operator_voice_context as voice
import tenant_operator_copilot as copilot
import tenant_ai_agent_run_store as knowledge_store
import tenant_ai_spend_budget as tenant_spend
import tenant_operator_copilot_threads as history_candidate
import tenant_voice_test_grant as grant_candidate
import tenant_operator_voice_orchestration as production
from tenant_linear_policy import TenantLinearError

ROOT=Path(__file__).resolve().parents[2]
class IntegratedSynthetic(RealVoiceAuthority):
    def setUp(self):
        super().setUp()
        self.history=history_candidate
        self.grant=grant_candidate
        self.conn.execute('''ALTER TABLE saas_crm_messages ADD COLUMN id uuid DEFAULT gen_random_uuid(),ADD COLUMN kommo_lead_id bigint,ADD COLUMN external_id text,
          ADD COLUMN direction text,ADD COLUMN body text,ADD COLUMN message_type text,ADD COLUMN happened_at timestamptz DEFAULT now(),
          ADD COLUMN created_at timestamptz DEFAULT now();
          CREATE TABLE saas_operator_copilot_threads(tenant_id uuid,requested_by bigint,channel_id text,conversation_id text,
            turns jsonb,clear_revision bigint NOT NULL DEFAULT 0,updated_at timestamptz DEFAULT now(),
            PRIMARY KEY(tenant_id,requested_by,channel_id,conversation_id));
          CREATE TABLE saas_ai_knowledge_sources(tenant_id uuid,id uuid,title text,active boolean,agent_id uuid,provenance jsonb,updated_at timestamptz);
          CREATE TABLE saas_ai_knowledge_chunks(tenant_id uuid,source_id uuid,content text,search_vector tsvector,chunk_no integer);
          CREATE TABLE saas_knowledge_candidates(tenant_id uuid,id uuid,reviewed_by bigint,status text);
          CREATE TABLE saas_knowledge_candidate_sources(tenant_id uuid,candidate_id uuid,source_id uuid,agent_id uuid);''')
        tenant_spend.ensure_schema(self.conn)
        self.conn.execute((ROOT/'experiments/voice_budget_poc/voice_test_grant.sql').read_text())
        self.conn.execute('UPDATE saas_voice_test_grant SET enabled=true')
        self.conn.execute("INSERT INTO saas_crm_messages(tenant_id,channel,raw,kommo_lead_id,external_id,direction,body,message_type) VALUES(%s,%s,'{}',41,'customer-1','incoming','synthetic customer','text')",(self.tenant,'connect:'+self.channel))
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('''DROP TABLE IF EXISTS saas_voice_test_hold,saas_voice_test_session,saas_voice_test_grant,
          saas_ai_spend_reservations,saas_operator_copilot_threads,saas_ai_knowledge_sources,saas_ai_knowledge_chunks,
          saas_knowledge_candidates,saas_knowledge_candidate_sources CASCADE;
          DELETE FROM saas_crm_messages;
          ALTER TABLE saas_crm_messages DROP COLUMN IF EXISTS id,DROP COLUMN IF EXISTS kommo_lead_id,DROP COLUMN IF EXISTS external_id,
            DROP COLUMN IF EXISTS direction,DROP COLUMN IF EXISTS body,DROP COLUMN IF EXISTS message_type,
            DROP COLUMN IF EXISTS happened_at,DROP COLUMN IF EXISTS created_at''')
        self.conn.commit();super().tearDown()

    def scoped(self):
        conn=self.connect();conn.execute('SET search_path TO '+self.schema);return conn

    def test_actual_server_composition_fails_closed_and_reconciles_after_revoke(self):
        profile={'tenant_id':self.tenant,'telegram_id':7};initial=self.admit()
        self.conn.commit()  # release synthetic membership/route locks before revoke
        with self.scoped() as conn,conn.cursor() as cur:
            self.history.append(cur,self.tenant,7,self.channel,self.conversation,0,initial.agent_binding,
                [{'role':'employee','text':'synthetic text','agent_binding':initial.agent_binding}])

        async def execute():
            authority=await production.ScopedAuthority.create(profile,self.route)
            context=production.CopilotContext(authority)
            # Internal diagnostic only; the public read refuses until source
            # versions are sealed, so no prompt is delivered to a provider.
            snapshot=await asyncio.to_thread(context._read)
            self.assertEqual(snapshot['extra_context']['prior_internal_copilot_turns'][0]['text'],'synthetic text')
            self.assertNotIn('CONTEXT_ONLY_NO_CREDENTIAL',json.dumps(snapshot))
            with self.assertRaises(TenantLinearError):await context.read_authorized(authority.session_scope)
            with self.scoped() as conn:
                conn.execute("UPDATE saas_crm_messages SET body='synthetic changed' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            # Even when membership/agent/config are unchanged, a changed
            # source must never reach provider before source sealing exists.
            await authority.check(authority.session_scope,'response_dispatch')
            with self.assertRaises(TenantLinearError):await context.read_authorized(authority.session_scope)
            with self.assertRaises(TenantLinearError):await production.VoiceHistory(authority).append({'text':'synthetic voice'})
            with self.assertRaises(TenantLinearError):await production.prepare_server_session(profile,self.route,None,None)
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_voice_test_hold').fetchone()['n'],0)

            session_id=str(uuid.uuid4());budget=production.ScopedBudget(authority,session_id)
            request={'request_id':str(uuid.uuid4()),'tenant_id':self.tenant,'session_id':session_id,'actor_id':7,
                     'channel_id':self.channel,'conversation_id':self.conversation,'response_number':1,'executor':'copilot-voice-server'}
            reserved=await budget.reserve(request);self.assertEqual(reserved['state'],'reserved')
            claimed=await budget.claim(request);self.assertEqual(claimed['state'],'claimed')
            # A separate transaction can see the claim before any dispatcher.
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT state FROM saas_voice_test_hold WHERE request_id=%s',(request['request_id'],)).fetchone()['state'],'claimed')
                conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            with self.assertRaises(TenantLinearError):await budget.reserve({**request,'request_id':str(uuid.uuid4()),'response_number':2})
            with self.assertRaises(TenantLinearError):await budget.mark_unknown({**request,'conversation_id':'different'})
            unknown=await budget.mark_unknown(request);self.assertEqual(unknown['state'],'unknown')
            actual=await budget.note_actual(request,11);self.assertEqual(actual['state'],'settled')
            with self.scoped() as conn:
                hold=conn.execute('SELECT state,reserved_microusd FROM saas_voice_test_hold WHERE request_id=%s',(request['request_id'],)).fetchone()
                self.assertEqual(hold['state'],'settled');self.assertGreater(hold['reserved_microusd'],0)

        with patch.object(voice,'_connect',self.scoped),patch.object(production,'_connect',self.scoped),patch.object(knowledge_store,'_connect',self.scoped),\
             patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),patch.object(self.grant,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            asyncio.run(execute())

    def test_cancelled_background_claim_keeps_committed_hold(self):
        profile={'tenant_id':self.tenant,'telegram_id':7};entered=threading.Event();release=threading.Event();original=self.grant.claim
        def blocked_claim(cur,item):
            row=original(cur,item);entered.set()
            if not release.wait(3):raise RuntimeError('synthetic test timeout')
            return row
        async def execute():
            authority=await production.ScopedAuthority.create(profile,self.route)
            session_id=str(uuid.uuid4());budget=production.ScopedBudget(authority,session_id)
            request={'request_id':str(uuid.uuid4()),'tenant_id':self.tenant,'session_id':session_id,'actor_id':7,
                     'channel_id':self.channel,'conversation_id':self.conversation,'response_number':1,'executor':'copilot-voice-server'}
            await budget.reserve(request)
            pending=asyncio.create_task(budget.claim(request));self.assertTrue(await asyncio.to_thread(entered.wait,1))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
            release.set()
            state=None
            for _ in range(100):
                with self.scoped() as conn:
                    state=conn.execute('SELECT state FROM saas_voice_test_hold WHERE request_id=%s',(request['request_id'],)).fetchone()['state']
                if state=='claimed':break
                await asyncio.sleep(.01)
            self.assertEqual(state,'claimed')
            self.assertIsNone(await budget.claim(request))
            # No provider exists in the production orchestrator path.
            with self.assertRaises(TenantLinearError):await production.prepare_server_session(profile,self.route,None,None)

        try:
            with patch.object(voice,'_connect',self.scoped),patch.object(production,'_connect',self.scoped),\
                 patch.object(self.grant,'claim',blocked_claim),patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),\
                 patch.object(self.grant,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
                asyncio.run(execute())
        finally:release.set()

    def test_synthetic_text_voice_interruption_text_with_real_admission_budget_and_history(self):
        profile={'tenant_id':self.tenant,'telegram_id':7};scope=self.admit();outer=self
        with self.scoped() as conn,conn.cursor() as cur:
            self.history.append(cur,self.tenant,7,self.channel,self.conversation,0,scope.agent_binding,
                [{'role':'employee','text':'synthetic text before voice','agent_binding':scope.agent_binding}])

        def read_context():
            with outer.scoped() as conn,conn.cursor() as cur:
                current=voice.authorize(cur,profile,outer.route)
                assert current==scope
                command={'mode':'chat','prompt':'','lead_id':41,'channel_id':outer.channel,'conversation_id':outer.conversation,
                         '_agent_id':outer.agent,'_agent_binding':scope.agent_binding}
                agent=copilot._assigned_context(cur,outer.tenant,command)
                rows,turns=copilot._read_history(cur,outer.tenant,7,command)
            # The thread lock/transaction is gone before preparing prompt.
            with patch.object(knowledge_store,'_connect',outer.scoped):
                prepared=copilot._draft_context(outer.tenant,7,command,{'enabled':True},{'updated_at':'synthetic'},
                    {'api_key':'NOT_A_REAL_KEY'},'valid-model',agent,rows,turns,context_only=True)
            # ManualExecution includes even a placeholder key; keep it private.
            return {'existing_copilot_instructions':prepared['instructions'],'lines':prepared['lines'],
                    'extra_context':prepared['extra_context']}

        class Authority:
            async def check(self,_scope,_stage):
                with outer.scoped() as conn,conn.cursor() as cur:
                    if voice.authorize(cur,profile,outer.route)!=scope:raise ValueError('scope_changed')

        class Context:
            async def read_authorized(self,_scope):return read_context()
            async def revalidate(self,_scope):return True

        class Budget:
            def __init__(self):self.events=[]
            async def operation(self,name,data):
                with outer.scoped() as conn,conn.cursor() as cur:
                    if voice.authorize(cur,profile,outer.route)!=scope:raise ValueError('scope_changed')
                    row=getattr(outer.grant,name)(cur,data)
                self.events.append((name,dict(data)))
                return row
            async def reserve(self,data):return await self.operation('reserve',data)
            async def claim(self,data):return await self.operation('claim',data)
            async def mark_unknown(self,data):return await self.operation('mark_unknown',data)

        class Socket:
            synthetic_only=True
            def __init__(self):self.sent=[];self.closed=False
            async def send_json(self,event):self.sent.append(event)
            async def receive_json(self):
                if not self.sent:return {'type':'session.created'}
                return {'type':'session.updated','session':self.sent[-1]['session']}
            async def close(self):self.closed=True

        class DispatchGuard:
            def __init__(self):self.seen=set()
            async def consume(self,request,receipt):
                if request['request_id'] in self.seen:return False
                with outer.scoped() as conn,conn.cursor() as cur:
                    if voice.authorize(cur,profile,outer.route)!=scope:return False
                    cur.execute('''SELECT h.state,h.fingerprint,h.tenant_reservation_id,h.reserved_microusd,h.quote_version,h.model,
                      t.state AS tenant_state,t.reserved_microusd AS tenant_amount
                      FROM saas_voice_test_hold h JOIN saas_ai_spend_reservations t
                        ON t.tenant_id=h.tenant_id AND t.id=h.tenant_reservation_id
                      WHERE h.request_id=%s::uuid AND h.tenant_id=%s::uuid''',(request['request_id'],request['tenant_id']))
                    row=cur.fetchone()
                ok=(row and row['state']=='claimed' and row['tenant_state']=='unknown' and
                    row['fingerprint']==receipt['fingerprint'] and row['reserved_microusd']==receipt['reserved_microusd'] and
                    row['tenant_amount']==receipt['reserved_microusd'] and str(row['tenant_reservation_id'])==str(receipt['tenant_reservation_id']) and
                    row['model']==receipt['model'] and row['quote_version']==receipt['quote_version'])
                if ok:self.seen.add(request['request_id'])
                # This in-memory marker is test-only; it is not a durable
                # cross-process one-time dispatch capability.
                return bool(ok)

        async def execute():
            budget=Budget();socket=Socket();provider=SyntheticRealtimeWire(socket,DispatchGuard());sessions=[]
            # Provider is a synthetic event source; there is no model or ASR.
            snapshot=read_context();self.assertEqual(snapshot['extra_context']['prior_internal_copilot_turns'][0]['text'],'synthetic text before voice')
            self.assertNotIn('NOT_A_REAL_KEY',json.dumps(snapshot))
            async def authenticate(request):
                if request.headers.get('X-Synthetic-Auth')!='yes':raise ValueError('denied')
                await Authority().check(None,'connect')
                return (self.tenant,7,self.channel,self.conversation)
            def build(server_scope,client):
                session=Session(server_scope,authority=Authority(),ledger=budget,lease=Lease(),context=Context(),
                                provider=provider,client=client,max_seconds=10,idle_seconds=8)
                sessions.append(session);return session
            app=web.Application()
            app.router.add_get('/voice',make_handler(allowed_origins={'https://crm.pro.az'},authenticate=authenticate,build_session=build))
            browser=TestClient(TestServer(app));await browser.start_server()
            ws=await browser.ws_connect('/voice',headers={'Origin':'https://crm.pro.az','X-Synthetic-Auth':'yes'})
            self.assertEqual((await ws.receive_json())['type'],'ready');session=sessions[0]
            # Test-only transcription fixture. Production has no ASR handler or
            # voice writer, so this does not prove those integrations.
            with self.scoped() as conn,conn.cursor() as cur:
                self.assertEqual(voice.authorize(cur,profile,self.route),scope)
                self.history.append(cur,self.tenant,7,self.channel,self.conversation,0,scope.agent_binding,[{
                  'role':'employee','text':'synthetic non-exact voice transcript','agent_binding':scope.agent_binding,
                  'voice_session_id':session.session_id,'turn_sequence':0,'transcript_status':'partial','transcript_quality':'automatic_unverified'}])
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            await ws.send_bytes(bytes(480))
            for _ in range(50):
                if any(e.get('type')=='input_audio_buffer.append' for e in socket.sent):break
                await asyncio.sleep(.01)
            self.assertTrue(any(e.get('type')=='input_audio_buffer.append' for e in socket.sent))
            await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
            await session.receive_provider({'type':'response.created','response':{'id':'synthetic-response'}})
            await session.receive_provider({'type':'response.output_audio.delta','response_id':'synthetic-response','item_id':'synthetic-item',
                                            'content_index':0,'delta':base64.b64encode(bytes(480)).decode()})
            self.assertEqual((await ws.receive_json())['type'],'audio')
            await session.receive_provider({'type':'input_audio_buffer.speech_started'})
            self.assertEqual((await ws.receive_json())['type'],'interrupt')
            await ws.send_json({'type':'played','item_id':'synthetic-item','played_ms':0})
            for _ in range(50):
                if socket.sent[-1].get('type')=='conversation.item.truncate':break
                await asyncio.sleep(.01)
            self.assertEqual(socket.sent[-1]['type'],'conversation.item.truncate')
            await session.receive_provider({'type':'response.done','response':{'id':'synthetic-response'}})
            self.assertEqual(socket.sent[-1]['audio_end_ms'],0)
            self.assertEqual([e[0] for e in budget.events],['reserve','claim','mark_unknown'])
            await ws.send_json({'type':'end'});self.assertEqual((await ws.receive_json())['type'],'ended')
            await browser.close()
            text=read_context()['extra_context']['prior_internal_copilot_turns']
            self.assertEqual([x['text'] for x in text],['synthetic text before voice','synthetic non-exact voice transcript'])
            self.assertEqual(text[-1]['transcript_status'],'partial');self.assertEqual(text[-1]['transcript_quality'],'automatic_unverified')
            self.assertIn('Voice transcript metadata describes imperfect evidence',read_context()['existing_copilot_instructions'])
            with self.scoped() as conn:
                hold=conn.execute('SELECT state,reserved_microusd FROM saas_voice_test_hold').fetchone()
                self.assertEqual(hold['state'],'unknown');self.assertGreater(hold['reserved_microusd'],0)

        with patch.object(self.grant,'ENFORCEMENT_VERIFIED',True),patch.object(self.grant,'RECONCILIATION_VERIFIED',True),patch.object(tenant_spend,'limit_microusd',return_value=0):
            asyncio.run(execute())
