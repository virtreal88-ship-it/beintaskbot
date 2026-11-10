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
    legacy_session_allowed: Callable[..., bool] | None = None

    def _signature(self, payload: str) -> str:
        key = hashlib.sha256(f"tenant-session:{self.secret()}".encode("utf-8")).digest()
        return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, tenant_id: str, chat_id: int, *, credential_fingerprint: str | None = None,
              source_session: dict | None = None, revoke_source: bool = False) -> str:
        """Issue a signed cookie; new production cookies are revocable by session ID."""
        expires_at = int(self.clock()) + TTL_SECONDS
        payload = f"{str(tenant_id)}.{int(chat_id)}.{expires_at}"
        if (credential_fingerprint is not None or source_session is not None or revoke_source) and (
                not self.create_session or int(chat_id) <= 0):
            raise self.membership_error('Giriş məlumatları dəyişib. Yenidən daxil olun.')
        if self.create_session:
            sid = str(uuid.uuid4())
            if int(chat_id) > 0:
                credentials = ({'credential_fingerprint': credential_fingerprint}
                               if credential_fingerprint is not None else {})
                if source_session is not None:
                    credentials['source_session'] = source_session
                if revoke_source:
                    credentials['revoke_source'] = True
                self.create_session(session_id=sid, tenant_id=str(tenant_id), actor_id=int(chat_id),
                                    expires_at=expires_at, **credentials)
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
            # Both cookie formats check current global account eligibility.
            # Legacy cookies additionally respect the actor-specific recovery
            # fence; Telegram-only identities retain their existing access.
            if chat_id > 0 and not sid and self.legacy_session_allowed:
                try:
                    if not self.legacy_session_allowed(actor_id=chat_id):
                        return None
                except Exception:
                    return None
            if chat_id <= 0 and not self.identity_allowed(tenant_id, chat_id):
                return None
            if sid and chat_id > 0 and self.session_active:
                try:
                    if not self.session_active(session_id=sid, tenant_id=tenant_id, actor_id=chat_id):
                        return None
                except Exception:
                    # A failed status/revocation lookup cannot fall back to a
                    # signed-cookie identity or expose backend error details.
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

    def source(self, request: CookieRequest) -> dict | None:
        """An internal proof for atomically rechecking session-based actions."""
        identity = self.identity(request)
        if not identity or identity[1] <= 0:
            return None
        parts = str(request.cookies.get(COOKIE_NAME) or '').rsplit('.', 4)
        return {'tenant_id': identity[0], 'actor_id': identity[1],
                'session_id': str(uuid.UUID(parts[3])) if len(parts) == 5 else ''}

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
