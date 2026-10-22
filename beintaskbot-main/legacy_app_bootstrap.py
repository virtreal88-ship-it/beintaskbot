"""Compose the legacy Telegram application and its HTTP thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler


def start_http_thread(start_server: Callable, logger) -> threading.Thread:
    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(start_server())
            loop.run_forever()
        except Exception as exc:
            logger.error("HTTP server failed: %s", exc)

    thread = threading.Thread(target=run, name="http-server", daemon=True)
    thread.start()
    return thread


def build_telegram_application(
    *, token: str, post_init: Callable,
    command_handlers: list[tuple[str, Callable]],
    callback_handlers: list[tuple[str, Callable]],
    message_handlers: list[tuple[object, Callable]],
    repeating_jobs: list[tuple[Callable, int, int, dict | None]],
    daily_jobs: list[tuple[Callable, object]],
):
    app = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .post_init(post_init)
        .build()
    )
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
