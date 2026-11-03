"""Rolling writer/cutover tests against disposable localhost PostgreSQL only.

The legacy SQL below is copied from the old deployed Copilot writer. Tests
assert both counterexamples and new-writer properties; no provider is called.
"""
import ast
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from psycopg.errors import LockNotAvailable,RaiseException

import tenant_episode_media
import tenant_media_evidence
import tenant_operator_copilot as copilot
import tenant_operator_copilot_threads as threads
from tenant_linear_policy import TenantLinearError
from test_integrated_synthetic_pg import IntegratedSynthetic


class RollingHistory(IntegratedSynthetic):
    def setUp(self):
        super().setUp()
        self.conn.execute('''CREATE TABLE saas_chat_ai_runs(
          tenant_id uuid,request_id uuid,requested_by bigint,channel_id text,conversation_id text,
          config_version text,status text,result jsonb,updated_at timestamptz DEFAULT now(),
          PRIMARY KEY(tenant_id,request_id))''')
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback();self.conn.execute('DROP TABLE IF EXISTS saas_chat_ai_runs');self.conn.commit()
        super().tearDown()

    def entry(self,text,role='employee'):
        return {'role':role,'text':text,'agent_binding':'synthetic-binding'}

    def voice(self,text='synthetic voice'):
        return {**self.entry(text),'voice_session_id':str(uuid.uuid4()),'turn_sequence':0,
                'transcript_status':'partial','transcript_quality':'automatic_unverified'}

    def command(self):
        return {'mode':'chat','prompt':'synthetic prompt','lead_id':41,'channel_id':self.channel,
                'conversation_id':self.conversation,'request_id':str(uuid.uuid4()),
                '_agent_binding':'synthetic-binding'}

    def snapshot(self,command):
        with self.scoped() as conn,conn.cursor() as cur,\
             patch.object(tenant_episode_media,'extend_episode',side_effect=lambda _cur,_tenant,_leads,_channel,rows,*_args,**_kw:rows),\
             patch.object(tenant_media_evidence,'annotate_rows'):
            _rows,turns=copilot._read_history(cur,self.tenant,7,command)
        # Keep source validation outside these history-specific fixtures.
        command.pop('_latest_source_id',None)
        return turns

    def make_run(self,command):
        with self.scoped() as conn:
            version=conn.execute("SELECT updated_at FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='chat_ai'",(self.tenant,)).fetchone()['updated_at']
            conn.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,channel_id,conversation_id,config_version,status) VALUES(%s,%s,7,%s,%s,%s,'sending')",
                         (self.tenant,command['request_id'],self.channel,self.conversation,str(version)))

    def finish(self,command,turns,answer='synthetic answer'):
        policy=type('Policy',(),{'can_use_chat_ai':lambda _self:True})()
        result={'text':answer}
        with patch.object(copilot,'_connect',self.scoped),patch.object(copilot,'ensure_schema'),\
             patch.object(copilot,'access',return_value=(self.tenant,7,policy)),\
             patch.object(copilot,'assert_route',return_value={'lead_id':41}),\
             patch.object(copilot,'_assigned_context',return_value={}),\
             patch.object(copilot,'_agent_binding',return_value='synthetic-binding'),\
             patch.object(tenant_episode_media,'revalidate_sources'):
            copilot._finish(self.tenant,command,7,result,turns,command['prompt'],answer)
        return result

    def read_thread(self):
        with self.scoped() as conn:
            return conn.execute('SELECT turns,clear_revision FROM saas_operator_copilot_threads WHERE tenant_id=%s AND requested_by=7 AND channel_id=%s AND conversation_id=%s',
                                (self.tenant,self.channel,self.conversation)).fetchone()

    def insert_voice(self):
        with self.scoped() as conn,conn.cursor() as cur:
            threads.append(cur,self.tenant,7,self.channel,self.conversation,0,'synthetic-binding',[self.voice()])

    def legacy_finish(self,old_turns):
        snapshot=(old_turns+[self.entry('legacy prompt'),self.entry('legacy answer','copilot')])[-20:]
        with self.scoped() as conn:
            conn.execute('''INSERT INTO saas_operator_copilot_threads(tenant_id,requested_by,channel_id,conversation_id,turns)
              VALUES(%s::uuid,%s,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,requested_by,channel_id,conversation_id)
              DO UPDATE SET turns=EXCLUDED.turns,updated_at=now()''',
              (self.tenant,7,self.channel,self.conversation,json.dumps(snapshot)))

    def actual_legacy_finish(self,command,turns):
        # Execute the exact old deployed Python _finish body from the base
        # commit, with only auth/source/provider dependencies replaced. No
        # running app, production database, credentials or provider are used.
        source=subprocess.check_output(['git','show',
            '156ce9ce15d8e4e3cddcbca2006614a827f028e3:beintaskbot-main/tenant_operator_copilot.py'],
            cwd=Path(__file__).resolve().parents[2],text=True)
        node=next(item for item in ast.parse(source).body if isinstance(item,ast.FunctionDef) and item.name=='_finish')
        module=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
        env=dict(copilot.__dict__)
        policy=type('Policy',(),{'can_use_chat_ai':lambda _self:True})()
        env.update(_connect=self.scoped,ensure_schema=lambda _conn:None,
                   access=lambda *_args:(self.tenant,7,policy),assert_route=lambda *_args:{'lead_id':41},
                   _assigned_context=lambda *_args:{},_agent_binding=lambda *_args:'synthetic-binding')
        exec(compile(module,'synthetic-old-copilot','exec'),env)
        with patch.object(tenant_episode_media,'revalidate_sources'):
            env['_finish'](self.tenant,command,7,{'text':'legacy answer'},turns,command['prompt'],'legacy answer')

    def test_legacy_stale_finish_loses_voice_but_new_finish_preserves_it(self):
        command=self.command();old=self.snapshot(command);self.make_run(command);self.insert_voice()
        self.actual_legacy_finish(command,old)
        self.assertNotIn('voice_session_id',json.dumps(self.read_thread()['turns']))  # counterexample
        with self.scoped() as conn:
            conn.execute('DELETE FROM saas_operator_copilot_threads WHERE tenant_id=%s',(self.tenant,))
            conn.execute("UPDATE saas_chat_ai_runs SET status='sending',result=NULL WHERE request_id=%s",(command['request_id'],))
        self.insert_voice();self.finish(command,old)
        turns=self.read_thread()['turns']
        self.assertEqual([x['text'] for x in turns],['synthetic voice','synthetic prompt','synthetic answer'])
        with self.assertRaises(TenantLinearError):self.finish(command,old)  # already-completed receipt cannot append twice
        self.assertEqual(self.read_thread()['turns'],turns)

    def test_clear_absent_during_text_request_preserves_answer_but_not_history(self):
        command=self.command();old=self.snapshot(command);self.make_run(command)
        with self.scoped() as conn,conn.cursor() as cur:threads.clear(cur,self.tenant,7,self.channel,self.conversation)
        result=self.finish(command,old)
        self.assertEqual(result['history_status'],'cleared')
        self.assertEqual(self.read_thread(),{'turns':[],'clear_revision':1})
        with self.scoped() as conn:
            row=conn.execute('SELECT status,result FROM saas_chat_ai_runs WHERE request_id=%s',(command['request_id'],)).fetchone()
        self.assertEqual(row['status'],'completed');self.assertEqual(row['result']['text'],'synthetic answer')
        self.assertEqual(row['result']['history_status'],'cleared')

    def test_legacy_clear_absent_cannot_fence_legacy_inflight_finish(self):
        command=self.command();old=self.snapshot(command)
        with self.scoped() as conn:
            deleted=conn.execute('DELETE FROM saas_operator_copilot_threads WHERE tenant_id=%s AND requested_by=7 AND channel_id=%s AND conversation_id=%s',
                                 (self.tenant,self.channel,self.conversation)).rowcount
        self.assertEqual(deleted,0);self.legacy_finish(old)
        self.assertEqual([x['text'] for x in self.read_thread()['turns']],['legacy prompt','legacy answer'])

    def test_legacy_clear_absent_cannot_fence_new_inflight_finish(self):
        command=self.command();old=self.snapshot(command);self.make_run(command)
        with self.scoped() as conn:
            self.assertEqual(conn.execute('DELETE FROM saas_operator_copilot_threads WHERE tenant_id=%s AND requested_by=7 AND channel_id=%s AND conversation_id=%s',
                                          (self.tenant,self.channel,self.conversation)).rowcount,0)
        result=self.finish(command,old)
        self.assertNotIn('history_status',result)  # unavoidable mixed-version counterexample
        self.assertEqual([x['text'] for x in self.read_thread()['turns']],['synthetic prompt','synthetic answer'])

    def test_legacy_writer_ignores_receipt_no_longer_sending(self):
        command=self.command();old=self.snapshot(command);self.make_run(command);self.insert_voice()
        # The old _finish computes status='completed' from config before the
        # conditional UPDATE and does not check whether it updated any row.
        with self.scoped() as conn:
            conn.execute("UPDATE saas_chat_ai_runs SET status='unknown' WHERE request_id=%s",(command['request_id'],))
            row=conn.execute("UPDATE saas_chat_ai_runs SET status='completed' WHERE request_id=%s AND status='sending' RETURNING request_id",
                             (command['request_id'],)).fetchone()
        self.assertIsNone(row);self.actual_legacy_finish(command,old)
        self.assertNotIn('voice_session_id',json.dumps(self.read_thread()['turns']))
        with self.scoped() as conn:
            self.assertEqual(conn.execute('SELECT status FROM saas_chat_ai_runs WHERE request_id=%s',(command['request_id'],)).fetchone()['status'],'unknown')

    def test_new_writer_rejects_non_sending_receipt_without_history_change(self):
        command=self.command();old=self.snapshot(command);self.make_run(command);self.insert_voice()
        with self.scoped() as conn:conn.execute("UPDATE saas_chat_ai_runs SET status='unknown' WHERE request_id=%s",(command['request_id'],))
        with self.assertRaises(TenantLinearError) as caught:self.finish(command,old)
        self.assertTrue(caught.exception.keep_request)
        self.assertIn('voice_session_id',self.read_thread()['turns'][0])

    def test_clear_commits_between_receipt_update_and_history_append(self):
        command=self.command();old=self.snapshot(command);self.make_run(command)
        entered=threading.Event();release=threading.Event();original=threads.append
        def blocked(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise RuntimeError('synthetic barrier timeout')
            return original(*args,**kwargs)
        try:
            with patch.object(threads,'append',blocked),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.finish,command,old)
                self.assertTrue(entered.wait(2))
                with self.scoped() as conn,conn.cursor() as cur:threads.clear(cur,self.tenant,7,self.channel,self.conversation)
                release.set();future.result(timeout=3)
            self.assertEqual(self.read_thread(),{'turns':[],'clear_revision':1})
            with self.scoped() as conn:
                self.assertEqual(conn.execute('SELECT status FROM saas_chat_ai_runs WHERE request_id=%s',(command['request_id'],)).fetchone()['status'],'completed')
        finally:release.set()

    def test_two_new_inflight_finishes_keep_both_turn_pairs(self):
        commands=[self.command(),self.command()]
        old=[self.snapshot(command) for command in commands]
        for command in commands:self.make_run(command)
        barrier=threading.Barrier(2)
        def finish(index):barrier.wait();self.finish(commands[index],old[index],f'answer-{index}')
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(finish,(0,1)))
        turns=self.read_thread()['turns']
        self.assertEqual(len(turns),4)
        self.assertCountEqual([t['text'] for t in turns if t['role']=='copilot'],['answer-0','answer-1'])

    def test_clear_waits_for_inflight_append_then_removes_it(self):
        writer=self.scoped();channel=queue.Queue()
        with writer.cursor() as cur:
            threads.append(cur,self.tenant,7,self.channel,self.conversation,0,'synthetic-binding',[self.voice()])
        def clear():
            with self.scoped() as conn,conn.cursor() as cur:
                channel.put(conn.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'])
                threads.clear(cur,self.tenant,7,self.channel,self.conversation)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(clear);pid=channel.get(timeout=2)
                deadline=time.monotonic()+2;waiting=False
                while time.monotonic()<deadline:
                    with self.scoped() as conn:
                        state=conn.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s',(pid,)).fetchone()
                    if state and state['wait_event_type']=='Lock':waiting=True;break
                    time.sleep(.01)
                self.assertTrue(waiting,'clear must block on the in-flight append')
                writer.commit();future.result(timeout=3)
            self.assertEqual(self.read_thread(),{'turns':[],'clear_revision':1})
        finally:writer.close()

    def test_writer_crash_rolls_back_receipt_and_can_retry_once(self):
        command=self.command();old=self.snapshot(command);self.make_run(command)
        with patch.object(threads,'append',side_effect=RuntimeError('synthetic crash')):
            with self.assertRaises(RuntimeError):self.finish(command,old)
        self.assertIsNone(self.read_thread())
        with self.scoped() as conn:
            self.assertEqual(conn.execute('SELECT status FROM saas_chat_ai_runs WHERE request_id=%s',(command['request_id'],)).fetchone()['status'],'sending')
        self.finish(command,old)
        with self.assertRaises(TenantLinearError):self.finish(command,old)
        self.assertEqual(len(self.read_thread()['turns']),2)

    def test_postgres_backend_death_rolls_back_receipt_and_thread(self):
        command=self.command();old=self.snapshot(command);self.make_run(command)
        victim=self.scoped()
        try:
            pid=victim.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
            victim.execute("UPDATE saas_chat_ai_runs SET status='completed' WHERE request_id=%s",(command['request_id'],))
            with victim.cursor() as cur:
                threads.append(cur,self.tenant,7,self.channel,self.conversation,0,'synthetic-binding',[self.entry('uncommitted')])
            with self.scoped() as control:
                self.assertTrue(control.execute('SELECT pg_terminate_backend(%s) AS stopped',(pid,)).fetchone()['stopped'])
        finally:victim.close()
        self.assertIsNone(self.read_thread())
        with self.scoped() as conn:
            self.assertEqual(conn.execute('SELECT status FROM saas_chat_ai_runs WHERE request_id=%s',(command['request_id'],)).fetchone()['status'],'sending')
        self.finish(command,old)
        self.assertEqual(len(self.read_thread()['turns']),2)

    def test_expired_cleanup_keeps_tombstone_for_delayed_voice(self):
        self.insert_voice()
        with self.scoped() as conn,conn.cursor() as cur:
            cur.execute("UPDATE saas_operator_copilot_threads SET updated_at=now()-interval '61 days' WHERE tenant_id=%s",(self.tenant,))
            threads.cleanup_expired(cur,self.tenant)
        self.assertEqual(self.read_thread(),{'turns':[],'clear_revision':1})
        with self.scoped() as conn,conn.cursor() as cur:
            with self.assertRaises(threads.ThreadCleared):
                threads.append(cur,self.tenant,7,self.channel,self.conversation,0,'synthetic-binding',[self.voice()])

    def test_readonly_snapshot_filters_expired_without_mutating_thread(self):
        self.insert_voice()
        with self.scoped() as conn:
            conn.execute("UPDATE saas_operator_copilot_threads SET updated_at=now()-interval '61 days' WHERE tenant_id=%s",(self.tenant,))
        command=self.command();self.assertEqual(self.snapshot(command),[])
        row=self.read_thread();self.assertEqual(row['clear_revision'],0)
        self.assertEqual(row['turns'][0]['text'],'synthetic voice')

    def test_cleanup_before_snapshot_does_not_clear_its_own_answer(self):
        self.insert_voice()
        with self.scoped() as conn,conn.cursor() as cur:
            cur.execute("UPDATE saas_operator_copilot_threads SET updated_at=now()-interval '61 days' WHERE tenant_id=%s",(self.tenant,))
            threads.cleanup_expired(cur,self.tenant)
        command=self.command();old=self.snapshot(command)
        self.assertEqual(old,[]);self.assertEqual(command['_thread_clear_revision'],1)
        self.make_run(command);result=self.finish(command,old)
        self.assertNotIn('history_status',result)
        self.assertEqual([row['text'] for row in self.read_thread()['turns']],['synthetic prompt','synthetic answer'])

    def test_retention_after_snapshot_reports_cleared_without_resurrection(self):
        self.insert_voice();command=self.command();old=self.snapshot(command);self.make_run(command)
        with self.scoped() as conn,conn.cursor() as cur:
            cur.execute("UPDATE saas_operator_copilot_threads SET updated_at=now()-interval '61 days' WHERE tenant_id=%s",(self.tenant,))
            threads.cleanup_expired(cur,self.tenant)
        result=self.finish(command,old)
        self.assertEqual(result['history_status'],'cleared')
        self.assertEqual(self.read_thread(),{'turns':[],'clear_revision':1})

    def test_expand_ddl_fails_fast_when_old_transaction_holds_lock(self):
        # Disposable schema only. Demonstrates no guaranteed zero-latency DDL.
        with self.scoped() as conn:conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')
        lock=self.scoped();lock.execute('SELECT * FROM saas_operator_copilot_threads')
        try:
            start=time.monotonic()
            with self.assertRaises(LockNotAvailable):
                with self.scoped() as conn:
                    conn.execute("SET LOCAL lock_timeout='250ms'")
                    conn.execute('ALTER TABLE saas_operator_copilot_threads ADD COLUMN clear_revision bigint NOT NULL DEFAULT 0')
            self.assertLess(time.monotonic()-start,2)
        finally:
            lock.rollback();lock.close()
            with self.scoped() as conn:conn.execute('ALTER TABLE saas_operator_copilot_threads ADD COLUMN clear_revision bigint NOT NULL DEFAULT 0')

    def test_additive_expand_keeps_old_writer_and_can_rollback_without_voice(self):
        with self.scoped() as conn:conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')
        self.legacy_finish([])
        sql=Path(__file__).with_name('rolling_history_expand.sql').read_text()
        with self.scoped() as conn:
            conn.commit();conn.execute(sql)
            conn.execute(sql)  # expansion can be repeated on the same schema
        row=self.read_thread()
        self.assertEqual(row['clear_revision'],0)
        self.assertEqual([x['text'] for x in row['turns']],['legacy prompt','legacy answer'])
        self.legacy_finish([])  # old INSERT/ON CONFLICT still works after expansion
        with self.scoped() as conn:conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')
        self.legacy_finish([])  # rollback smoke test; no voice data was written
        with self.scoped() as conn:
            self.assertEqual(len(conn.execute('SELECT turns FROM saas_operator_copilot_threads').fetchone()['turns']),2)

    def test_expand_rejects_incompatible_preexisting_column(self):
        with self.scoped() as conn:
            conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')
            conn.execute("ALTER TABLE saas_operator_copilot_threads ADD COLUMN clear_revision text NOT NULL DEFAULT '0'")
        sql=Path(__file__).with_name('rolling_history_expand.sql').read_text()
        with self.assertRaises(RaiseException):
            with self.scoped() as conn:
                conn.commit();conn.execute(sql)
