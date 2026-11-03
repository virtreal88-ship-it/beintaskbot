"""Read-only gate evidence on a disposable local PostgreSQL schema only."""
import json
import os
import subprocess
import sys
import threading
import types
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
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

    def test_synthetic_role_barrier_fences_writes_but_does_not_prove_http_drain(self):
        """Table-level evidence only; never use these synthetic roles in production."""
        from psycopg import sql
        from psycopg.errors import InsufficientPrivilege
        legacy='poc_old_'+uuid.uuid4().hex
        fresh='poc_new_'+uuid.uuid4().hex
        tenant=str(uuid.uuid4())
        request=str(uuid.uuid4())
        table=sql.SQL('{}.saas_operator_copilot_threads').format(sql.Identifier(self.schema))
        with self.psycopg.connect(URL,autocommit=True) as admin:
            for role in (legacy,fresh):
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(role)))
                admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(self.schema),sql.Identifier(role)))
                admin.execute(sql.SQL('GRANT SELECT, UPDATE ON {} TO {}').format(table,sql.Identifier(role)))
                admin.execute(sql.SQL('GRANT SELECT, UPDATE ON {}.saas_chat_ai_runs TO {}').format(sql.Identifier(self.schema),sql.Identifier(role)))
                admin.execute(sql.SQL('GRANT SELECT ON {}.saas_tenant_integrations TO {}').format(sql.Identifier(self.schema),sql.Identifier(role)))
            admin.execute(sql.SQL('INSERT INTO {}(tenant_id,requested_by,channel_id,conversation_id,turns) VALUES(%s,1,%s,%s,%s::jsonb)').format(table),
                          (tenant,'channel','dialog',json.dumps([])))
            admin.execute(sql.SQL('INSERT INTO {}.saas_chat_ai_runs(tenant_id,request_id,config_version,status) VALUES(%s,%s,%s,%s)').format(sql.Identifier(self.schema)),
                          (tenant,request,'v1','sending'))
            admin.execute(sql.SQL('INSERT INTO {}.saas_tenant_integrations VALUES(%s,%s,%s)').format(sql.Identifier(self.schema)),
                          (tenant,'chat_ai','v1'))
        started,locked=threading.Event(),threading.Event()
        def barrier():
            with self.connect() as admin:
                started.set()
                admin.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(table))
                locked.set()
                admin.execute(sql.SQL('REVOKE INSERT, UPDATE, DELETE ON {} FROM {}').format(table,sql.Identifier(legacy)))
                admin.commit()
        try:
            with self.connect() as old:
                old.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(legacy)))
                old.execute(sql.SQL('UPDATE {} SET turns=%s::jsonb WHERE tenant_id=%s').format(table),
                            (json.dumps([{'text':'in-flight'}]),tenant))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending=pool.submit(barrier)
                    self.assertTrue(started.wait(5))
                    self.assertFalse(locked.wait(.25),'barrier should wait for existing table writer')
                    old.commit()
                    pending.result(timeout=5)
            with self.connect() as late_old:
                late_old.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(legacy)))
                with self.assertRaises(InsufficientPrivilege):
                    late_old.execute(sql.SQL('UPDATE {} SET turns=%s::jsonb WHERE tenant_id=%s').format(table),
                                     (json.dumps([{'text':'late'}]),tenant))
                late_old.rollback()
            with self.connect() as late_old:
                late_old.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(legacy)))
                with self.assertRaises(InsufficientPrivilege):
                    late_old.execute(sql.SQL('DELETE FROM {} WHERE tenant_id=%s').format(table),(tenant,))
                late_old.rollback()
            with self.connect() as new:
                new.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(fresh)))
                new.execute(sql.SQL('UPDATE {} SET turns=%s::jsonb WHERE tenant_id=%s').format(table),
                            (json.dumps([{'text':'new'}]),tenant))
                new.commit()
            # A legacy HTTP request that was awaiting its provider (and held no
            # DB lock) is not drained by the barrier. Its actual completion
            # fails rather than safely preserving the answer.
            source=subprocess.run(['git','show','fc75c2899b7b86de42bf2cafdbdd116e9717d821:beintaskbot-main/tenant_operator_copilot.py'],
                                  cwd=ROOT,check=True,text=True,capture_output=True).stdout
            baseline=types.ModuleType('synthetic_old_writer_after_barrier')
            exec(compile(source,'<baseline-copilot>','exec'),baseline.__dict__)
            def old_connection():
                conn=self.connect()
                conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(legacy)))
                return conn
            command={'lead_id':1,'channel_id':'channel','conversation_id':'dialog',
                     'request_id':request,'_agent_binding':'binding'}
            policy=SimpleNamespace(can_use_chat_ai=lambda:True)
            with patch.object(baseline,'_connect',old_connection),patch.object(baseline,'ensure_schema'), \
                 patch.object(baseline,'access',return_value=(tenant,1,policy)),patch.object(baseline,'assert_route'), \
                 patch.object(baseline,'_assigned_context',return_value={}),patch.object(baseline,'_agent_binding',return_value='binding'), \
                 patch('tenant_episode_media.revalidate_sources'):
                with self.assertRaises(InsufficientPrivilege):
                    baseline._finish(tenant,command,1,{'text':'synthetic answer'},[],'question','synthetic answer')
            with self.connect() as admin:
                receipt=admin.execute(sql.SQL('SELECT status,result FROM {}.saas_chat_ai_runs WHERE tenant_id=%s').format(sql.Identifier(self.schema)),(tenant,)).fetchone()
            self.assertEqual(receipt,{'status':'sending','result':None})
            with self.assertRaises(VoiceWriteUnavailable):require_voice_write_ready()
        finally:
            with self.psycopg.connect(URL,autocommit=True) as admin:
                for role in (legacy,fresh):
                    admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                    admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
