"""Voice lease integration against a local, disposable Unix-socket PostgreSQL.

Never use a network connection or the application's DATABASE_URL here.
"""
import asyncio
import os
from pathlib import Path
import re
import subprocess
import sys
import types
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from session import Session  # Adds the application module path.
from tenant_operator_voice_pilot import PilotAdmission
from test_pilot_admission import MockPilotProvider
from test_session import Context, Client
sys.modules.setdefault('tenant_platform',types.SimpleNamespace(_connect=lambda:None))
import tenant_operator_voice_lease as lease_module
from tenant_linear_policy import TenantLinearError


def local_test_dsn():
    value=os.environ.get('TEST_DATABASE_URL','')
    if not value:raise unittest.SkipTest('isolated local TEST_DATABASE_URL not configured')
    if not re.fullmatch(r'host=/tmp/bein-lease-pg\.[A-Za-z0-9]+ dbname=voice_lease_test user=postgres',value):
        raise RuntimeError('TEST_DATABASE_URL must identify the isolated local Unix socket')
    return value


class Authority:
    def __init__(self,tenant,actor=7,channel=None,conversation='synthetic'):
        self.session_scope=(tenant,actor,channel or str(uuid.uuid4()),conversation)
    def recheck(self,_cur):pass
    async def check(self,scope,stage):
        if scope!=self.session_scope:raise TenantLinearError('scope_invalid',403)


class RealPostgresLeaseTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn=local_test_dsn()
        cls.schema='voice_test_'+uuid.uuid4().hex
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            database,local=conn.execute('SELECT current_database(), inet_server_addr() IS NULL').fetchone()
            if database!='voice_lease_test' or not local:raise RuntimeError('unexpected database or nonlocal connection')
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(cls.schema)))
            conn.execute('CREATE TABLE saas_tenants (id uuid PRIMARY KEY)')
            conn.execute(Path(__file__).with_name('voice_session_lease.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def connect(self,**kwargs):
        return psycopg.connect(self.dsn,options='-csearch_path='+self.schema,**kwargs)

    def setUp(self):
        self.tenant=str(uuid.uuid4())
        with self.connect() as conn:conn.execute('INSERT INTO saas_tenants(id) VALUES(%s)',(self.tenant,))
        self.patch=patch.object(lease_module,'_connect',lambda:self.connect(row_factory=dict_row))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        with self.connect() as conn:conn.execute('DELETE FROM saas_tenants WHERE id=%s',(self.tenant,))

    def expire(self,actor):
        with self.connect() as conn:
            conn.execute("UPDATE saas_operator_voice_lease SET lease_until=now()-interval '1 second' WHERE tenant_id=%s AND actor_id=%s",(self.tenant,actor))

    def crash_after_claim(self,authority,active):
        source='''
import asyncio, os, sys, types, uuid
import psycopg
from psycopg.rows import dict_row
sys.modules['tenant_platform']=types.SimpleNamespace(_connect=lambda:None)
import tenant_operator_voice_lease as module
class Authority:
    session_scope=(sys.argv[1],7,sys.argv[2],'synthetic')
    def recheck(self,cur):pass
module._connect=lambda:psycopg.connect(os.environ['TEST_DATABASE_URL'],options='-csearch_path='+sys.argv[3],row_factory=dict_row)
async def run():
    authority=Authority();lease=module.VoiceSessionLease(authority,str(uuid.uuid4()))
    fence=await lease.claim(authority.session_scope)
    if fence!=1:raise RuntimeError('claim failed')
    if sys.argv[4]=='active':await lease.assert_current(authority.session_scope,fence)
asyncio.run(run())
os._exit(0)
'''
        result=subprocess.run([sys.executable,'-c',source,self.tenant,authority.session_scope[2],self.schema,
                               'active' if active else 'starting'],capture_output=True,text=True,timeout=10,
                              env={'TEST_DATABASE_URL':self.dsn,'PYTHONPATH':os.environ['PYTHONPATH']})
        self.assertEqual(result.returncode,0,result.stderr)

    async def test_parallel_claims_across_connections_and_dialogs(self):
        authorities=[Authority(self.tenant,conversation=f'dialog-{i}') for i in range(8)]
        leases=[lease_module.VoiceSessionLease(a,str(uuid.uuid4())) for a in authorities]
        results=await asyncio.gather(*(lease.claim(a.session_scope) for lease,a in zip(leases,authorities)))
        self.assertEqual(sum(value is not None for value in results),1)
        winner=next(i for i,value in enumerate(results) if value is not None)
        self.assertTrue(await leases[winner].release(authorities[winner].session_scope,results[winner]))

    async def test_competing_processes_have_a_single_owner(self):
        source='''
import asyncio, os, sys, types, uuid
import psycopg
from psycopg.rows import dict_row
sys.modules['tenant_platform']=types.SimpleNamespace(_connect=lambda:None)
import tenant_operator_voice_lease as module
class Authority:
    session_scope=(sys.argv[1],7,sys.argv[2],'synthetic')
    def recheck(self,cur):pass
module._connect=lambda:psycopg.connect(os.environ['TEST_DATABASE_URL'],options='-csearch_path='+sys.argv[3],row_factory=dict_row)
async def run():
    authority=Authority();lease=module.VoiceSessionLease(authority,str(uuid.uuid4()))
    result=await lease.claim(authority.session_scope)
    print('claimed' if result is not None else 'blocked',flush=True)
sys.stdin.readline()
asyncio.run(run())
'''
        authority=Authority(self.tenant)
        env={'TEST_DATABASE_URL':self.dsn,'PYTHONPATH':os.environ['PYTHONPATH']}
        processes=[subprocess.Popen([sys.executable,'-c',source,self.tenant,authority.session_scope[2],self.schema],
                                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
                   for _ in range(6)]
        try:
            for process in processes:process.stdin.write('start\n');process.stdin.flush()
            results=[]
            for process in processes:
                out,err=process.communicate(timeout=10)
                self.assertEqual(process.returncode,0,err);results.append(out.strip())
            self.assertEqual(results.count('claimed'),1)
            self.assertEqual(results.count('blocked'),5)
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.wait()

    async def test_restart_before_provider_open_reclaims_and_fences_old_owner(self):
        a=Authority(self.tenant);first=lease_module.VoiceSessionLease(a,str(uuid.uuid4()))
        second=lease_module.VoiceSessionLease(a,str(uuid.uuid4()))
        self.assertEqual(await first.claim(a.session_scope),1);self.expire(7)
        self.assertEqual(await second.claim(a.session_scope),2)
        with self.assertRaises(TenantLinearError):await first.assert_current(a.session_scope,1)
        self.assertFalse(await first.release(a.session_scope,1))
        await second.assert_current(a.session_scope,2)
        self.assertTrue(await second.release(a.session_scope,2))

    async def test_restart_with_uncertain_active_provider_remains_blocked(self):
        a=Authority(self.tenant);first=lease_module.VoiceSessionLease(a,str(uuid.uuid4()))
        second=lease_module.VoiceSessionLease(a,str(uuid.uuid4()))
        self.assertEqual(await first.claim(a.session_scope),1)
        await first.assert_current(a.session_scope,1);self.expire(7)
        self.assertIsNone(await second.claim(a.session_scope))
        self.assertTrue(await first.release(a.session_scope,1))
        self.assertEqual(await second.claim(a.session_scope),2)
        self.assertTrue(await second.release(a.session_scope,2))

    async def test_process_exit_reclaims_starting_but_not_uncertain_active(self):
        starting=Authority(self.tenant);self.crash_after_claim(starting,False);self.expire(7)
        replacement=lease_module.VoiceSessionLease(starting,str(uuid.uuid4()))
        self.assertEqual(await replacement.claim(starting.session_scope),2)
        self.assertTrue(await replacement.release(starting.session_scope,2))
        # Reset the already closed synthetic row for an independent crash case.
        with self.connect() as conn:conn.execute('DELETE FROM saas_operator_voice_lease WHERE tenant_id=%s AND actor_id=%s',(self.tenant,7))
        active=Authority(self.tenant);self.crash_after_claim(active,True);self.expire(7)
        later=lease_module.VoiceSessionLease(active,str(uuid.uuid4()))
        self.assertIsNone(await later.claim(active.session_scope))

    async def test_same_actor_different_tenants_are_isolated(self):
        other=str(uuid.uuid4())
        with self.connect() as conn:conn.execute('INSERT INTO saas_tenants(id) VALUES(%s)',(other,))
        try:
            first_a=Authority(self.tenant);second_a=Authority(other)
            first=lease_module.VoiceSessionLease(first_a,str(uuid.uuid4()))
            second=lease_module.VoiceSessionLease(second_a,str(uuid.uuid4()))
            self.assertEqual(await first.claim(first_a.session_scope),1)
            self.assertEqual(await second.claim(second_a.session_scope),1)
            self.assertTrue(await first.release(first_a.session_scope,1))
            self.assertTrue(await second.release(second_a.session_scope,1))
        finally:
            with self.connect() as conn:conn.execute('DELETE FROM saas_tenants WHERE id=%s',(other,))

    async def test_pilot_session_uses_postgres_lease_through_clean_shutdown(self):
        authority=Authority(self.tenant);sid=str(uuid.uuid4())
        lease=lease_module.VoiceSessionLease(authority,sid)
        policy=PilotAdmission(authority,lease,authority.session_scope,sid)
        provider=MockPilotProvider(policy)
        session=Session(authority.session_scope,authority=authority,ledger=None,lease=lease,context=Context(),
                        provider=provider,client=Client(),session_id=sid,pilot_policy=policy,max_seconds=5,idle_seconds=4)
        await session.start()
        other=lease_module.VoiceSessionLease(authority,str(uuid.uuid4()))
        self.assertIsNone(await other.claim(authority.session_scope))
        await session.receive_provider({'type':'input_audio_buffer.speech_started'})
        await session.receive_provider({'type':'input_audio_buffer.speech_stopped'})
        await session.receive_provider({'type':'input_audio_buffer.committed','item_id':'synthetic-input'})
        self.assertEqual(sum(event['type']=='response.create' for event in provider.events),1)
        self.assertTrue(await session.end('test'))
        self.assertTrue(provider.closed)
        self.assertEqual(await other.claim(authority.session_scope),2)
        self.assertTrue(await other.release(authority.session_scope,2))
