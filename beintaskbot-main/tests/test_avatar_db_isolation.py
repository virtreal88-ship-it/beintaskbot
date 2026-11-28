"""A stalled avatar database call cannot stall other HTTP requests."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

import tenant_connect_avatars as avatars
from tenant_linear_policy import TenantLinearError


def test_avatar_database_deadline_does_not_block_http_or_release_active_slot(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    worker_threads = []
    event_loop_thread = threading.get_ident()

    def stalled(*args):
        worker_threads.append(threading.get_ident())
        entered.set()
        release.wait(3)
        return 'tenant', {}

    async def image(request):
        try:
            await avatars.get_avatar({}, {'provider': 'connect'})
        except TenantLinearError as exc:
            return web.Response(status=exc.status)
        return web.Response(status=204)

    async def ordinary(request):
        return web.Response(text='ok')

    async def exercise():
        app = web.Application()
        app.router.add_get('/dialogs/avatar', image)
        app.router.add_get('/health', ordinary)
        app.router.add_get('/ordinary', ordinary)
        async with TestClient(TestServer(app)) as client:
            started = asyncio.create_task(client.get('/dialogs/avatar'))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                for path in ('/health', '/ordinary'):
                    response = await asyncio.wait_for(client.get(path), 0.8)
                    assert response.status == 200
                    await response.read()
                response = await asyncio.wait_for(started, 1)
                assert response.status == 503
                response = await client.get('/dialogs/avatar')
                assert response.status == 503
                assert len(worker_threads) == 1
                assert worker_threads[0] != event_loop_thread
            finally:
                release.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(avatars, '_AVATAR_DB_POOL', pool)
        monkeypatch.setattr(avatars, '_AVATAR_DB_SLOTS', threading.BoundedSemaphore(1))
        monkeypatch.setattr(avatars, '_AVATAR_DB_DEADLINE', 0.25)
        monkeypatch.setattr(avatars, '_prepare_avatar', stalled)
        asyncio.run(exercise())


def test_avatar_does_not_return_downloaded_image_after_access_revocation(monkeypatch):
    active = True
    route = {'channel_id': 'channel', 'conversation_id': 'chat', 'api_key': 'fake'}

    @contextmanager
    def db():
        class Connection:
            @contextmanager
            def cursor(self):
                yield None
        yield Connection()

    def access(*args):
        if not active:
            raise TenantLinearError('Denied', 403)
        return 'tenant', 1, {}

    async def download(*args):
        nonlocal active
        active = False
        return b'protected image', 'image/png'

    monkeypatch.setattr(avatars, '_prepare_avatar', lambda *args: ('tenant', route))
    monkeypatch.setattr(avatars, '_connect', db)
    monkeypatch.setattr(avatars, 'access', access)
    monkeypatch.setattr(avatars, '_fetch', download)
    with pytest.raises(TenantLinearError) as exc:
        asyncio.run(avatars.get_avatar({}, {'provider': 'connect'}))
    assert exc.value.status == 403

