"""Tenant-scoped Kommo OAuth, token refresh and connection HTTP API."""
import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
_rate_lock = threading.Lock()
_next_request: dict[str, float] = {}


def oauth_settings(origin: str, environ: Mapping[str, str] | None = None,
                   error: type[Exception] = ValueError) -> tuple[str, str, str]:
    values = environ if environ is not None else os.environ

    def first(*names: str) -> str:
        for name in names:
            value = str(values.get(name) or "").strip()
            if value:
                return value
        return ""

    client_id = first("KOMMO_OAUTH_CLIENT_ID", "KOMMO_CLIENT_ID", "KOMMO_PUBLIC_CLIENT_ID",
                      "KOMMO_INTEGRATION_CLIENT_ID", "KOMMO_INTEGRATION_ID")
    client_secret = first("KOMMO_OAUTH_CLIENT_SECRET", "KOMMO_CLIENT_SECRET", "KOMMO_PUBLIC_CLIENT_SECRET",
                          "KOMMO_INTEGRATION_CLIENT_SECRET", "KOMMO_SECRET_KEY")
    redirect_uri = first("KOMMO_OAUTH_REDIRECT_URI", "KOMMO_REDIRECT_URI",
                         "KOMMO_OAUTH_REDIRECT_URL", "KOMMO_REDIRECT_URL") \
        or f"{origin}/api/platform/integrations/kommo/callback"
    if str(values.get("STAGING_MODE") or "").strip().casefold() in {"1", "true", "yes", "on"}:
        expected_origin = urlparse(origin)
        callback = urlparse(redirect_uri)
        if (expected_origin.scheme != "https" or not expected_origin.netloc
                or callback.scheme != "https" or callback.netloc.casefold() != expected_origin.netloc.casefold()
                or callback.path != "/api/platform/integrations/kommo/callback"
                or callback.query or callback.fragment):
            raise error("Staging Kommo callback must use the staging HTTPS host and exact callback path.")
    if not client_id or not client_secret:
        missing = []
        if not client_id:
            missing.append("KOMMO_OAUTH_CLIENT_ID")
        if not client_secret:
            missing.append("KOMMO_OAUTH_CLIENT_SECRET")
        raise error(
            "Kommo bağlantısı hələ aktiv deyil. Railway-də public Kommo inteqrasiyasının "
            f"{', '.join(missing)} dəyişənini əlavə edin və deploy edin."
        )
    return client_id, client_secret, redirect_uri


def callback_domain(value: str, error: type[Exception] = ValueError) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raise error("Kommo hesab ünvanı cavabda yoxdur.")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    domain = (parsed.netloc or parsed.path).split("/", 1)[0]
    if not domain.endswith(".kommo.com"):
        raise error("Kommo hesab ünvanı etibarlı deyil.")
    return domain


def token_is_expiring(value: str, *, now: datetime | None = None) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= (now or datetime.now(timezone.utc)) + timedelta(minutes=3)
    except (TypeError, ValueError):
        return True


def wait_slot(account_domain: str) -> None:
    """Serialize each account below Kommo's seven requests per second limit."""
    with _rate_lock:
        now = time.monotonic()
        available_at = _next_request.get(account_domain, now)
        _next_request[account_domain] = max(now, available_at) + (1 / 6)
    delay = max(0.0, available_at - now)
    if delay:
        time.sleep(delay)


@dataclass(frozen=True)
class KommoContext:
    member: Callable[..., dict | None]
    origin: str
    logger: logging.Logger
    platform_error: type[Exception]
    begin_oauth: Callable[..., str]
    consume_oauth_state: Callable[[str], dict | str]
    disconnect_integration: Callable[..., dict]
    credentials: Callable[..., dict]
    replace_tokens: Callable[..., object]
    request_connection: Callable[..., dict]
    save_tokens: Callable[..., object]
    save_workflow: Callable[..., dict]
    update_onboarding: Callable[..., dict]
    append_audit: Callable[..., object]


class TenantKommo:
    def __init__(self, context: KommoContext):
        self.context = context

    def settings(self) -> tuple[str, str, str]:
        return oauth_settings(self.context.origin, error=self.context.platform_error)

    def _verify_legacy_account(self, credentials: dict) -> None:
        """Verify the shared legacy bearer token belongs to the explicitly linked account."""
        domain = str(credentials.get("account_domain") or "")
        expected = str(credentials.get("expected_account_id") or "")
        if not domain or not expected.isdigit():
            raise self.context.platform_error("Mövcud Kommo bağlantısının hesabı təsdiqlənməyib.")
        wait_slot(domain)
        try:
            response = requests.get(
                f"https://{domain}/api/v4/account",
                headers={"Authorization": f"Bearer {credentials['access_token']}", "Accept": "application/json"},
                timeout=15,
            )
            payload = response.json() if response.status_code == 200 else {}
        except Exception:
            self.context.logger.warning("Legacy Kommo account verification failed")
            raise self.context.platform_error("Mövcud Kommo hesabı təsdiqlənmədi.") from None
        if not isinstance(payload, dict) or str(payload.get("id") or "") != expected:
            self.context.logger.warning("Legacy Kommo account identity mismatch")
            raise self.context.platform_error("Mövcud Kommo hesabının ID-si uyğun gəlmir.")
        actual_subdomain = str(payload.get("subdomain") or "").strip().casefold()
        expected_subdomain = domain.split(".", 1)[0].casefold()
        if actual_subdomain and actual_subdomain != expected_subdomain:
            self.context.logger.warning("Legacy Kommo account domain mismatch")
            raise self.context.platform_error("Mövcud Kommo hesabının ünvanı uyğun gəlmir.")

    async def request(self, tenant_id: str, method: str, path: str, *,
                      params: dict | None = None, json_body: dict | list | None = None) -> dict:
        def run() -> dict:
            credentials = self.context.credentials(tenant_id=tenant_id)
            source = str(credentials.get("credential_source") or "tenant_oauth")
            if source not in {"tenant_oauth", "legacy_environment"}:
                raise self.context.platform_error("Kommo bağlantı mənbəyi dəstəklənmir.")
            if source == "legacy_environment":
                # The legacy app token is shared and may have write scopes. The
                # AI Agents adapter therefore uses it only for live reads.
                if method.upper() not in {"GET", "HEAD"}:
                    raise self.context.platform_error("Mövcud Kommo bağlantısı AI Agents üçün yalnız oxuma rejimindədir.")
                self._verify_legacy_account(credentials)
            else:
                client_id, client_secret, redirect_uri = self.settings()

                def refresh(active: dict) -> dict:
                    response = requests.post(
                        f"https://{active['account_domain']}/oauth2/access_token",
                        json={"client_id": client_id, "client_secret": client_secret,
                              "grant_type": "refresh_token", "refresh_token": active["refresh_token"],
                              "redirect_uri": redirect_uri}, timeout=15,
                    )
                    if response.status_code != 200:
                        self.context.logger.warning("Kommo OAuth refresh failed: status=%s", response.status_code)
                        raise self.context.platform_error("Kommo bağlantısının vaxtı bitib. Yenidən qoşulun.")
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise self.context.platform_error("Kommo token cavabı düzgün deyil.")
                    self.context.replace_tokens(tenant_id=tenant_id, account_domain=active["account_domain"],
                                                token_payload=payload)
                    return self.context.credentials(tenant_id=tenant_id)

                if token_is_expiring(credentials.get("token_expires_at") or ""):
                    credentials = refresh(credentials)

            def send(active: dict):
                wait_slot(active["account_domain"])
                return requests.request(
                    method.upper(), f"https://{active['account_domain']}/api/v4/{path.lstrip('/')}",
                    params=params, json=json_body,
                    headers={"Authorization": f"Bearer {active['access_token']}", "Accept": "application/json"},
                    timeout=20,
                )

            response = send(credentials)
            if response.status_code == 401 and source == "tenant_oauth":
                credentials = refresh(credentials)
                response = send(credentials)
            if response.status_code >= 400:
                self.context.logger.warning("Tenant Kommo request failed: tenant=%s status=%s path=%s",
                                            tenant_id, response.status_code, path)
                raise self.context.platform_error("Kommo məlumatları yüklənmədi. Bir az sonra yenidən cəhd edin.")
            if response.status_code == 204 or not response.content:
                return {}
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

        return await asyncio.to_thread(run)
