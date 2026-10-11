"""Tenant integration state and encrypted Kommo OAuth persistence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets

from cryptography.fernet import InvalidToken

from tenant_member_store import member
from tenant_platform_core import (
    TenantPlatformError, _connect, _ensure_schema, _fernet, _json, _kommo_domain,
)

def list_integrations(*, tenant_id: str, telegram_id: int) -> list[dict]:
    if not member(tenant_id, telegram_id):
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT provider, status, account_domain, metadata, connected_at, updated_at FROM saas_tenant_integrations WHERE tenant_id = %s::uuid ORDER BY provider", (tenant_id,))
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("connected_at", "updated_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        if not isinstance(item.get("metadata"), dict):
            item["metadata"] = {}
        result.append(item)
    return result


def disconnect_integration(*, tenant_id: str, owner_id: int, provider: str) -> dict:
    """Revoke the local connection state without exposing stored credentials."""
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    provider = str(provider or "").strip().casefold()
    if provider not in {"kommo", "waba", "telegram", "push"}:
        raise TenantPlatformError("Bu inteqrasiya idarə edilmir.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenant_integrations
                SET status = 'not_connected', account_domain = '', metadata = '{}'::jsonb,
                    secrets = NULL, connected_at = NULL, updated_at = now()
                WHERE tenant_id = %s::uuid AND provider = %s
                RETURNING provider, status, account_domain, metadata, connected_at, updated_at
            """, (tenant_id, provider))
            row = cur.fetchone()
        conn.commit()
    if not row:
        return {"provider": provider, "status": "not_connected", "account_domain": "", "metadata": {}}
    item = dict(row)
    for key in ("connected_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def request_kommo_connection(*, tenant_id: str, owner_id: int, account_domain: str) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    domain = _kommo_domain(account_domain)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenant_integrations (tenant_id, provider, status, account_domain, metadata)
                VALUES (%s::uuid, 'kommo', 'awaiting_oauth', %s, '{}'::jsonb)
                ON CONFLICT (tenant_id, provider) DO UPDATE SET status = 'awaiting_oauth', account_domain = EXCLUDED.account_domain, updated_at = now()
                RETURNING provider, status, account_domain, updated_at
            """, (tenant_id, domain))
            row = cur.fetchone()
        conn.commit()
    item = dict(row)
    item["updated_at"] = item["updated_at"].isoformat()
    return item


def begin_kommo_oauth(*, tenant_id: str, owner_id: int, account_domain: str) -> str:
    """Create a short-lived, one-use CSRF state for a Kommo authorization."""
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    # The Kommo authorization window itself returns the selected account's
    # domain.  Keeping this optional lets the customer connect in one click
    # instead of typing a technical subdomain first.
    domain = _kommo_domain(account_domain) if str(account_domain or "").strip() else ""
    state_id = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenant_oauth_states (id, tenant_id, provider, nonce_hash, expires_at)
                VALUES (%s, %s::uuid, 'kommo', %s, %s)
            """, (state_id, tenant_id, hashlib.sha256(nonce.encode("utf-8")).hexdigest(), expires))
            cur.execute("""
                INSERT INTO saas_tenant_integrations (tenant_id, provider, status, account_domain, metadata)
                VALUES (%s::uuid, 'kommo', 'awaiting_oauth', %s, '{}'::jsonb)
                ON CONFLICT (tenant_id, provider) DO UPDATE SET status = 'awaiting_oauth', account_domain = EXCLUDED.account_domain, updated_at = now()
            """, (tenant_id, domain))
        conn.commit()
    return f"{state_id}.{nonce}"


def consume_kommo_oauth_state(state: str) -> dict:
    """Validate and consume a state before exchanging Kommo's one-time code."""
    try:
        state_id, nonce = str(state or "").split(".", 1)
    except ValueError as exc:
        raise TenantPlatformError("Kommo quraşdırma keçidi etibarsızdır.") from exc
    if not state_id or not nonce:
        raise TenantPlatformError("Kommo quraşdırma keçidi etibarsızdır.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.*, i.account_domain
                FROM saas_tenant_oauth_states s
                LEFT JOIN saas_tenant_integrations i
                  ON i.tenant_id = s.tenant_id AND i.provider = 'kommo'
                WHERE s.id = %s AND s.provider = 'kommo' AND s.used_at IS NULL AND s.expires_at > now()
                FOR UPDATE OF s
            """, (state_id,))
            row = cur.fetchone()
            expected = str((row or {}).get("nonce_hash") or "")
            actual = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            if not row or not secrets.compare_digest(expected, actual):
                raise TenantPlatformError("Kommo quraşdırma keçidi etibarsızdır və ya müddəti bitib.")
            cur.execute("UPDATE saas_tenant_oauth_states SET used_at = now() WHERE id = %s", (state_id,))
        conn.commit()
    # The OAuth callback does not reliably include an HTTP Referer. Return the
    # domain saved when the owner clicked “Kommo ilə qoşul”, so a tenant can
    # complete the public OAuth flow without typing its technical subdomain
    # again.
    return {
        "tenant_id": str(row["tenant_id"]),
        "account_domain": str(row.get("account_domain") or ""),
    }


def save_kommo_oauth_tokens(*, tenant_id: str, account_domain: str, token_payload: dict,
                          refresh_only: bool = False) -> dict:
    """Encrypt Kommo credentials before persisting them for this tenant only."""
    domain = _kommo_domain(account_domain)
    access_token = str(token_payload.get("access_token") or "")
    refresh_token = str(token_payload.get("refresh_token") or "")
    if not access_token or not refresh_token:
        raise TenantPlatformError("Kommo token cavabı tam deyil.")
    try:
        expires_in = int(token_payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    token_data = {"access_token": access_token, "refresh_token": refresh_token}
    encrypted = _fernet().encrypt(_json(token_data).encode("utf-8"))
    metadata = {"token_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 0))).isoformat()}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenant_integrations
                SET status = 'connected', account_domain = %s, metadata = %s::jsonb,
                    secrets = %s, connected_at = CASE WHEN %s THEN connected_at ELSE now() END, updated_at = now()
                WHERE tenant_id = %s::uuid AND provider = 'kommo'
                  AND (NOT %s OR (status = 'connected' AND account_domain = %s))
                RETURNING provider, status, account_domain, metadata, connected_at, updated_at
            """, (domain, _json(metadata), encrypted, refresh_only, tenant_id, refresh_only, domain))
            row = cur.fetchone()
            if row:
                # Setup may be completed before OAuth. Activate only that
                # completed workspace; never revive a disabled company.
                cur.execute("""UPDATE saas_tenants SET status='active', updated_at=now()
                    WHERE id=%s::uuid AND status='ready_for_integration'""", (tenant_id,))
        conn.commit()
    if not row:
        raise TenantPlatformError("Kommo bağlantısı üçün quraşdırma tapılmadı.")
    item = dict(row)
    for key in ("connected_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def kommo_credentials(*, tenant_id: str) -> dict:
    """Read credentials only for server-side requests, never for a browser."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT account_domain, status, metadata, secrets
                FROM saas_tenant_integrations
                WHERE tenant_id = %s::uuid AND provider = 'kommo'
            """, (tenant_id,))
            row = cur.fetchone()
    if not row or row.get("status") != "connected" or not row.get("secrets"):
        raise TenantPlatformError("Kommo hələ qoşulmayıb.")
    try:
        raw = _fernet().decrypt(bytes(row["secrets"]))
        tokens = json.loads(raw.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TenantPlatformError("Kommo bağlantı məlumatı oxunmadı. Yenidən qoşulun.") from exc
    if not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise TenantPlatformError("Kommo bağlantı məlumatı tam deyil. Yenidən qoşulun.")
    metadata = row["metadata"] if isinstance(row.get("metadata"), dict) else {}
    return {
        "account_domain": str(row["account_domain"]),
        "access_token": str(tokens["access_token"]),
        "refresh_token": str(tokens["refresh_token"]),
        "token_expires_at": str(metadata.get("token_expires_at") or ""),
    }


def replace_kommo_tokens(*, tenant_id: str, account_domain: str, token_payload: dict) -> dict:
    """Save a refreshed token pair using the same encrypted storage path."""
    return save_kommo_oauth_tokens(
        tenant_id=tenant_id,
        account_domain=account_domain,
        token_payload=token_payload,
        refresh_only=True,
    )
