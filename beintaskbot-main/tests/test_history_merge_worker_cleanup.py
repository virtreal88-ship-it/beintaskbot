"""Exercise the production aiohttp startup order without a listener or database."""
import asyncio
import ast
import threading
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

import tenant_ai_agent_startup as startup
import tenant_dialog_history_merge_jobs as jobs


async def configured_runner():
    app = web.Application()
    startup.install_history_merge_worker_cleanup(app)
    startup.install_history_merge_worker_cleanup(app)
    runner = web.AppRunner(app)
    await runner.setup()  # Production runs deferred queue startup after this.
    assert app.frozen
    return app, runner


def test_web_entry_installs_cleanup_before_runner_setup():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text())
    entry = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'start_webhook_server')
    install = next(n.lineno for n in ast.walk(entry) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == 'install_history_merge_worker_cleanup')
    setup = next(n.lineno for n in ast.walk(entry) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'setup')
    assert install < setup


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_runner_cleanup_only_stops_enabled_history_worker(monkeypatch, enabled):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    unrelated = asyncio.Event()

    async def feature():
        entered.set()
        try:
            await asyncio.Future()  # Simulates a cancellable in-flight provider wait.
        finally:
            cancelled.set()

    async def ai(*args):
        await unrelated.wait()

    feature_entry = AsyncMock(side_effect=feature)
    monkeypatch.setattr(jobs, 'enabled', lambda: enabled)
    monkeypatch.setattr(jobs, 'worker', feature_entry)
    app, runner = await configured_runner()
    await startup.start_worker(app, ai, None, None, None, require_ready=lambda: None)
    legacy_task = app['tenant_ai_agent_worker']
    try:
        if enabled:
            await asyncio.wait_for(entered.wait(), timeout=1)
        # An accidental second attempt cannot spawn another worker.
        startup._start_history_merge_worker(app)
        await runner.cleanup()
        assert not legacy_task.done()
        assert cancelled.is_set() is enabled
        assert feature_entry.await_count == int(enabled)
    finally:
        unrelated.set()
        await legacy_task


@pytest.mark.asyncio
async def test_cleanup_fences_deferred_start_during_readiness(monkeypatch):
    loop = asyncio.get_running_loop()
    ready_started = asyncio.Event()
    ready_release = threading.Event()
    feature = AsyncMock()
    monkeypatch.setattr(jobs, 'enabled', lambda: True)
    monkeypatch.setattr(jobs, 'worker', feature)

    def readiness():
        loop.call_soon_threadsafe(ready_started.set)
        assert ready_release.wait(timeout=3)

    app, runner = await configured_runner()
    deferred = asyncio.create_task(startup.start_worker(app, AsyncMock(), None, None, None, require_ready=readiness))
    try:
        await asyncio.wait_for(ready_started.wait(), timeout=1)
        await runner.cleanup()
    finally:
        ready_release.set()
        await deferred
        await app['tenant_ai_agent_worker']
    feature.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_bound_does_not_wait_indefinitely_for_cancellation(monkeypatch):
    entered, delayed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def worker():
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            delayed.set()
            await release.wait()

    monkeypatch.setattr(jobs, 'worker', worker)
    monkeypatch.setattr(jobs, 'enabled', lambda: True)
    monkeypatch.setattr(startup, '_HISTORY_MERGE_CLEANUP_SECONDS', .01)
    app, runner = await configured_runner()
    startup._start_history_merge_worker(app)
    task = app[startup._HISTORY_MERGE_WORKER].task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(runner.cleanup(), timeout=1)
        assert delayed.is_set() and not task.done()
    finally:
        release.set()
        await task
