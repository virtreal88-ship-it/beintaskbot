"""Bounded same-origin password entry for the opt-in review account only."""

import asyncio
import hmac
import time
from pathlib import Path

from aiohttp import web
from moderation_demo import config, password_matches, provision

_attempts: list[float] = []
_lock = asyncio.Lock()


async def page(_request: web.Request) -> web.Response:
    if config() is None:
        raise web.HTTPNotFound()
    return web.FileResponse(Path(__file__).resolve().parent / "docs" / "moderation-login.html",
                            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


def handler(make_session, cookie_name: str, canonical_origin: str, logger):
    async def login(request: web.Request) -> web.Response:
        current = config()
        if current is None:
            raise web.HTTPNotFound()
        if request.headers.get("Origin", "").rstrip("/") != canonical_origin.rstrip("/"):
            return web.json_response({"success": False, "error": "Use the login page on this website."}, status=403)
        if request.content_type != "application/json" or request.content_length is None or request.content_length > 4096:
            return web.json_response({"success": False, "error": "Invalid request."}, status=400)
        async with _lock:
            now = time.monotonic()
            _attempts[:] = [stamp for stamp in _attempts if now - stamp < 60]
            if len(_attempts) >= 5:
                return web.json_response({"success": False, "error": "Please try again in one minute."}, status=429,
                                         headers={"Retry-After": "60", "Cache-Control": "no-store"})
            _attempts.append(now)
            try:
                data = await request.json()
                if not isinstance(data, dict):
                    raise ValueError("Invalid body")
                username, password = data.get("username"), data.get("password")
                valid = isinstance(username, str) and isinstance(password, str) and len(username) <= 200
                if valid:
                    username_ok = hmac.compare_digest(username.encode(), current.username.encode())
                    password_ok = await asyncio.to_thread(password_matches, password, current.password_hash)
                    valid = username_ok and password_ok
                if not valid:
                    return web.json_response({"success": False, "error": "Invalid login or password."}, status=401,
                                             headers={"Cache-Control": "no-store"})
                await asyncio.to_thread(provision, current)
                # Recheck expiry/configuration after DB work, before granting a session.
                if config() != current:
                    raise ValueError("Review access changed")
                response = web.json_response({"success": True, "redirect": "/setup"}, headers={"Cache-Control": "no-store"})
                response.set_cookie(cookie_name, make_session(current.tenant_id, current.user_id),
                                    max_age=min(8 * 3600, current.expires_at - int(time.time())),
                                    secure=True, httponly=True, samesite="Lax", path="/")
                return response
            except (TypeError, ValueError):
                return web.json_response({"success": False, "error": "Review access is unavailable."}, status=400,
                                         headers={"Cache-Control": "no-store"})
            except Exception:
                logger.exception("Isolated moderation login failed")
                return web.json_response({"success": False, "error": "Review access is unavailable. Contact support."}, status=503,
                                         headers={"Cache-Control": "no-store"})
    return login
