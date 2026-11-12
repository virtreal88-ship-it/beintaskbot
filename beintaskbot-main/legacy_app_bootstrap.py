"""Compose the legacy Telegram application and its HTTP thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler


class HTTPServerHandle:
    """Own the aiohttp loop so Telegram's shutdown hook can drain it cleanly."""

    def __init__(self, start_server: Callable, logger):
        self.start_server = start_server
        self.logger = logger
        self.ready = threading.Event()
        self.loop = None
        self.runner = None
        self.thread = threading.Thread(target=self._run, name="http-server", daemon=True)
        self._stop_lock = threading.Lock()
        self._stop_future: Future | None = None

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            self.runner = loop.run_until_complete(self.start_server())
        except Exception as exc:
            self.logger.error("HTTP server failed: %s", exc)
        finally:
            self.ready.set()
        if self.runner is None:
            loop.close()
            return
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def stop(self):
        await asyncio.to_thread(self.ready.wait)
        if self.runner is None or self.loop is None:
            return
        with self._stop_lock:
            if self._stop_future is None:
                self._stop_future = asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop)
            stop_future = self._stop_future
        try:
            await asyncio.wrap_future(stop_future)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            await asyncio.to_thread(self.thread.join)


def start_http_thread(start_server: Callable, logger) -> HTTPServerHandle:
    return HTTPServerHandle(start_server, logger).start()


def build_telegram_application(
    *, token: str, post_init: Callable, post_shutdown: Callable | None = None,
    command_handlers: list[tuple[str, Callable]],
    callback_handlers: list[tuple[str, Callable]],
    message_handlers: list[tuple[object, Callable]],
    repeating_jobs: list[tuple[Callable, int, int, dict | None]],
    daily_jobs: list[tuple[Callable, object]],
):
    builder = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .post_init(post_init)
    )
    if post_shutdown is not None:
        builder = builder.post_shutdown(post_shutdown)
    app = builder.build()
    for command, callback in command_handlers:
        app.add_handler(CommandHandler(command, callback))
    for pattern, callback in callback_handlers:
        app.add_handler(CallbackQueryHandler(callback, pattern=pattern))
    for message_filter, callback in message_handlers:
        app.add_handler(MessageHandler(message_filter, callback))
    for callback, interval, first, job_kwargs in repeating_jobs:
        kwargs = {"interval": interval, "first": first}
        if job_kwargs:
            kwargs["job_kwargs"] = dict(job_kwargs)
        app.job_queue.run_repeating(callback, **kwargs)
    for callback, run_time in daily_jobs:
        app.job_queue.run_daily(callback, time=run_time)
    return app


def run_polling(app) -> None:
    # Keep updates queued while deployments overlap or the worker restarts.
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
