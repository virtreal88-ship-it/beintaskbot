"""Rate-limited HTTP transport for the single legacy Kommo account."""
from __future__ import annotations

import base64
import collections
from dataclasses import dataclass
import json
import threading
import time
from urllib.parse import urlparse

import requests


KOMMO_MAX_RPS = 6
KOMMO_WINDOW_SEC = 1.05
KOMMO_MIN_GAP = max(0.170, 1.0 / KOMMO_MAX_RPS)


def is_kommo_url(url: object) -> bool:
    try:
        raw = str(url or "").strip().lower()
        host = (urlparse(raw).hostname or "").lower()
        return (
            host == "kommo.com" or host.endswith(".kommo.com")
            or host == "amocrm.ru" or host.endswith(".amocrm.ru")
            or host == "amocrm.com" or host.endswith(".amocrm.com")
        )
    except Exception:
        return False


def token_has_scope(token: str, scope: str) -> bool:
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return False
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return scope in (data.get("scopes") or [])
    except Exception:
        return False


class KommoRateLimiter:
    """Thread-safe rolling-window limiter shared by legacy HTTP sessions."""

    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.history = collections.deque()
        self.next_at = 0.0
        self.cooldown_until = 0.0

    def engage_cooldown(self, seconds: float = 3.0) -> None:
        with self.lock:
            now = self.clock()
            self.cooldown_until = max(self.cooldown_until, now + seconds)
            self.next_at = max(self.next_at, self.cooldown_until)
            self.history.clear()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = self.clock()
                cooldown_delay = max(0.0, self.cooldown_until - now)
            if cooldown_delay:
                self.sleep(cooldown_delay)
                continue
            with self.lock:
                now = self.clock()
                if now < self.cooldown_until:
                    continue
                base = max(now, self.next_at)
                while self.history and self.history[0] <= base - KOMMO_WINDOW_SEC:
                    self.history.popleft()
                slot = max(base, self.history[0] + KOMMO_WINDOW_SEC) if len(self.history) >= KOMMO_MAX_RPS else base
                self.next_at = slot + KOMMO_MIN_GAP
                self.history.append(slot)
            delay = slot - self.clock()
            interrupted = False
            while delay > 0:
                self.sleep(min(delay, 0.05))
                if self.clock() < self.cooldown_until:
                    interrupted = True
                    break
                delay = slot - self.clock()
            if not interrupted:
                return


@dataclass(frozen=True)
class LegacyKommoRuntime:
    headers: dict[str, str]
    session: requests.Session
    limiter: KommoRateLimiter
    probe_status: int


def install_legacy_kommo_transport(*, token: str, base_url: str, logger,
                                   requests_module=requests, probe: bool = True) -> LegacyKommoRuntime:
    """Install the historical process-wide pacing hook exactly once."""
    limiter = KommoRateLimiter()
    current_request = requests_module.Session.request
    original_request = getattr(current_request, "_legacy_kommo_original", current_request)

    def paced_request(session, method, url, *args, **kwargs):
        kommo = is_kommo_url(url)
        response = None
        for attempt in range(3):
            if kommo:
                limiter.wait()
            response = original_request(session, method, url, *args, **kwargs)
            if not kommo or getattr(response, "status_code", 0) != 429 or attempt >= 2:
                return response
            logger.warning("Kommo returned 429 on attempt %s; initiating global cooldown", attempt + 1)
            limiter.engage_cooldown(2.5 * (attempt + 1))
        return response

    paced_request._legacy_kommo_original = original_request
    requests_module.Session.request = paced_request
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    probe_status = 0
    if probe:
        try:
            response = requests_module.get(
                f"{base_url}/api/v4/account",
                headers={"Authorization": f"Bearer {token}"}, timeout=10)
            probe_status = int(getattr(response, "status_code", 0) or 0)
        except Exception:
            logger.warning("Kommo token probe failed")
        if probe_status != 200:
            logger.error("Railway KOMMO_TOKEN check failed with HTTP %s", probe_status)
        elif not token_has_scope(token, "list_external_messages"):
            logger.warning("Railway KOMMO_TOKEN does not advertise list_external_messages scope")
    session = requests_module.Session()
    session.headers.update(headers)
    session.mount("https://", requests_module.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))
    return LegacyKommoRuntime(headers=headers, session=session, limiter=limiter, probe_status=probe_status)
