"""Bounded production audio checks, using synthetic local database results."""
import asyncio
import base64
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tenant_linear_policy import TenantLinearError
from tenant_operator_voice_lease import VoiceSessionLease
from tenant_operator_voice_orchestration import ScopedAuthority
from tenant_operator_voice_session import Refused, Session
import tenant_operator_voice_lease as lease_module
import tenant_operator_voice_orchestration as orchestration


class FakeDatabase:
    def __init__(self):
        self.lease=None;self.connects=0;self.authorizations=0;self.commits=0
        self.fence=9;self.owner=None;self.active=True;self.expired=False;self.route_valid=True

    def connect(self):
        self.connects+=1
        database=self
        class Cursor:
            def __init__(self):self.row=None
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def execute(self,query,params):
                if query.startswith('SELECT pg_advisory_xact_lock'):return
                if not query.lstrip().startswith('UPDATE saas_operator_voice_lease'):
                    raise AssertionError('unexpected query')
                _,tenant,actor,channel,conversation,session,owner,fence=params
                lease=database.lease
                expected=(lease.scope[0],lease.scope[1],lease.channel,lease.conversation,
                          lease.session_id,database.owner,database.fence)
                self.row={'fence':fence} if (tenant,actor,channel,conversation,session,owner,fence)==expected and database.active and not database.expired else None
            def fetchone(self):return self.row
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def cursor(self):return Cursor()
            def commit(self):database.commits+=1
        return Connection()


class InputAuthorityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tenant=str(uuid.uuid4());channel=str(uuid.uuid4());actor=7
        self.scope=SimpleNamespace(tenant=tenant,actor=actor,channel_id=channel,conversation_id='synthetic',
                                   lead_id=1,agent_binding='synthetic',config_version='v1')
        self.identity={'value':{'tenant_id':tenant,'telegram_id':actor}}
        self.route={'provider':'connect','lead_id':1,'channel_id':channel,'conversation_id':'synthetic'}
        self.authority=ScopedAuthority({},self.route,self.scope,lambda:self.identity['value'])
        self.lease=VoiceSessionLease(self.authority,str(uuid.uuid4()))
        self.lease.fence=9;self.database=FakeDatabase();self.database.lease=self.lease;self.database.owner=self.lease.owner_id
        self.session=object.__new__(Session)
        s=self.session;s.closed=False;s.scope=self.authority.session_scope;s.authority=self.authority
        s.lease=self.lease;s.fence=9;s.session_id=self.lease.session_id;s.duplex=True;s.muted=False
        s.input_bytes=0;s.max_input_bytes=2_000_000;s.started=0;s.max_seconds=600
        s.io_lock=asyncio.Lock();s.provider=SimpleNamespace(send=AsyncMock())
        self.stack=[]
        self._patch(patch.object(lease_module,'_connect',side_effect=self.database.connect))
        self._patch(patch.object(orchestration,'require_pilot'))
        self._patch(patch.object(orchestration.voice,'check',side_effect=AssertionError('redundant separate check')))
        self._patch(patch.object(orchestration.voice,'authorize',side_effect=self.authorize))

    def _patch(self,p):
        p.start();self.addCleanup(p.stop)

    def authorize(self,cur,profile,route):
        self.database.authorizations+=1
        self.assertEqual(profile,{'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor})
        self.assertEqual(route,self.route)
        if not self.database.route_valid:raise TenantLinearError('synthetic denial',403)
        return self.scope

    async def test_each_audio_batch_rechecks_identity_route_and_exact_lease_once(self):
        await self.session._receive_client({'type':'audio','pcm':bytes(8192)})
        await self.session._receive_client({'type':'audio','pcm':bytes(8192)})
        self.assertEqual(self.database.connects,2)
        self.assertEqual(self.database.authorizations,2)
        self.assertEqual(self.database.commits,2)
        self.assertEqual(self.session.provider.send.await_count,2)
        self.assertEqual(base64.b64decode(self.session.provider.send.await_args.args[0]['audio']),bytes(8192))

    async def test_revocations_and_stale_lease_never_forward_the_next_frame(self):
        cases=('identity','route','fence','owner','expired','state')
        for case in cases:
            with self.subTest(case=case):
                self.identity['value']={'tenant_id':self.scope.tenant,'telegram_id':self.scope.actor}
                self.database.route_valid=True;self.database.fence=9;self.database.owner=self.lease.owner_id
                self.database.expired=False;self.database.active=True
                self.session.provider.send.reset_mock()
                if case=='identity':self.identity['value']=None
                if case=='route':self.database.route_valid=False
                if case=='fence':self.database.fence=10
                if case=='owner':self.database.owner=str(uuid.uuid4())
                if case=='expired':self.database.expired=True
                if case=='state':self.database.active=False
                with self.assertRaises(Refused):await self.session._receive_client({'type':'audio','pcm':bytes(2)})
                self.session.provider.send.assert_not_awaited()

    async def test_exact_authority_session_and_scope_binding_is_required(self):
        wrong=ScopedAuthority({},self.route,self.scope,lambda:self.identity['value'])
        self.session.authority=wrong
        with self.assertRaises(Refused):await self.session._receive_client({'type':'audio','pcm':bytes(2)})
        self.assertEqual(self.database.connects,0)
        self.session.provider.send.assert_not_awaited()

    async def test_other_dependencies_keep_separate_live_authority_and_lease_checks(self):
        other_authority=SimpleNamespace(check=AsyncMock())
        other_lease=SimpleNamespace(assert_current=AsyncMock())
        self.session.authority=other_authority;self.session.lease=other_lease
        await self.session._receive_client({'type':'audio','pcm':bytes(2)})
        other_authority.check.assert_awaited_once_with(self.session.scope,'input')
        other_lease.assert_current.assert_awaited_once_with(self.session.scope,self.session.fence)
        self.session.provider.send.assert_awaited_once()

    async def test_revocation_while_waiting_for_io_lock_blocks_provider_send(self):
        await self.session.io_lock.acquire()
        task=asyncio.create_task(self.session._receive_client({'type':'audio','pcm':bytes(2)}))
        await asyncio.sleep(0)
        self.assertEqual(self.database.connects,0,'authorization follows io_lock acquisition')
        self.database.fence=10
        self.session.io_lock.release()
        with self.assertRaises(Refused):await task
        self.session.provider.send.assert_not_awaited()

    async def test_shutdown_while_waiting_for_io_lock_never_sends_or_renews(self):
        await self.session.io_lock.acquire()
        task=asyncio.create_task(self.session._receive_client({'type':'audio','pcm':bytes(2)}))
        await asyncio.sleep(0);self.session.closed=True;self.session.io_lock.release()
        with self.assertRaisesRegex(Refused,'session_closed'):await task
        self.assertEqual(self.database.connects,0)
        self.session.provider.send.assert_not_awaited()

    async def test_shutdown_during_authorization_never_sends(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(*_):entered.set();await release.wait()
        self.lease.assert_current=delayed
        task=asyncio.create_task(self.session._receive_client({'type':'audio','pcm':bytes(2)}))
        await entered.wait();self.session.closed=True;release.set()
        with self.assertRaisesRegex(Refused,'session_closed'):await task
        self.session.provider.send.assert_not_awaited()

    async def test_muted_invalid_and_excess_pcm_still_fail_or_stop_before_provider(self):
        self.session.muted=True
        await self.session._receive_client({'type':'audio','pcm':bytes(2)})
        self.assertEqual(self.database.authorizations,1)
        self.session.muted=False
        for pcm in (b'',b'\x01',bytes(9602)):
            with self.subTest(length=len(pcm)):
                with self.assertRaisesRegex(Refused,'invalid_pcm_frame'):
                    await self.session._receive_client({'type':'audio','pcm':pcm})
        self.session.input_bytes=self.session.max_input_bytes
        with self.assertRaisesRegex(Refused,'session_input_limit'):
            await self.session._receive_client({'type':'audio','pcm':bytes(2)})
        self.session.provider.send.assert_not_awaited()

    async def test_duration_and_failed_send_keep_original_byte_accounting(self):
        self.session.started=time.monotonic()-self.session.max_seconds
        with self.assertRaisesRegex(Refused,'session_duration_limit'):
            await self.session._receive_client({'type':'audio','pcm':bytes(2)})
        self.assertEqual(self.session.input_bytes,2)
        self.session.started=0;self.session.provider.send.side_effect=RuntimeError('synthetic send failure')
        with self.assertRaises(RuntimeError):await self.session._receive_client({'type':'audio','pcm':bytes(2)})
        self.assertEqual(self.session.input_bytes,4)

    async def test_fixed_latency_model_can_keep_up_at_observed_batch_size(self):
        # A deterministic model, not a measurement of Railway latency:
        # 150ms per database-backed check, 76 batches of 8192 PCM bytes.
        elapsed=[0.0]
        async def fixed_latency(fn,*args,**kwargs):
            elapsed[0]+=.15
            return fn(*args,**kwargs)
        with patch.object(orchestration.voice,'check',return_value=self.scope),patch.object(asyncio,'to_thread',side_effect=fixed_latency):
            for _ in range(76):
                await self.authority.check(self.session.scope,'input')
                await self.lease.assert_current(self.session.scope,self.session.fence)
            old=elapsed[0];elapsed[0]=0
            for _ in range(76):await self.session._receive_client({'type':'audio','pcm':bytes(8192)})
            new=elapsed[0]
        audio_seconds=76*8192/(24_000*2)
        self.assertAlmostEqual(old,22.8)
        self.assertAlmostEqual(new,11.4)
        self.assertGreater(old,audio_seconds);self.assertLess(new,audio_seconds)


if __name__=='__main__':unittest.main()
