"""Tenant cookie authentication; current membership is never cached here."""
import hashlib
import hmac
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

COOKIE_NAME = "csa_tenant_session"
TTL_SECONDS = 8 * 60 * 60


class CookieRequest(Protocol):
    cookies: Mapping[str, str]


@dataclass(frozen=True)
class TenantBrowserSession:
    secret: Callable[[], str]
    member: Callable[[str, int], dict | None]
    identity_allowed: Callable[[str, int], bool]
    membership_error: type[Exception]
    clock: Callable[[], float] = time.time

    def _signature(self, payload: str) -> str:
        key = hashlib.sha256(f"tenant-session:{self.secret()}".encode("utf-8")).digest()
        return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, tenant_id: str, chat_id: int) -> str:
        """Preserve the deployed cookie wire format and signing key derivation."""
        expires_at = int(self.clock()) + TTL_SECONDS
        payload = f"{str(tenant_id)}.{int(chat_id)}.{expires_at}"
        return f"{payload}.{self._signature(payload)}"

    def identity(self, request: CookieRequest) -> tuple[str, int] | None:
        raw = str(request.cookies.get(COOKIE_NAME) or "")
        try:
            tenant_id, chat_raw, expires_raw, signature = raw.rsplit(".", 3)
            uuid.UUID(tenant_id)
            payload = f"{tenant_id}.{chat_raw}.{expires_raw}"
            if not hmac.compare_digest(self._signature(payload), signature):
                return None
            if int(expires_raw) < int(self.clock()):
                return None
            chat_id = int(chat_raw)
            if chat_id <= 0 and not self.identity_allowed(tenant_id, chat_id):
                return None
            return (tenant_id, chat_id) if chat_id else None
        except (TypeError, ValueError):
            return None

    def profile(self, request: CookieRequest, *, owner_only: bool = False) -> dict | None:
        identity = self.identity(request)
        if not identity:
            return None
        try:
            profile = self.member(*identity)
        except self.membership_error:
            return None
        if not profile or (owner_only and profile.get("role") != "owner"):
            return None
        return profile
