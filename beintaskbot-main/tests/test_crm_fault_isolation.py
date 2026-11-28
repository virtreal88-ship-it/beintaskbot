import asyncio
import logging
import threading
from unittest.mock import Mock

import psycopg
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_connect_avatars as avatars
import tenant_crm_sync_worker as sync
from tenant_operator_api import handler
import worker_crm_health as health
from worker_crm_capacity import WorkPool, CapacityUnavailable
from test_startup_transaction_budget_postgres import database


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


def test_group_of_optional_downloads_and_stuck_sync_dont_stop_dialogs(monkeypatch):
    async def scenario():
        blocked, entered, provider = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def fetch(_):
            entered.set()
            await blocked.wait()
            return b'image', 'image/png'

        async def remote(*args, **kwargs):
            provider.set()
            await blocked.wait()
            return {'_embedded': {'tasks': []}}

        row = {'tenant_id': 'tenant', 'resource': 'tasks', 'next_page': 1}
        store = Mock(row=row)
        claim = Mock(side_effect=[store, None])
        monkeypatch.setattr(sync.SyncPageStore, 'claim', claim)
        monkeypatch.setattr(sync, 'window_params', lambda *args: {})
        monkeypatch.setattr(avatars, '_prepare_avatar', lambda *_: ('tenant', {}))
        monkeypatch.setattr(avatars, '_fetch', fetch)
        monkeypatch.setattr(avatars, '_complete_avatar', lambda *_: None)
        monkeypatch.setattr(avatars, '_avatar_requests', threading.BoundedSemaphore(1))
        monkeypatch.setattr('tenant_operator_api.listing', lambda *args, **kwargs: {'items': []})
        def member(req):
            return {'tenant_id':'tenant','telegram_id':1} if req.headers.get('X-Test') else None
        app = web.Application()
        for route in ('dialogs','avatar'):
            app.router.add_get('/'+route, handler(member,'',logging.getLogger(__name__),route))
        async with TestClient(TestServer(app), headers={'X-Test':'1'}) as client:
            photo = asyncio.create_task(client.get('/avatar'))
            batch = asyncio.create_task(sync.run_sync_batch(remote, logging.getLogger(__name__)))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.wait_for(provider.wait(), 2)
                assert (await client.get('/avatar')).status == 503
                await asyncio.wait_for(sync.run_sync_batch(remote, None), .5)
                assert claim.call_count == 1
                assert (await asyncio.wait_for(client.get('/dialogs'), .8)).status == 200
                assert not store.save_page.called
            finally:
                blocked.set()
                await batch
                assert (await photo).status == 200
            store.save_page.assert_called_once()
            store.close.assert_called_once()
            assert (await client.get('/avatar')).status == 200
    asyncio.run(scenario())


def test_timeout_and_cancellation_do_not_reopen_session_capacity():
    async def scenario():
        pool = WorkPool('test-session',1,1,.1,100)
        release = threading.Event()
        entered = threading.Event()
        def stall():
            entered.set()
            release.wait(3)
        try:
            with pytest.raises(CapacityUnavailable):
                await pool.run(stall)
            assert entered.is_set()
            with pytest.raises(CapacityUnavailable):
                await pool.run(lambda: True)
        finally:
            release.set()
            pool.executor.shutdown()
    asyncio.run(scenario())


def test_real_lock_is_attributed_readiness_fails_and_recovers(database, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', database)
    with psycopg.connect(database, autocommit=True) as setup:
        setup.execute('CREATE TABLE saas_tenants(id uuid, modules jsonb)')
        setup.execute('CREATE TABLE saas_tenant_members(tenant_id uuid)')
        setup.execute('CREATE TABLE saas_connect_conversations(tenant_id uuid, conversation_id text)')
    async def scenario():
        app = web.Application()
        app.router.add_get('/readyz', health.readiness_handler())
        async def shell(request):
            return web.Response(text='shell')
        app.router.add_get('/app', shell)
        with psycopg.connect(database, application_name='crm-migration') as blocker:
            blocker.execute('LOCK saas_tenant_members IN ACCESS EXCLUSIVE MODE')
            async with TestClient(TestServer(app)) as client:
                probe = asyncio.create_task(client.get('/readyz'))
                async with asyncio.timeout(2):
                    while True:
                        edges = await asyncio.to_thread(health.lock_snapshot)
                        if any(e['holder_pid']==blocker.info.backend_pid for e in edges):
                            break
                        await asyncio.sleep(.01)
                edge = next(e for e in edges if e['holder_pid']==blocker.info.backend_pid)
                assert edge['holder_xid'] and edge['component']=='crm-migration'
                assert edge['state']=='idle_in_transaction' and edge['lock_type']=='relation'
                assert (await asyncio.wait_for(client.get('/app'),3)).status==200
                result = await probe
                assert result.status==503 and await result.text()=='unavailable'
                blocker.rollback()
                await asyncio.sleep(2.05)
                assert (await client.get('/readyz')).status==200
                assert not health.lock_snapshot()
    asyncio.run(scenario())


def test_connect_overload_and_unknown_ack_retry_uses_same_durable_event(database, monkeypatch):
    import tenant_connect_api as api
    from test_connect_webhook_executor import Request
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute('CREATE TABLE synthetic_events(id text PRIMARY KEY)')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def ingest(_raw, _signature, event):
        with psycopg.connect(database) as conn:
            row = conn.execute('INSERT INTO synthetic_events VALUES (%s) ON CONFLICT DO NOTHING RETURNING id',
                               (event['id'],)).fetchone()
            entered.set()
            assert release.wait(5)
        finished.set()
        return bool(row)
    monkeypatch.setattr(api,'_webhook_deadline',.3)
    monkeypatch.setattr(api,'_webhook_slots',threading.BoundedSemaphore(1))
    monkeypatch.setattr(api,'ingest',ingest)
    async def scenario():
        post = api.webhook_handler(logging.getLogger(__name__))
        first = asyncio.create_task(post(Request()))
        try:
            await eventually(entered.is_set)
            assert (await post(Request())).status == 503
            response = await first
            assert response.status == 503 and 'webhook_unconfirmed' in response.text
        finally:
            release.set()
        await eventually(finished.is_set)
        retry = await post(Request())
        assert retry.status == 200 and 'false' in retry.text
        with psycopg.connect(database) as conn:
            assert conn.execute('SELECT count(*) FROM synthetic_events').fetchone() == (1,)
    asyncio.run(scenario())
