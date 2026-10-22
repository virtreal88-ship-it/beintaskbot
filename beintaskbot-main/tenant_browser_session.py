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
    create_session: Callable[..., object] | None = None
    session_active: Callable[..., bool] | None = None

    def _signature(self, payload: str) -> str:
        key = hashlib.sha256(f"tenant-session:{self.secret()}".encode("utf-8")).digest()
        return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, tenant_id: str, chat_id: int) -> str:
        """Issue a signed cookie; new production cookies are revocable by session ID."""
        expires_at = int(self.clock()) + TTL_SECONDS
        payload = f"{str(tenant_id)}.{int(chat_id)}.{expires_at}"
        if self.create_session:
            sid = str(uuid.uuid4())
            if int(chat_id) > 0:
                self.create_session(session_id=sid, tenant_id=str(tenant_id), actor_id=int(chat_id), expires_at=expires_at)
            payload = f"{payload}.{sid}"
        return f"{payload}.{self._signature(payload)}"

    def identity(self, request: CookieRequest) -> tuple[str, int] | None:
        raw = str(request.cookies.get(COOKIE_NAME) or "")
        try:
            parts = raw.rsplit(".", 4)
            if len(parts) == 5:
                tenant_id, chat_raw, expires_raw, sid, signature = parts
                payload = f"{tenant_id}.{chat_raw}.{expires_raw}.{sid}"
                uuid.UUID(sid)
            else:
                tenant_id, chat_raw, expires_raw, signature = raw.rsplit(".", 3)
                sid = ""
                payload = f"{tenant_id}.{chat_raw}.{expires_raw}"
            uuid.UUID(tenant_id)
            if not hmac.compare_digest(self._signature(payload), signature):
                return None
            if int(expires_raw) < int(self.clock()):
                return None
            chat_id = int(chat_raw)
            if chat_id <= 0 and not self.identity_allowed(tenant_id, chat_id):
                return None
            if sid and chat_id > 0 and self.session_active and not self.session_active(session_id=sid, tenant_id=tenant_id, actor_id=chat_id):
                return None
            return (tenant_id, chat_id) if chat_id else None
        except (TypeError, ValueError):
            return None

    def session_id(self, request: CookieRequest) -> str | None:
        identity = self.identity(request)
        if not identity: return None
        try:
            parts = str(request.cookies.get(COOKIE_NAME) or "").rsplit(".", 4)
            return str(uuid.UUID(parts[3])) if len(parts) == 5 else None
        except (TypeError, ValueError, IndexError):
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
