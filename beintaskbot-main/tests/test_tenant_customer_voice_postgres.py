"""Actual receipt/history SQL against a disposable, explicitly local database."""
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import Mock, patch

import psycopg
from psycopg.rows import dict_row
from tenant_customer_voice_service import record_acceptance, visible_receipt, save_visible_status
from tenant_connect_transport import _pause_unverified_outbound_audio
from tenant_linear_policy import TenantLinearError

BASE = os.environ.get('VOICE_TEST_POSTGRES_URL')
TENANT = 'a9c11a16-01d5-469a-a6c1-9a726975ae42'
CHANNEL = '9644b2f2-786c-43f4-b58e-2b9b5f69d554'
REQUEST = '79d6467b-3ca6-4557-9e12-b9da059c0611'
MESSAGE = '6b9f1ac1-d693-4f9f-a400-d9b8372306f8'
JID = '994500000000@s.whatsapp.net'
SESSION = {'tenant_id': TENANT, 'telegram_id': 20}
ITEM = {'key': (TENANT,20,REQUEST), 'lead_id':7, 'connection':['connect',CHANNEL,JID]}
RECEIPT = {'message_id':MESSAGE, 'provider_message_id':'PROVIDER123', 'provider_accepted':True,
           'delivery_state':'sent'}


@unittest.skipUnless(BASE, 'VOICE_TEST_POSTGRES_URL not set')
class VoiceHistoryPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlsplit(BASE)
        if parsed.hostname not in {'127.0.0.1','localhost','::1'}:
            raise RuntimeError('Synthetic tests require an explicitly local database')
        cls.name = 'test_voice_worker_' + uuid.uuid4().hex
        with psycopg.connect(BASE,autocommit=True) as admin:
            admin.execute('CREATE DATABASE "' + cls.name + '"')
        cls.dsn = urlunsplit((parsed.scheme,parsed.netloc,'/'+cls.name,parsed.query,parsed.fragment))
        with psycopg.connect(cls.dsn) as conn:
            conn.execute('''CREATE TABLE saas_chat_send_receipts(tenant_id uuid,actor_id bigint,request_id uuid,lead_id bigint,
                state text,route jsonb,message_id text,updated_at timestamptz DEFAULT now(),PRIMARY KEY(tenant_id,actor_id,request_id))''')
            conn.execute('''CREATE TABLE saas_crm_messages(id uuid PRIMARY KEY,tenant_id uuid,kommo_lead_id bigint,external_id text,
                direction text,channel text,author_name text,body text,message_type text,media_url text,happened_at timestamptz,raw jsonb,
                UNIQUE(tenant_id,external_id))''')
            conn.execute('''CREATE TABLE saas_tenant_audit_events(id uuid,tenant_id uuid,actor_telegram_id bigint,action text,
                entity_type text,entity_id text,payload jsonb)''')
            conn.execute('''CREATE TABLE saas_ai_conversation_state(tenant_id uuid,provider text,channel_id text,conversation_id text,
                state text,reason text,revision int default 1,updated_at timestamptz default now(),
                UNIQUE(tenant_id,provider,channel_id,conversation_id))''')
            conn.execute('''CREATE TABLE saas_ai_agent_jobs(tenant_id uuid,provider text,channel_id text,conversation_id text,
                state text,lease_token uuid,lease_until timestamptz,error_code text,updated_at timestamptz default now())''')
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(BASE,autocommit=True) as admin:
            admin.execute('DROP DATABASE "' + cls.name + '" WITH (FORCE)')

    def setUp(self):
        self.factory = lambda: psycopg.connect(self.dsn,row_factory=dict_row)
        with self.factory() as conn:
            conn.execute('TRUNCATE saas_chat_send_receipts,saas_crm_messages,saas_tenant_audit_events,saas_ai_conversation_state,saas_ai_agent_jobs')
            conn.execute('INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,state,route) VALUES(%s,%s,%s,%s,%s,%s::jsonb)',
                         (TENANT,20,REQUEST,7,'unknown',json.dumps({'voice_protocol':True,'channel_id':CHANNEL,'conversation_id':JID})))
            conn.commit()
        self.mark = Mock()
        self.patches = [patch('tenant_customer_voice_service._connect',self.factory),
            patch('tenant_customer_voice_service.authority',return_value=({'tenant_id':TENANT,'telegram_id':20,'display_name':'Employee'},ITEM['connection'],{'route':{'channel_id':CHANNEL,'conversation_id':JID,'api_key':'synthetic'}})),
            patch('tenant_ai_agents_schema.ensure_schema'),patch('tenant_chat_send_schema.ensure_schema'),
            patch('tenant_connect_transport._schema'),patch('tenant_ai_dialog_scope.lock_dialog'),
            patch('tenant_ai_agent_connect.mark_operator_reply',self.mark)]
        for item in self.patches:item.start()
        self.addCleanup(patch.stopall)

    def test_confirmed_receipt_is_idempotent_and_retains_best_delivery_status(self):
        record_acceptance(SESSION,ITEM,RECEIPT)
        record_acceptance(SESSION,ITEM,{**RECEIPT,'delivery_state':'read'})
        record_acceptance(SESSION,ITEM,RECEIPT)
        with self.factory() as conn:
            rows=conn.execute('SELECT * FROM saas_crm_messages').fetchall()
            saved=conn.execute('SELECT * FROM saas_chat_send_receipts').fetchone()
            audit=conn.execute('SELECT * FROM saas_tenant_audit_events').fetchall()
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['author_name'],'Employee')
        self.assertEqual(rows[0]['raw']['status'],'read'); self.assertEqual(rows[0]['raw']['verified_employee_id'],20)
        self.assertEqual(saved['state'],'accepted'); self.assertEqual(saved['message_id'],MESSAGE)
        self.assertEqual(len(audit),1);self.assertEqual(audit[0]['actor_telegram_id'],20)
        self.mark.assert_called_once()

    def test_ambiguous_audio_pauses_only_active_exact_scope_and_cancels_its_jobs(self):
        other='00000000-0000-0000-0000-000000000999'
        with self.factory() as conn:
            for tenant,channel,conversation,state in [(TENANT,CHANNEL,JID,'active'),(TENANT,other,JID,'active'),
                                                       (TENANT,CHANNEL,'other@s.whatsapp.net','active'),(other,CHANNEL,JID,'active')]:
                conn.execute('INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason) VALUES(%s,\'connect\',%s,%s,%s,\'existing\')',(tenant,channel,conversation,state))
            for state in ['queued','processing','drafted']:
                conn.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,provider,channel_id,conversation_id,state) VALUES(%s,'connect',%s,%s,%s)",(TENANT,CHANNEL,JID,state))
            conn.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,provider,channel_id,conversation_id,state) VALUES(%s,'connect',%s,%s,'queued')",(other,CHANNEL,JID))
            with conn.cursor() as cur:_pause_unverified_outbound_audio(cur,TENANT,CHANNEL,JID)
            states=conn.execute('SELECT tenant_id::text,channel_id,conversation_id,state,reason FROM saas_ai_conversation_state').fetchall()
            jobs=conn.execute('SELECT tenant_id::text,state,error_code FROM saas_ai_agent_jobs ORDER BY tenant_id,state').fetchall()
            conn.commit()
        changed=[row for row in states if row['state']=='paused'];self.assertEqual(len(changed),1)
        self.assertEqual(changed[0]['reason'],'unverified_outbound_audio')
        self.assertEqual(sum(row['state']=='skipped' and row['error_code']=='unverified_outbound_audio' for row in jobs),2)
        self.assertEqual(sum(row['state']=='queued' and row['tenant_id']==other for row in jobs),1)

    def test_ambiguous_audio_creates_paused_gate_and_never_changes_human(self):
        with self.factory() as conn:
            with conn.cursor() as cur:_pause_unverified_outbound_audio(cur,TENANT,CHANNEL,JID)
            row=conn.execute('SELECT state,reason FROM saas_ai_conversation_state').fetchone()
            self.assertEqual(row,{'state':'paused','reason':'unverified_outbound_audio'})
            conn.execute("UPDATE saas_ai_conversation_state SET state='paused',reason='automatic_assignment_pending'")
            with conn.cursor() as cur:_pause_unverified_outbound_audio(cur,TENANT,CHANNEL,JID)
            row=conn.execute('SELECT state,reason FROM saas_ai_conversation_state').fetchone()
            self.assertEqual(row,{'state':'paused','reason':'unverified_outbound_audio'})
            conn.execute("UPDATE saas_ai_conversation_state SET state='paused',reason='automatic_assignment_no_eligible_agent'")
            with conn.cursor() as cur:_pause_unverified_outbound_audio(cur,TENANT,CHANNEL,JID)
            row=conn.execute('SELECT state,reason FROM saas_ai_conversation_state').fetchone()
            self.assertEqual(row,{'state':'paused','reason':'unverified_outbound_audio'})
            conn.execute("UPDATE saas_ai_conversation_state SET state='human',reason='explicit_handoff'")
            with conn.cursor() as cur:_pause_unverified_outbound_audio(cur,TENANT,CHANNEL,JID)
            row=conn.execute('SELECT state,reason FROM saas_ai_conversation_state').fetchone()
            self.assertEqual(row,{'state':'human','reason':'explicit_handoff'});conn.commit()

    def test_early_unknown_provider_echo_is_reconciled_in_place(self):
        row_id=str(uuid.uuid4())
        with self.factory() as conn:
            conn.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,
                body,message_type,media_url,happened_at,raw) VALUES(%s,%s,7,%s,'outgoing',%s,'Unknown sender','','audio','',now(),%s::jsonb)''',
                (row_id,TENANT,'connect:'+MESSAGE,'connect:'+CHANNEL,json.dumps({'externalMessageId':'PROVIDER123','source':'unknown','status':'read'})))
            conn.commit()
        record_acceptance(SESSION,ITEM,RECEIPT)
        with self.factory() as conn:rows=conn.execute('SELECT * FROM saas_crm_messages').fetchall()
        self.assertEqual(len(rows),1); self.assertEqual(str(rows[0]['id']),row_id)
        self.assertEqual(rows[0]['author_name'],'Employee'); self.assertEqual(rows[0]['raw']['voice_receipt_request_id'],REQUEST)
        self.assertEqual(rows[0]['raw']['status'],'sent')
        self.mark.assert_called_once()

    def test_mismatched_provider_id_does_not_overwrite_history(self):
        with self.factory() as conn:
            conn.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,
                body,message_type,media_url,happened_at,raw) VALUES(%s,%s,7,%s,'outgoing',%s,'Other','','audio','',now(),%s::jsonb)''',
                (uuid.uuid4(),TENANT,'connect:'+MESSAGE,'connect:'+CHANNEL,json.dumps({'externalMessageId':'OTHER123'})))
            conn.commit()
        with self.assertRaises(TenantLinearError):record_acceptance(SESSION,ITEM,RECEIPT)
        with self.factory() as conn:row=conn.execute('SELECT * FROM saas_chat_send_receipts').fetchone()
        self.assertEqual(row['state'],'unknown');self.mark.assert_not_called()

    def test_audit_failure_rolls_back_history_and_acceptance(self):
        with self.factory() as conn:
            conn.execute('ALTER TABLE saas_tenant_audit_events ADD CONSTRAINT synthetic_failure CHECK (false)')
            conn.commit()
        try:
            with self.assertRaises(psycopg.Error):record_acceptance(SESSION,ITEM,RECEIPT)
            with self.factory() as conn:
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_crm_messages').fetchone()['n'],0)
                self.assertEqual(conn.execute('SELECT state FROM saas_chat_send_receipts').fetchone()['state'],'unknown')
        finally:
            with self.factory() as conn:
                conn.execute('ALTER TABLE saas_tenant_audit_events DROP CONSTRAINT synthetic_failure');conn.commit()

    def test_concurrent_reconciliation_commits_one_history_and_one_handoff(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=[pool.submit(record_acceptance,SESSION,ITEM,RECEIPT) for _ in range(2)]
            for result in results:result.result(timeout=10)
        with self.factory() as conn:rows=conn.execute('SELECT * FROM saas_crm_messages').fetchall()
        self.assertEqual(len(rows),1);self.mark.assert_called_once()

    def test_authorised_viewer_can_refresh_proven_status_without_replaying_handoff(self):
        record_acceptance(SESSION,ITEM,RECEIPT)
        with self.factory() as conn:row=conn.execute('SELECT id FROM saas_crm_messages').fetchone()
        item=visible_receipt(SESSION,7,str(row['id']))
        self.assertEqual(save_visible_status(SESSION,item,{**RECEIPT,'delivery_state':'read'}),'read')
        self.assertEqual(save_visible_status(SESSION,item,RECEIPT),'read')
        with self.factory() as conn:row=conn.execute('SELECT raw FROM saas_crm_messages').fetchone()
        self.assertEqual(row['raw']['status'],'read');self.mark.assert_called_once()
        with self.assertRaises(TenantLinearError):
            save_visible_status(SESSION,item,{**RECEIPT,'provider_message_id':'OTHER123'})
