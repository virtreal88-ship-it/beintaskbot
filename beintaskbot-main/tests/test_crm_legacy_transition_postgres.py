"""Real PostgreSQL sessions for legacy/scoped admission races. No provider access."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock
import psycopg
from psycopg.rows import dict_row
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A
from test_tenant_crm_relation_delivery_postgres import graph
from test_crm_scoped_scalar_postgres import scalar
from test_crm_legacy_transition import legacy_event
import tenant_crm_native_contacts as contacts
import tenant_crm_sync_outbox as legacy


@pytest.mark.parametrize('timeout',[False,True])
def test_legacy_inflight_fences_first_scoped_mutation_and_duplicate(scalar,monkeypatch,timeout):
    row=legacy_event(scalar)
    factory=lambda:psycopg.connect(scalar['db'].dsn,row_factory=dict_row)
    monkeypatch.setattr(legacy,'_connect',factory)
    monkeypatch.setattr(contacts,'_connect',factory)
    monkeypatch.setattr(legacy,'_linked',lambda *_:{'external_id':'1'})
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    with factory() as conn:
        with conn.cursor() as cur:
            legacy.record_link(cur,tenant_id=A,provider='kommo',entity_type='contact',
                internal_id=row['internal_id'],external_id='1',sync_state='synced')
    entered,release,started,finished=[threading.Event() for _ in range(4)]
    calls=[]
    async def request(*args,**kwargs):
        calls.append(args);entered.set();assert release.wait(10)
        if timeout:raise TimeoutError('may have applied')
        return {}
    def send():
        try:return asyncio.run(legacy._send(row,None,request,Mock()))
        except TimeoutError:return 'timeout'
    def edit():
        started.set()
        result=contacts.update_contact(A,scalar['contacts'][0].internal_id,'Scoped name','',actor_id=81)
        finished.set();return result
    with ThreadPoolExecutor(3) as pool:
        sending=pool.submit(send)
        try:
            assert entered.wait(10)
            editing=pool.submit(edit)
            assert started.wait(3) and not finished.wait(.2)
            # A duplicate worker/expired lease never passes the active session fence.
            with factory() as conn:
                conn.execute("UPDATE saas_crm_sync_outbox SET lease_until=now()-interval '1 hour' WHERE id=%s::uuid",(row['id'],))
            retry=legacy.claim()
            assert retry['attempts']==row['attempts']+1
            assert asyncio.run(legacy._send(retry,None,request,Mock()))==('busy','legacy_card_busy')
            with factory() as conn:
                attempt=conn.execute('SELECT outcome,claim_attempt FROM saas_crm_legacy_delivery_attempts WHERE event_id=%s::uuid',(row['id'],)).fetchone()
            assert attempt=={'outcome':'sending','claim_attempt':row['attempts']}
            # Different cards are not serialized behind the provider request.
            unrelated=pool.submit(contacts.update_contact,A,scalar['contacts'][1].internal_id,'Other','',actor_id=81)
            assert unrelated.result(5)['sync']['local_saved'] is True
        finally:release.set()
        result=sending.result(10);saved=editing.result(10)
    assert len(calls)==1 and calls[0][1]=='PATCH'
    if timeout:
        assert result=='timeout'
        assert saved['sync']['state']=='needs_verification'
        assert saved['sync']['error_code']=='scalar_legacy_outcome_unknown'
    else:
        assert result==('synced','')
        assert saved['sync']['state']=='pending'


def test_different_event_busy_claim_can_send_after_active_sender_finishes(scalar,monkeypatch):
    first=legacy_event(scalar,'pending',0)
    factory=lambda:psycopg.connect(scalar['db'].dsn,row_factory=dict_row)
    monkeypatch.setattr(legacy,'_connect',factory)
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    with factory() as conn:
        with conn.cursor() as cur:
            legacy.record_link(cur,tenant_id=A,provider='kommo',entity_type='contact',
                internal_id=first['internal_id'],external_id='1',sync_state='synced')
    first=legacy.claim()
    second=legacy_event(scalar,'pending',0)
    entered,release=threading.Event(),threading.Event()
    calls=[]
    async def request(*args,**kwargs):
        calls.append(args)
        if len(calls)==1:entered.set();assert release.wait(10)
        return {}
    with ThreadPoolExecutor(1) as pool:
        sending=pool.submit(lambda:asyncio.run(legacy._send(first,None,request,Mock())))
        try:
            assert entered.wait(10)
            busy=legacy.claim()
            assert busy['id']==second['id']
            assert asyncio.run(legacy._send(busy,None,request,Mock()))==('busy','legacy_card_busy')
            with factory() as conn:
                attempt=conn.execute('SELECT outcome,claim_attempt FROM saas_crm_legacy_delivery_attempts WHERE event_id=%s::uuid',(second['id'],)).fetchone()
                assert attempt=={'outcome':'not_started','claim_attempt':1}
                conn.execute("UPDATE saas_crm_sync_outbox SET lease_until=now()-interval '1 hour' WHERE id=%s::uuid",(second['id'],))
        finally:release.set()
        assert sending.result(10)==('synced','')
    retry=legacy.claim()
    assert retry['id']==second['id'] and retry['attempts']==2
    assert asyncio.run(legacy._send(retry,None,request,Mock()))==('synced','')
    assert len(calls)==2 and all(call[1]=='PATCH' for call in calls)
