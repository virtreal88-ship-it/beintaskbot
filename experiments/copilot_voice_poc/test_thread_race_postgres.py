"""Exercise scoped text/voice thread interactions on isolated PostgreSQL.
Set COPILOT_POC_POSTGRES_URL to a disposable localhost database whose name
starts with copilot_voice_poc; this suite creates/drops one random schema.
"""
import json
import os
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse


URL = os.environ.get('COPILOT_POC_POSTGRES_URL')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'beintaskbot-main'))


@unittest.skipUnless(URL, 'COPILOT_POC_POSTGRES_URL not set')
class ExistingTextWriterRace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlparse(URL)
        if (parsed.scheme not in {'postgres', 'postgresql'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or not parsed.path.lstrip('/').startswith('copilot_voice_poc') or parsed.query or parsed.fragment):
            raise RuntimeError('Refusing non-isolated database URL')
        import psycopg
        from psycopg.rows import dict_row
        cls.psycopg, cls.dict_row = psycopg, dict_row
        cls.schema = 'copilot_poc_' + uuid.uuid4().hex
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute('CREATE SCHEMA ' + cls.schema)
            conn.execute('''CREATE TABLE ''' + cls.schema + '''.saas_chat_ai_runs(
                tenant_id uuid, request_id uuid, config_version text, status text, result jsonb, updated_at timestamptz DEFAULT now())''')
            conn.execute('''CREATE TABLE ''' + cls.schema + '''.saas_tenant_integrations(
                tenant_id uuid, provider text, updated_at text)''')
            conn.execute('''CREATE TABLE ''' + cls.schema + '''.saas_operator_copilot_threads(
                tenant_id uuid, requested_by bigint, channel_id text, conversation_id text,
                turns jsonb, clear_revision bigint NOT NULL DEFAULT 0, updated_at timestamptz DEFAULT now(),
                PRIMARY KEY(tenant_id,requested_by,channel_id,conversation_id))''')
            conn.execute('''CREATE TABLE ''' + cls.schema + '''.saas_crm_messages(
                tenant_id uuid, kommo_lead_id bigint, external_id text, direction text, body text,
                message_type text, happened_at timestamptz DEFAULT now(), created_at timestamptz DEFAULT now(),
                channel text, raw jsonb)''')

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(URL, autocommit=True) as conn:
            conn.execute('DROP SCHEMA ' + cls.schema + ' CASCADE')

    def connect(self):
        return self.psycopg.connect(URL, row_factory=type(self).dict_row, options='-csearch_path='+self.schema)

    def test_existing_reader_scopes_thread_and_filters_agent_binding(self):
        from tenant_operator_copilot import _read_history
        tenant, other_tenant = str(uuid.uuid4()), str(uuid.uuid4())
        actor, channel, conversation, binding = 7, 'channel-1', 'dialog-1', 'binding-1'
        voice = {'role':'employee','text':'synthetic partial','agent_binding':binding,
                 'voice_session_id':'voice-1','transcript_status':'partial'}
        thread_rows = [
            (tenant, actor, channel, conversation, [voice, {'role':'copilot','text':'old-agent','agent_binding':'old'},
                                                    {'role':'employee','text':'unbound'}]),
            (other_tenant, actor, channel, conversation, [{'text':'other-tenant','agent_binding':binding}]),
            (tenant, actor+1, channel, conversation, [{'text':'other-actor','agent_binding':binding}]),
            (tenant, actor, 'channel-2', conversation, [{'text':'other-channel','agent_binding':binding}]),
            (tenant, actor, channel, 'dialog-2', [{'text':'other-dialog','agent_binding':binding}]),
        ]
        with self.connect() as conn:
            for t, a, c, d, turns in thread_rows:
                conn.execute('''INSERT INTO saas_operator_copilot_threads
                    (tenant_id,requested_by,channel_id,conversation_id,turns) VALUES(%s,%s,%s,%s,%s::jsonb)''',
                    (t,a,c,d,json.dumps(turns)))
            for t, lead, c, body in [(tenant,1,channel,'our-customer'),(other_tenant,1,channel,'other-tenant'),
                                     (tenant,2,channel,'other-lead'),(tenant,1,'channel-2','other-channel')]:
                conn.execute('''INSERT INTO saas_crm_messages
                    (tenant_id,kommo_lead_id,external_id,direction,body,message_type,channel,raw)
                    VALUES(%s,%s,%s,'incoming',%s,'text',%s,'{}'::jsonb)''',
                    (t,lead,str(uuid.uuid4()),body,'connect:'+c))
            conn.commit()
            command = {'channel_id':channel,'conversation_id':conversation,'lead_id':1,
                       'mode':'chat','_agent_binding':binding}
            with conn.cursor() as cur:
                rows, turns = _read_history(cur,tenant,actor,command)
            conn.rollback()
        self.assertEqual([row['body'] for row in rows], ['our-customer'])
        self.assertEqual(turns, [voice])

    def test_text_completion_preserves_an_intervening_voice_turn(self):
        import tenant_operator_copilot as copilot
        tenant, request = str(uuid.uuid4()), str(uuid.uuid4())
        channel, conversation, actor, binding = 'synthetic-channel', 'synthetic-dialog', 1, 'agent-binding'
        command = {'lead_id':1, 'channel_id':channel, 'conversation_id':conversation,
                   'request_id':request, '_agent_binding':binding, '_thread_clear_revision':0}
        initial = [{'role':'employee','text':'previous','agent_binding':binding}]
        voice = {'role':'employee','text':'synthetic voice','agent_binding':binding,
                 'voice_session_id':'synthetic-session','transcript_status':'complete'}
        with self.connect() as conn:
            conn.execute('INSERT INTO saas_chat_ai_runs(tenant_id,request_id,config_version,status) VALUES(%s,%s,%s,%s)',
                         (tenant,request,'config-version','sending'))
            conn.execute('INSERT INTO saas_tenant_integrations VALUES(%s,%s,%s)',(tenant,'chat_ai','config-version'))
            conn.execute('''INSERT INTO saas_operator_copilot_threads(tenant_id,requested_by,channel_id,conversation_id,turns)
                            VALUES(%s,%s,%s,%s,%s::jsonb)''',(tenant,actor,channel,conversation,json.dumps(initial)))
            conn.commit()
            # Text request's snapshot lock is released before external I/O.
            snapshot = conn.execute('''SELECT turns FROM saas_operator_copilot_threads
                WHERE tenant_id=%s AND requested_by=%s AND channel_id=%s AND conversation_id=%s FOR UPDATE''',
                (tenant,actor,channel,conversation)).fetchone()['turns']
            conn.commit()
        # A separate writer commits the voice turn while text is in flight.
        with self.connect() as conn:
            conn.execute('''UPDATE saas_operator_copilot_threads SET turns=turns || %s::jsonb
                WHERE tenant_id=%s AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',
                (json.dumps([voice]),tenant,actor,channel,conversation))
            conn.commit()
        policy = SimpleNamespace(can_use_chat_ai=lambda: True)
        with patch.object(copilot, '_connect', self.connect), patch.object(copilot,'ensure_schema'), \
             patch.object(copilot,'access',return_value=(tenant,actor,policy)), patch.object(copilot,'assert_route'), \
             patch.object(copilot,'_assigned_context',return_value={}), patch.object(copilot,'_agent_binding',return_value=binding), \
             patch('tenant_episode_media.revalidate_sources'):
            copilot._finish(tenant,command,actor,{'text':'synthetic text answer'},snapshot,'synthetic question','synthetic text answer')
        with self.connect() as conn:
            rows=conn.execute('''SELECT turns FROM saas_operator_copilot_threads
                WHERE tenant_id=%s AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',
                (tenant,actor,channel,conversation)).fetchone()['turns']
        self.assertIn(voice, rows)
        self.assertEqual([row['text'] for row in rows], ['previous','synthetic voice','synthetic question','synthetic text answer'])

    def test_two_concurrent_first_writers_preserve_both_turns(self):
        from tenant_operator_copilot_threads import append
        tenant, actor, channel, dialog = str(uuid.uuid4()), 9, 'new-channel', 'new-dialog'
        barrier = threading.Barrier(2)
        def writer(value):
            with self.connect() as conn:
                barrier.wait(timeout=5)
                with conn.cursor() as cur:
                    append(cur,tenant,actor,channel,dialog,0,[{'text':value,'agent_binding':'binding'}])
                conn.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(writer, ['synthetic-text','synthetic-voice']))
        with self.connect() as conn:
            turns=conn.execute('''SELECT turns FROM saas_operator_copilot_threads
                WHERE tenant_id=%s AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',
                (tenant,actor,channel,dialog)).fetchone()['turns']
        self.assertCountEqual([turn['text'] for turn in turns], ['synthetic-text','synthetic-voice'])

    def test_clear_tombstone_rejects_old_writer_and_allows_new_revision(self):
        from tenant_operator_copilot_threads import append, clear, ThreadCleared
        tenant, actor, channel, dialog = str(uuid.uuid4()), 9, 'new-channel', 'new-dialog'
        with self.connect() as conn:
            with conn.cursor() as cur: clear(cur,tenant,actor,channel,dialog)
            conn.commit()
        with self.connect() as conn:
            with self.assertRaises(ThreadCleared):
                with conn.cursor() as cur: append(cur,tenant,actor,channel,dialog,0,[{'text':'stale'}])
            conn.rollback()
        with self.connect() as conn:
            with conn.cursor() as cur: append(cur,tenant,actor,channel,dialog,1,[{'text':'new'}])
            conn.commit()
            row=conn.execute('''SELECT turns,clear_revision FROM saas_operator_copilot_threads
                WHERE tenant_id=%s AND requested_by=%s AND channel_id=%s AND conversation_id=%s''',
                (tenant,actor,channel,dialog)).fetchone()
        self.assertEqual(row['clear_revision'],1)
        self.assertEqual(row['turns'],[{'text':'new'}])
