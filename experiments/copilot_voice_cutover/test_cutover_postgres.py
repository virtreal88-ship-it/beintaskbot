"""Read-only gate evidence on a disposable local PostgreSQL schema only."""
import json
import os
import subprocess
import sys
import types
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

from cutover_gate import observe_receipts, require_voice_write_ready, VoiceWriteUnavailable

URL = os.environ.get('COPILOT_POC_POSTGRES_URL')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'beintaskbot-main'))


@unittest.skipUnless(URL, 'COPILOT_POC_POSTGRES_URL not set')
class CutoverPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed=urlparse(URL)
        if (parsed.scheme not in {'postgres','postgresql'} or parsed.hostname not in {'127.0.0.1','localhost','::1'}
                or not parsed.path.lstrip('/').startswith('copilot_voice_poc') or parsed.query or parsed.fragment):
            raise RuntimeError('Refusing non-isolated database URL')
        import psycopg
        from psycopg.rows import dict_row
        cls.psycopg,cls.dict_row=psycopg,dict_row
        cls.schema='copilot_poc_'+uuid.uuid4().hex
        with psycopg.connect(URL,autocommit=True) as conn:
            conn.execute('CREATE SCHEMA '+cls.schema)
            conn.execute('''CREATE TABLE '''+cls.schema+'''.saas_chat_ai_runs(
              tenant_id uuid,request_id uuid,status text,config_version text,result jsonb,provider text DEFAULT 'connect',
              updated_at timestamptz DEFAULT now())''')
            conn.execute('''CREATE TABLE '''+cls.schema+'''.saas_tenant_integrations(tenant_id uuid,provider text,updated_at text)''')
            conn.execute('''CREATE TABLE '''+cls.schema+'''.saas_operator_copilot_threads(
              tenant_id uuid,requested_by bigint,channel_id text,conversation_id text,turns jsonb,
              clear_revision bigint NOT NULL DEFAULT 0,updated_at timestamptz DEFAULT now(),
              PRIMARY KEY(tenant_id,requested_by,channel_id,conversation_id))''')

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(URL,autocommit=True) as conn:
            conn.execute('DROP SCHEMA '+cls.schema+' CASCADE')

    def connect(self):
        return self.psycopg.connect(URL,row_factory=type(self).dict_row,options='-csearch_path='+self.schema)

    def test_uncommitted_legacy_admission_is_invisible_to_receipt_snapshot(self):
        tenant,request=str(uuid.uuid4()),str(uuid.uuid4())
        with self.connect() as observer,self.connect() as old_admission:
            with observer.cursor() as cur: before=observe_receipts(cur)
            observer.commit()
            old_admission.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,status) VALUES(%s,%s,'sending')",(tenant,request))
            with observer.cursor() as cur: during=observe_receipts(cur)
            observer.commit()
            self.assertEqual(during,before)
            with self.assertRaises(VoiceWriteUnavailable):require_voice_write_ready()
            old_admission.commit()
            with observer.cursor() as cur: after=observe_receipts(cur)
            self.assertEqual(after.sending,before.sending+1)

    def test_actual_old_writer_overwrites_new_thread_in_mixed_version(self):
        """Passing reproduces a release blocker; it is not a safety pass."""
        baseline='fc75c2899b7b86de42bf2cafdbdd116e9717d821'
        source=subprocess.run(['git','show',baseline+':beintaskbot-main/tenant_operator_copilot.py'],
                              cwd=ROOT,check=True,text=True,capture_output=True).stdout
        legacy=types.ModuleType('synthetic_baseline_copilot')
        exec(compile(source,'<baseline-copilot>','exec'),legacy.__dict__)
        tenant,request=str(uuid.uuid4()),str(uuid.uuid4())
        channel,dialog,actor,binding='channel','dialog',1,'binding'
        command={'lead_id':1,'channel_id':channel,'conversation_id':dialog,'request_id':request,'_agent_binding':binding}
        with self.connect() as conn:
            conn.execute('INSERT INTO saas_chat_ai_runs(tenant_id,request_id,config_version,status) VALUES(%s,%s,%s,%s)',
                         (tenant,request,'v1','sending'))
            conn.execute('INSERT INTO saas_tenant_integrations VALUES(%s,%s,%s)',(tenant,'chat_ai','v1'))
            conn.execute('''INSERT INTO saas_operator_copilot_threads
              (tenant_id,requested_by,channel_id,conversation_id,turns) VALUES(%s,%s,%s,%s,%s::jsonb)''',
              (tenant,actor,channel,dialog,json.dumps([{'role':'employee','text':'newer','agent_binding':binding}])))
            conn.commit()
        policy=SimpleNamespace(can_use_chat_ai=lambda:True)
        with patch.object(legacy,'_connect',self.connect),patch.object(legacy,'ensure_schema'), \
             patch.object(legacy,'access',return_value=(tenant,actor,policy)),patch.object(legacy,'assert_route'), \
             patch.object(legacy,'_assigned_context',return_value={}),patch.object(legacy,'_agent_binding',return_value=binding), \
             patch('tenant_episode_media.revalidate_sources'):
            legacy._finish(tenant,command,actor,{'text':'legacy answer'},[],'legacy question','legacy answer')
        with self.connect() as conn:
            turns=conn.execute('SELECT turns FROM saas_operator_copilot_threads WHERE tenant_id=%s',(tenant,)).fetchone()['turns']
        self.assertEqual([turn['text'] for turn in turns],['legacy question','legacy answer'])
