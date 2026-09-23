#!/usr/bin/env python3
"""Local development entrypoint that runs ONLY the aiohttp web server.

The production entrypoint (``bot.py``) starts Telegram long polling in addition
to the web server. Long polling requires a real ``TELEGRAM_TOKEN`` and, when run
with the production token, conflicts with the live bot (Telegram allows only one
``getUpdates`` consumer per token). For local development we bring up just the
web server so the CRM web app and HTTP API can be exercised without touching the
production Telegram bot.

Set ``PORT`` to change the listen port (defaults to 8080). ``TELEGRAM_TOKEN`` is
only read at import time for module configuration and is never used to reach
Telegram here, so a placeholder value is fine.
"""
import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "local-dev-webserver-only")

import bot  # noqa: E402  (import after setting env defaults)


async def _main() -> None:
    await bot.start_webhook_server()
    print(f"Local web server running on http://0.0.0.0:{bot.WEBHOOK_PORT}", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(_main())
