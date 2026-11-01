"""Run the notification contracts through real psycopg against disposable local PostgreSQL.

The WASM adapter substitutes placeholders itself and cannot detect psycopg's
percent-token parsing. Keep this production-driver boundary covered in CI.
"""
from test_tenant_notification_center import *  # noqa: F401,F403 -- shared behavioral contracts
from test_connect_channel_multiprocess import database  # noqa: F401 -- real driver fixture


def test_real_http_and_full_app_browser_read_the_native_postgres_ledger(ledger, monkeypatch):
    import logging
    import os
    from pathlib import Path
    import socket
    import subprocess
    import threading
    import psycopg
    from psycopg.rows import dict_row
    root = Path(__file__).resolve().parents[1]
    if not (root/'.support-test-deps/node_modules/playwright').exists():
        pytest.skip('Install the browser regression dependencies')
    ids = {notice(ledger, event='task:runtime:'+str(index)) for index in range(3)}
    state = {'offline': False, 'posts': []}
    def connect():
        if state['offline']:
            raise psycopg.OperationalError('Synthetic unavailable local database')
        return psycopg.connect(ledger.dsn, row_factory=dict_row)
    monkeypatch.setattr(center, '_connect', connect)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(16)
    origin = 'http://127.0.0.1:'+str(sock.getsockname()[1])
    loop, ready = asyncio.new_event_loop(), threading.Event()
    async def start():
        @web.middleware
        async def no_writes(request, handler):
            if request.method != 'GET':
                state['posts'].append(request.path)
                return web.Response(status=405)
            return await handler(request)
        app = web.Application(middlewares=[no_writes])
        async def index(_):
            return web.FileResponse(root/'docs/tenant-app.html')
        async def asset(request):
            file = root/'docs'/request.match_info['name']
            return web.FileResponse(file) if file.is_file() else web.Response(status=404)
        async def profile(_):
            return web.json_response({'success': True, 'member': {**PROFILE, 'role': 'owner', 'active': True},
                'capabilities': {'modules': {'tasks': True, 'dialogs': True}}})
        async def toggle(request):
            state['offline'] = request.match_info['state'] == 'offline'
            return web.json_response({'success': True})
        async def other(_):
            return web.json_response({'success': True, 'result': {}})
        app.router.add_get('/app', index)
        app.router.add_get('/assets/{name}', asset)
        app.router.add_get('/api/platform/me', profile)
        app.router.add_get('/api/platform/notifications', handler(lambda _: PROFILE, origin, logging.getLogger(__name__)))
        app.router.add_get('/test/{state}', toggle)
        app.router.add_get('/api/{tail:.*}', other)
        runner = web.AppRunner(app); await runner.setup(); await web.SockSite(runner, sock).start()
        state['runner'] = runner; ready.set()
    def serve():
        asyncio.set_event_loop(loop); loop.run_until_complete(start()); loop.run_forever()
    thread = threading.Thread(target=serve, daemon=True); thread.start(); assert ready.wait(10)
    try:
        env = {**os.environ, 'NOTIFICATION_RUNTIME_ORIGIN': origin}
        completed = subprocess.run(['node', str(root/'tests/test_tenant_notification_center_postgres_browser.cjs')],
            cwd=root, env=env, capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stdout+'\n'+completed.stderr
        assert not state['posts'], state['posts']
        with psycopg.connect(ledger.dsn, row_factory=dict_row) as conn:
            rows = conn.execute('SELECT id::text,read_at FROM saas_tenant_notifications WHERE tenant_id=%s::uuid AND telegram_id=%s', (A, 11)).fetchall()
        assert {row['id'] for row in rows} == ids
        assert all(row['read_at'] is None for row in rows)
    finally:
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop); thread.join(timeout=10); loop.close()
