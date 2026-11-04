"""Unit contract for lease fencing; this is not a PostgreSQL integration test."""
import asyncio
import sys
import threading
import types
import unittest
from unittest.mock import patch

from session import Session as _Session  # Adds the repository's production module path.
sys.modules.setdefault('tenant_platform',types.SimpleNamespace(_connect=lambda:None))
import tenant_operator_voice_lease as lease_module
from tenant_linear_policy import TenantLinearError


class FakeAuthority:
    session_scope=('tenant',17,'channel','conversation')
    def recheck(self,_cur): pass


class Store:
    def __init__(self): self.rows={};self.lock=threading.Lock()
    def connect(self): return Connection(self)


class Connection:
    def __init__(self,store):self.store=store;self.cursor_value=Cursor(store)
    def __enter__(self):self.store.lock.acquire();return self
    def __exit__(self,*_):self.store.lock.release()
    def cursor(self):return self.cursor_value
    def commit(self):pass


class Cursor:
    def __init__(self,store):self.store=store;self.row=None
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def execute(self,sql,args=()):
        if 'pg_advisory_xact_lock' in sql:return
        if sql.lstrip().startswith('SELECT session_id::text'):
            self.row=dict(self.store.rows[tuple(args)]) if tuple(args) in self.store.rows else None
            if self.row:self.row['live']=self.row['live']
        elif sql.lstrip().startswith('INSERT INTO saas_operator_voice_lease'):
            tenant,actor,channel,conversation,session,owner,fence,ttl=args
            key=(tenant,actor)
            self.store.rows[key]={'session_id':session,'actor_id':actor,'channel':channel,'conversation':conversation,'owner_id':owner,'fence':fence,
                                  'state':'starting','live':True}
            self.row={'fence':fence}
        elif "SET session_id=%s::uuid" in sql:
            session,channel,conversation,owner,fence,ttl,tenant,actor,expected=args
            key=(tenant,actor);old=self.store.rows[key]
            if old['fence']==expected:
                self.store.rows[key]={'session_id':session,'actor_id':actor,'channel':channel,'conversation':conversation,'owner_id':owner,'fence':fence,
                                      'state':'starting','live':True}
                self.row={'fence':fence}
            else:self.row=None
        elif "SET state='active'" in sql:
            ttl,tenant,actor,channel,conversation,session,owner,fence=args
            key=(tenant,actor);old=self.store.rows.get(key)
            if old and old['session_id']==session and old['channel']==channel and old['conversation']==conversation and old['owner_id']==owner and old['fence']==fence and old['state'] in ('starting','active') and old['live']:
                old['state']='active';self.row={'fence':fence}
            else:self.row=None
        elif "SET state='closed'" in sql:
            tenant,actor,channel,conversation,session,owner,fence=args
            key=(tenant,actor);old=self.store.rows.get(key)
            if old and old['session_id']==session and old['channel']==channel and old['conversation']==conversation and old['owner_id']==owner and old['fence']==fence:
                old['state']='closed';old['live']=False;self.row={'fence':fence}
            else:self.row=None
        else:raise AssertionError('unexpected SQL in lease unit test')
    def fetchone(self):return self.row


class VoiceLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_session_is_not_replaced_until_cleanup_confirms_close(self):
        store=Store();authority=FakeAuthority()
        first=lease_module.VoiceSessionLease(authority,'00000000-0000-0000-0000-000000000001')
        second=lease_module.VoiceSessionLease(authority,'00000000-0000-0000-0000-000000000002')
        with patch.object(lease_module,'_connect',store.connect):
            self.assertEqual(await first.claim(authority.session_scope),1)
            self.assertIsNone(await second.claim(authority.session_scope))
            await first.assert_current(authority.session_scope,1)
            store.rows[('tenant',17)]['live']=False
            self.assertIsNone(await second.claim(authority.session_scope))
            with self.assertRaises(TenantLinearError):
                await first.assert_current(authority.session_scope,1)
            self.assertTrue(await first.release(authority.session_scope,1))
            self.assertEqual(await second.claim(authority.session_scope),2)
            await second.assert_current(authority.session_scope,2)
            self.assertFalse(await first.release(authority.session_scope,1))
            self.assertTrue(await second.release(authority.session_scope,2))

    async def test_expired_starting_session_stays_blocked(self):
        store=Store();authority=FakeAuthority()
        first=lease_module.VoiceSessionLease(authority,'00000000-0000-0000-0000-000000000011')
        second=lease_module.VoiceSessionLease(authority,'00000000-0000-0000-0000-000000000012')
        with patch.object(lease_module,'_connect',store.connect):
            self.assertEqual(await first.claim(authority.session_scope),1)
            store.rows[('tenant',17)]['live']=False
            self.assertIsNone(await second.claim(authority.session_scope))
            self.assertTrue(await first.release(authority.session_scope,1))
            self.assertEqual(await second.claim(authority.session_scope),2)

    async def test_concurrent_claim_has_exactly_one_winner(self):
        store=Store();first_authority=FakeAuthority();second_authority=FakeAuthority()
        second_authority.session_scope=('tenant',17,'other-channel','other-dialog')
        authorities=[first_authority,second_authority]
        leases=[lease_module.VoiceSessionLease(authority,f'00000000-0000-0000-0000-{n:012d}')
                for n,authority in zip((3,4),authorities)]
        with patch.object(lease_module,'_connect',store.connect):
            results=await asyncio.gather(*(item.claim(authority.session_scope) for item,authority in zip(leases,authorities)))
        self.assertEqual(sum(value is not None for value in results),1)

    async def test_same_user_different_dialog_is_exclusive_but_other_user_is_independent(self):
        store=Store();first_authority=FakeAuthority()
        second_authority=FakeAuthority();second_authority.session_scope=('tenant',17,'other-channel','other-dialog')
        third_authority=FakeAuthority();third_authority.session_scope=('tenant',18,'channel','conversation')
        first=lease_module.VoiceSessionLease(first_authority,'00000000-0000-0000-0000-000000000005')
        second=lease_module.VoiceSessionLease(second_authority,'00000000-0000-0000-0000-000000000006')
        third=lease_module.VoiceSessionLease(third_authority,'00000000-0000-0000-0000-000000000007')
        with patch.object(lease_module,'_connect',store.connect):
            self.assertEqual(await first.claim(first_authority.session_scope),1)
            self.assertIsNone(await second.claim(second_authority.session_scope))
            self.assertEqual(await third.claim(third_authority.session_scope),1)
            self.assertTrue(await first.release(first_authority.session_scope,1))
            self.assertEqual(await second.claim(second_authority.session_scope),2)


if __name__=='__main__':unittest.main()
