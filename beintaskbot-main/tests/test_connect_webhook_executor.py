"""Synthetic concurrency regressions. No DB, provider traffic or customer sends."""
import asyncio
import concurrent.futures
import contextvars
import json
import threading
from unittest.mock import MagicMock

import pytest
import tenant_connect_api as api
from tenant_connect_transport import ConnectWebhookError


class Request:
    headers = {'x-connect-signature-256': 'sha256=synthetic'}

    async def read(self):
        return b'{"id":"synthetic","type":"message.inbound.received","data":{}}'


async def wait_for(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), 2)


def test_backlog_cannot_starve_default_executor_and_ack_waits_for_commit(monkeypatch):
    async def scenario():
        release = threading.Event()
        entered = []
        lock = threading.Lock()

        def ingest(*_args):
            with lock:
                entered.append(threading.current_thread().name)
            assert release.wait(3)
            return True

        monkeypatch.setattr(api, 'ingest', ingest)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='ingest-test') as pool:
            monkeypatch.setattr(api, '_webhook_io', pool)
            asyncio.get_running_loop().set_default_executor(
                concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='ui-test'))
            handler = api.webhook_handler(MagicMock())
            requests = [asyncio.create_task(handler(Request())) for _ in range(10)]
            try:
                await wait_for(lambda: len(entered) == 2)
                assert not any(request.done() for request in requests)
                # Authentication/data reads and aiohttp file I/O use this pool.
                assert await asyncio.wait_for(asyncio.to_thread(lambda: 'ui-responsive'), .5) == 'ui-responsive'
                assert len(entered) == 2
                assert all(name.startswith('ingest-test') for name in entered)
            finally:
                release.set()
                responses = await asyncio.gather(*requests)
            assert all(response.status == 200 and json.loads(response.text) == {'accepted': True}
                       for response in responses)
            assert len(entered) == 10
    asyncio.run(scenario())


def test_context_and_rejection_survive_executor_isolation(monkeypatch):
    marker = contextvars.ContextVar('webhook-request-marker')

    def ingest(*_args):
        assert marker.get() == 'synthetic-context'
        raise ConnectWebhookError('invalid_signature', 'rejected', 401)

    monkeypatch.setattr(api, 'ingest', ingest)

    async def scenario():
        marker.set('synthetic-context')
        response = await api.webhook_handler(MagicMock())(Request())
        assert response.status == 401
        assert json.loads(response.text) == {'error': 'invalid_signature'}
    asyncio.run(scenario())


def test_cancelled_queued_request_does_not_ingest_but_running_commit_finishes(monkeypatch):
    async def scenario():
        release, entered, committed = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def ingest(*_args):
            calls.append(1)
            entered.set()
            assert release.wait(3)
            committed.set()
            return True

        monkeypatch.setattr(api, 'ingest', ingest)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            monkeypatch.setattr(api, '_webhook_io', pool)
            handler = api.webhook_handler(MagicMock())
            running = asyncio.create_task(handler(Request()))
            try:
                await wait_for(entered.is_set)
                queued = asyncio.create_task(handler(Request()))
                await asyncio.sleep(0)
                queued.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await queued
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
                assert not committed.is_set()
            finally:
                release.set()
            await wait_for(committed.is_set)
        assert len(calls) == 1
    asyncio.run(scenario())


def test_failed_ingestion_is_not_acknowledged(monkeypatch):
    def ingest(*_args):
        raise RuntimeError('synthetic rollback')
    monkeypatch.setattr(api, 'ingest', ingest)
    response = asyncio.run(api.webhook_handler(MagicMock())(Request()))
    assert response.status == 500
    assert json.loads(response.text) == {'error': 'webhook_failed'}
