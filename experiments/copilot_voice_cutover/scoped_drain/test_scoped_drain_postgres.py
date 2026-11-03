"""Synthetic phase-A protocol tests; never use a remote PostgreSQL."""
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
from urllib.parse import urlparse
from unittest.mock import patch

from protocol import admit, promote, resolve_v1

URL = os.environ.get('COPILOT_POC_POSTGRES_URL')
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'beintaskbot-main'))


@unittest.skipUnless(URL, 'COPILOT_POC_POSTGRES_URL not set')
class ScopedDrainPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = urlparse(URL)
        if (parsed.scheme not in {'postgres','postgresql'} or parsed.hostname not in {'127.0.0.1','localhost','::1'}
                or not parsed.path.lstrip('/').startswith('copilot_voice_poc') or parsed.query or parsed.fragment):
            raise RuntimeError('Refusing non-isolated database URL')
        import psycopg
        from psycopg.rows import dict_row
        cls.psycopg,cls.dict_row=psycopg,dict_row
        cls.schema='copilot_scope_'+uuid.uuid4().hex
        with psycopg.connect(URL,autocommit=True) as conn:
            conn.execute('CREATE SCHEMA '+cls.schema)
            conn.execute('''CREATE TABLE '''+cls.schema+'''.experiment_scopes(
              tenant uuid NOT NULL,actor bigint NOT NULL,channel text NOT NULL,conversation text NOT NULL,
              version int NOT NULL DEFAULT 1 CHECK (version IN (1,2)),
              PRIMARY KEY(tenant,actor,channel,conversation))''')
            conn.execute('''CREATE TABLE '''+cls.schema+'''.experiment_leases(
              tenant uuid NOT NULL,actor bigint NOT NULL,channel text NOT NULL,conversation text NOT NULL,
              lease_id uuid NOT NULL, PRIMARY KEY(tenant,actor,channel,conversation,lease_id),
              FOREIGN KEY(tenant,actor,channel,conversation) REFERENCES '''+cls.schema+'''.experiment_scopes)''')
            conn.execute('''CREATE TABLE '''+cls.schema+'''.saas_operator_copilot_threads(
              tenant_id uuid,requested_by bigint,channel_id text,conversation_id text,turns jsonb,
              clear_revision bigint NOT NULL DEFAULT 0,updated_at timestamptz DEFAULT now(),
              PRIMARY KEY(tenant_id,requested_by,channel_id,conversation_id))''')
            conn.execute('''CREATE TABLE '''+cls.schema+'''.saas_chat_ai_runs(
              tenant_id uuid,request_id uuid,status text,config_version text,result jsonb,updated_at timestamptz DEFAULT now())''')
            conn.execute('''CREATE TABLE '''+cls.schema+'''.saas_tenant_integrations(tenant_id uuid,provider text,updated_at text)''')

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(URL,autocommit=True) as conn:
            conn.execute('DROP SCHEMA '+cls.schema+' CASCADE')

    def connect(self):
        return self.psycopg.connect(URL,row_factory=type(self).dict_row,options='-csearch_path='+self.schema)

    def test_pending_legacy_never_blocks_further_text_but_blocks_voice_transition(self):
        scope=(str(uuid.uuid4()),42,'channel','dialog')
        old,second=str(uuid.uuid4()),str(uuid.uuid4())
        with self.connect() as conn:
            self.assertEqual(admit(conn.cursor(),scope,old),1); conn.commit()
        with self.connect() as conn:
            self.assertFalse(promote(conn.cursor(),scope)); conn.commit()
        with self.connect() as conn:
            self.assertEqual(admit(conn.cursor(),scope,second),1); conn.commit()
        with self.connect() as conn:
            resolve_v1(conn.cursor(),scope,old); conn.commit()
        with self.connect() as conn:
            self.assertFalse(promote(conn.cursor(),scope)); conn.commit()
        with self.connect() as conn:
            resolve_v1(conn.cursor(),scope,second); conn.commit()
        with self.connect() as conn:
            self.assertTrue(promote(conn.cursor(),scope)); conn.commit()
        with self.connect() as conn:
            self.assertEqual(admit(conn.cursor(),scope,str(uuid.uuid4())),2); conn.commit()

    def test_admit_racing_transition_cannot_issue_v1_after_v2(self):
        for _ in range(25):
            scope=(str(uuid.uuid4()),1,'channel','dialog')
            original,new=str(uuid.uuid4()),str(uuid.uuid4())
            with self.connect() as conn:
                admit(conn.cursor(),scope,original); resolve_v1(conn.cursor(),scope,original); conn.commit()
            barrier=threading.Barrier(2)
            def admission():
                with self.connect() as conn:
                    barrier.wait(); version=admit(conn.cursor(),scope,new); conn.commit(); return version
            def transition():
                with self.connect() as conn:
                    barrier.wait(); result=promote(conn.cursor(),scope); conn.commit(); return result
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(admission); b=pool.submit(transition)
                version,promoted=a.result(),b.result()
            self.assertEqual((version,promoted) in {(1,False),(2,True)},True)

    def test_v2_clear_fence_and_scoped_voice_text_append(self):
        from tenant_operator_copilot_threads import append,clear,ThreadCleared
        scope=(str(uuid.uuid4()),7,'channel','conversation')
        legacy=str(uuid.uuid4()); binding='synthetic-binding'
        with self.connect() as conn:
            admit(conn.cursor(),scope,legacy); resolve_v1(conn.cursor(),scope,legacy)
            self.assertTrue(promote(conn.cursor(),scope)); conn.commit()
        def turn(text,role='employee'):
            return {'role':role,'text':text,'agent_binding':binding}
        with self.connect() as conn:
            append(conn.cursor(),*scope,0,binding,[turn('text before clear')]); conn.commit()
        with self.connect() as conn:
            clear(conn.cursor(),*scope); conn.commit()
        with self.connect() as conn:
            with self.assertRaises(ThreadCleared):
                append(conn.cursor(),*scope,0,binding,[turn('stale')])
            conn.rollback()
        with self.connect() as conn:
            voice={**turn('voice after clear'),'voice_session_id':str(uuid.uuid4()),
                   'turn_sequence':1,'transcript_status':'partial','transcript_quality':'automatic_unverified'}
            append(conn.cursor(),*scope,1,binding,[voice,turn('text after clear','copilot')]); conn.commit()
        with self.connect() as conn:
            row=conn.execute('SELECT turns FROM saas_operator_copilot_threads WHERE tenant_id=%s',(scope[0],)).fetchone()
            self.assertEqual([x['text'] for x in row['turns']],['voice after clear','text after clear'])
            self.assertEqual(row['turns'][0]['transcript_status'],'partial')

    def test_lost_lease_keeps_scope_v1_without_suppressing_text(self):
        scope=(str(uuid.uuid4()),1,'channel','conversation')
        with self.connect() as conn:
            admit(conn.cursor(),scope,str(uuid.uuid4())); conn.commit()
        # No TTL/reaper: absence of evidence is not proof the old writer finished.
        for _ in range(2):
            with self.connect() as conn:
                self.assertFalse(promote(conn.cursor(),scope)); conn.commit()
            with self.connect() as conn:
                request=str(uuid.uuid4())
                self.assertEqual(admit(conn.cursor(),scope,request),1)
                resolve_v1(conn.cursor(),scope,request); conn.commit()

    def test_actual_legacy_finish_precedes_promotion_and_v2_voice(self):
        from tenant_operator_copilot_threads import append
        source=subprocess.run(['git','show','fc75c2899b7b86de42bf2cafdbdd116e9717d821:beintaskbot-main/tenant_operator_copilot.py'],
                              cwd=ROOT,check=True,text=True,capture_output=True).stdout
        legacy=types.ModuleType('scoped_drain_legacy'); exec(compile(source,'<legacy>','exec'),legacy.__dict__)
        tenant,request=str(uuid.uuid4()),str(uuid.uuid4())
        scope=(tenant,1,'channel','dialog'); binding='synthetic-binding'
        command={'lead_id':1,'channel_id':scope[2],'conversation_id':scope[3],
                 'request_id':request,'_agent_binding':binding}
        with self.connect() as conn:
            self.assertEqual(admit(conn.cursor(),scope,request),1)
            conn.execute('INSERT INTO saas_chat_ai_runs(tenant_id,request_id,status,config_version) VALUES(%s,%s,%s,%s)',
                         (tenant,request,'sending','v1'))
            conn.execute('INSERT INTO saas_tenant_integrations VALUES(%s,%s,%s)',(tenant,'chat_ai','v1'))
            conn.commit()
        with self.connect() as conn:
            self.assertFalse(promote(conn.cursor(),scope)); conn.commit()
        policy=SimpleNamespace(can_use_chat_ai=lambda:True)
        with patch.object(legacy,'_connect',self.connect),patch.object(legacy,'ensure_schema'), \
             patch.object(legacy,'access',return_value=(tenant,1,policy)),patch.object(legacy,'assert_route'), \
             patch.object(legacy,'_assigned_context',return_value={}),patch.object(legacy,'_agent_binding',return_value=binding), \
             patch('tenant_episode_media.revalidate_sources'):
            legacy._finish(tenant,command,1,{'text':'legacy answer'},[],'legacy question','legacy answer')
        with self.connect() as conn:
            resolve_v1(conn.cursor(),scope,request)
            self.assertTrue(promote(conn.cursor(),scope)); conn.commit()
        voice={'role':'employee','text':'voice after transition','agent_binding':binding,
               'voice_session_id':str(uuid.uuid4()),'turn_sequence':1,
               'transcript_status':'partial','transcript_quality':'automatic_unverified'}
        with self.connect() as conn:
            append(conn.cursor(),*scope,0,binding,[voice]); conn.commit()
        with self.connect() as conn:
            turns=conn.execute('SELECT turns FROM saas_operator_copilot_threads WHERE tenant_id=%s',(tenant,)).fetchone()['turns']
            self.assertEqual([x['text'] for x in turns],['legacy question','legacy answer','voice after transition'])

    def test_v1_lease_does_not_fix_actual_legacy_clear_resurrection(self):
        """Passing reproduces a blocker: tracking alone is not a clear fence."""
        source=subprocess.run(['git','show','fc75c2899b7b86de42bf2cafdbdd116e9717d821:beintaskbot-main/tenant_operator_copilot.py'],
                              cwd=ROOT,check=True,text=True,capture_output=True).stdout
        legacy=types.ModuleType('scoped_clear_legacy'); exec(compile(source,'<legacy>','exec'),legacy.__dict__)
        tenant,request=str(uuid.uuid4()),str(uuid.uuid4()); scope=(tenant,1,'channel','dialog'); binding='synthetic-binding'
        command={'lead_id':1,'channel_id':scope[2],'conversation_id':scope[3],
                 'request_id':request,'_agent_binding':binding}
        snapshot=[{'role':'employee','text':'must remain cleared','agent_binding':binding}]
        with self.connect() as conn:
            admit(conn.cursor(),scope,request)
            conn.execute('INSERT INTO saas_chat_ai_runs(tenant_id,request_id,status,config_version) VALUES(%s,%s,%s,%s)',
                         (tenant,request,'sending','v1'))
            conn.execute('INSERT INTO saas_tenant_integrations VALUES(%s,%s,%s)',(tenant,'chat_ai','v1'))
            conn.execute('''INSERT INTO saas_operator_copilot_threads
              (tenant_id,requested_by,channel_id,conversation_id,turns) VALUES(%s,%s,%s,%s,%s::jsonb)''',(*scope,json.dumps(snapshot)))
            conn.commit()
        with self.connect() as conn:
            # Exact legacy scoped DELETE. A clear must itself have a lease in a
            # real integration; this test's purpose is the stale finish itself.
            conn.execute('''DELETE FROM saas_operator_copilot_threads WHERE
              (tenant_id,requested_by,channel_id,conversation_id)=(%s,%s,%s,%s)''',scope)
            self.assertFalse(promote(conn.cursor(),scope)); conn.commit()
        policy=SimpleNamespace(can_use_chat_ai=lambda:True)
        with patch.object(legacy,'_connect',self.connect),patch.object(legacy,'ensure_schema'), \
             patch.object(legacy,'access',return_value=(tenant,1,policy)),patch.object(legacy,'assert_route'), \
             patch.object(legacy,'_assigned_context',return_value={}),patch.object(legacy,'_agent_binding',return_value=binding), \
             patch('tenant_episode_media.revalidate_sources'):
            legacy._finish(tenant,command,1,{'text':'answer'},snapshot,'question','answer')
        with self.connect() as conn:
            resolve_v1(conn.cursor(),scope,request)
            self.assertTrue(promote(conn.cursor(),scope)); conn.commit()
        with self.connect() as conn:
            turns=conn.execute('SELECT turns FROM saas_operator_copilot_threads WHERE tenant_id=%s',(tenant,)).fetchone()['turns']
            self.assertEqual(turns[0]['text'],'must remain cleared')

    def test_legacy_cleanup_can_erase_v2_generation_fence(self):
        """Passing reproduces why legacy tenant-wide cleanup blocks rollout."""
        from tenant_operator_copilot_threads import append
        scope=(str(uuid.uuid4()),1,'channel','dialog'); binding='synthetic-binding'
        with self.connect() as conn:
            request=str(uuid.uuid4()); admit(conn.cursor(),scope,request); resolve_v1(conn.cursor(),scope,request)
            self.assertTrue(promote(conn.cursor(),scope))
            append(conn.cursor(),*scope,0,binding,[{'role':'employee','text':'expired','agent_binding':binding}])
            conn.execute("UPDATE saas_operator_copilot_threads SET updated_at=now()-interval '61 days' WHERE tenant_id=%s",(scope[0],))
            conn.commit()
        with self.connect() as conn:
            # Exact tenant-wide legacy cleanup does not inspect v2 activity.
            conn.execute("DELETE FROM saas_operator_copilot_threads WHERE updated_at<now()-interval '60 days' AND tenant_id=%s::uuid",(scope[0],))
            conn.commit()
        with self.connect() as conn:
            # The zero revision is recreated and a request from before expiry
            # is incorrectly accepted, demonstrating generation resurrection.
            append(conn.cursor(),*scope,0,binding,[{'role':'copilot','text':'stale completion','agent_binding':binding}])
            conn.commit()
        with self.connect() as conn:
            result=conn.execute('SELECT turns FROM saas_operator_copilot_threads WHERE tenant_id=%s',(scope[0],)).fetchone()
            self.assertEqual(result['turns'][0]['text'],'stale completion')


if __name__ == '__main__': unittest.main()
