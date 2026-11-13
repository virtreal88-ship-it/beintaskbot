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

from tenant_shared_integration_access import operation, admitted, project_status
from tenant_scoped_access import scoped_access_enabled
from tenant_scoped_access_store import read_access_profile
from tenant_linear_policy import TenantLinearError

def list_integrations(*, tenant_id: str, telegram_id: int) -> list[dict]:
    if not member(tenant_id, telegram_id):
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT provider, status, account_domain, metadata, connected_at, updated_at FROM saas_tenant_integrations WHERE tenant_id = %s::uuid ORDER BY provider", (tenant_id,))
            rows = cur.fetchall()
    result = []
    from tenant_beinsystems_operator_access import is_operator_workspace
    for row in rows:
        item = dict(row)
        if item.get("provider") == "beinsystems_operator" and not is_operator_workspace(tenant_id):
            continue
        if item.get("provider") == "beinsystems_operator":
            item = {
                "provider": "beinsystems_operator",
                "status": str(item.get("status") or "not_connected"),
                "account_domain": "",
                "metadata": {},
                "connected_at": item.get("connected_at"),
                "updated_at": item.get("updated_at"),
            }
        for key in ("connected_at", "updated_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        if not isinstance(item.get("metadata"), dict):
            item["metadata"] = {}
        result.append(item)
    if is_operator_workspace(tenant_id) and not any(
            row.get("provider") == "beinsystems_operator" for row in result):
        result.append({
            "provider": "beinsystems_operator", "status": "not_connected",
            "account_domain": "", "metadata": {}, "connected_at": None, "updated_at": None,
        })

    # Keep the private manual credential separate from the public OAuth row.
    # Expose one Kommo connection, preferring the official integration whenever
    # it is active, so existing OAuth clients keep their established behavior.
    oauth = next((item for item in result if item.get("provider") == "kommo"), None)
    manual = next((item for item in result if item.get("provider") == "kommo_manual"), None)
    result = [item for item in result if item.get("provider") not in {"kommo", "kommo_manual"}]
    selected = oauth if oauth and oauth.get("status") == "connected" else (
        manual if manual and manual.get("status") == "connected" else oauth or manual)
    if selected:
        selected = dict(selected)
        selected["provider"] = "kommo"
        if (manual and selected.get("status") == "connected"
                and selected.get("metadata", {}).get("auth_mode") == "manual"):
            selected["metadata"] = {**selected.get("metadata", {}), "auth_mode": "manual"}
        result.append(selected)
    if scoped_access_enabled():
        with _connect() as conn:
            with conn.cursor() as cur:
                current = read_access_profile(cur, tenant_id, telegram_id, lock=True)
                if not current:
                    raise TenantLinearError('integration_access_denied', 403)
                return project_status(cur, current, result)
    return sorted(result, key=lambda item: str(item.get("provider") or ""))

@operation(lambda v: str(v['provider'] or '').strip().casefold(), 'delete', data_arg='authorization')
def disconnect_integration(*, tenant_id: str, owner_id: int, provider: str, authorization=None) -> dict:
    """Revoke the local connection state without exposing stored credentials."""
    current = member(tenant_id, owner_id)
    if not admitted({"tenant_id":tenant_id,"telegram_id":owner_id}, str(provider or "").strip().casefold()) and (not current or current.get("role") != "owner"):
        raise TenantPlatformError("İcazə yoxdur.")
    provider = str(provider or "").strip().casefold()
    if provider == "beinsystems_operator":
        from tenant_beinsystems_operator_access import is_operator_workspace
        if not is_operator_workspace(tenant_id):
            raise TenantPlatformError("Bu inteqrasiya idarə edilmir.")
    if provider not in {"kommo", "kommo_manual", "beinsystems", "beinsystems_operator", "waba", "telegram", "push"}:
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
            if provider == "kommo":
                cur.execute("""
                    UPDATE saas_tenant_integrations
                    SET status = 'not_connected', account_domain = '', metadata = '{}'::jsonb,
                        secrets = NULL, connected_at = NULL, updated_at = now()
                    WHERE tenant_id = %s::uuid AND provider = 'kommo_manual'
                """, (tenant_id,))
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


def validate_kommo_manual_storage() -> None:
    """Fail before sending a customer token to Kommo when encryption is unavailable."""
    _fernet()


@operation('kommo_manual', 'connect', data_arg='authorization')
def save_kommo_manual_token(*, tenant_id: str, owner_id: int, account_domain: str,
                            access_token: str, authorization=None) -> dict:
    """Store an owner-provided Kommo long-lived token in a separate tenant row."""
    current = member(tenant_id, owner_id)
    if not admitted({"tenant_id":tenant_id,"telegram_id":owner_id}, "kommo_manual") and (not current or current.get("role") != "owner"):
        raise TenantPlatformError("İcazə yoxdur.")
    domain = _kommo_domain(account_domain)
    if not isinstance(access_token, str):
        raise TenantPlatformError("Kommo tokenini yoxlayın.")
    token = access_token.strip()
    if not token or len(token) > 4096 or any(ord(char) < 32 for char in token):
        raise TenantPlatformError("Kommo tokenini yoxlayın.")
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant_id, "provider": "kommo_manual", "access_token": token,
    }).encode("utf-8"))
    metadata = {"auth_mode": "manual"}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status FROM saas_tenant_integrations
                WHERE tenant_id = %s::uuid AND provider = 'kommo' FOR UPDATE
            """, (tenant_id,))
            oauth = cur.fetchone()
            if oauth and oauth.get("status") == "connected":
                raise TenantPlatformError("Kommo-nun rəsmi bağlantısı artıq aktivdir.")
            cur.execute("""
                INSERT INTO saas_tenant_integrations
                    (tenant_id, provider, status, account_domain, metadata, secrets, connected_at)
                VALUES (%s::uuid, 'kommo_manual', 'connected', %s, %s::jsonb, %s, now())
                ON CONFLICT (tenant_id, provider) DO UPDATE SET
                    status = 'connected', account_domain = EXCLUDED.account_domain,
                    metadata = EXCLUDED.metadata, secrets = EXCLUDED.secrets,
                    connected_at = now(), updated_at = now()
                RETURNING provider, status, account_domain, metadata, connected_at, updated_at
            """, (tenant_id, domain, _json(metadata), encrypted))
            row = cur.fetchone()
            # Match OAuth setup behavior without reviving any disabled tenant.
            cur.execute("""UPDATE saas_tenants SET status='active', updated_at=now()
                WHERE id=%s::uuid AND status='ready_for_integration'""", (tenant_id,))
        conn.commit()
    item = dict(row)
    for key in ("connected_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
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
                SELECT provider, account_domain, status, metadata, secrets, connected_at
                FROM saas_tenant_integrations
                WHERE tenant_id = %s::uuid AND provider IN ('kommo', 'kommo_manual')
            """, (tenant_id,))
            rows = cur.fetchall()
    integrations = {str(row.get("provider") or ""): row for row in rows}
    oauth = integrations.get("kommo")
    manual = integrations.get("kommo_manual")
    if oauth and oauth.get("status") == "connected":
        row, mode = oauth, "oauth"
    elif manual and manual.get("status") == "connected":
        row, mode = manual, "manual"
    else:
        raise TenantPlatformError("Kommo hələ qoşulmayıb.")
    if not row.get("secrets"):
        raise TenantPlatformError("Kommo bağlantı məlumatı oxunmadı. Yenidən qoşulun.")
    try:
        raw = _fernet().decrypt(bytes(row["secrets"]))
        tokens = json.loads(raw.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TenantPlatformError("Kommo bağlantı məlumatı oxunmadı. Yenidən qoşulun.") from exc
    if not isinstance(tokens, dict):
        raise TenantPlatformError("Kommo bağlantı məlumatı tam deyil. Yenidən qoşulun.")
    # Source metadata comes from the same row snapshot as these tokens. It is
    # server-only read evidence, not a new credential or connection setting.
    if mode == "manual":
        if tokens.get("tenant_id") != tenant_id or tokens.get("provider") != "kommo_manual" or not tokens.get("access_token"):
            raise TenantPlatformError("Kommo bağlantı məlumatı tam deyil. Yenidən qoşulun.")
        return {
            "account_domain": str(row["account_domain"]),
            "provider": row["provider"], "connected_at": row.get("connected_at"),
            "access_token": str(tokens["access_token"]),
            "refresh_token": "",
            "token_expires_at": "",
            "auth_mode": "manual",
        }
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise TenantPlatformError("Kommo bağlantı məlumatı tam deyil. Yenidən qoşulun.")
    metadata = row["metadata"] if isinstance(row.get("metadata"), dict) else {}
    return {
        "account_domain": str(row["account_domain"]),
        "provider": row["provider"], "connected_at": row.get("connected_at"),
        "access_token": str(tokens["access_token"]),
        "refresh_token": str(tokens["refresh_token"]),
        "token_expires_at": str(metadata.get("token_expires_at") or ""),
        "auth_mode": "oauth",
    }

def replace_kommo_tokens(*, tenant_id: str, account_domain: str, token_payload: dict) -> dict:
    """Save a refreshed token pair using the same encrypted storage path."""
    return save_kommo_oauth_tokens(
        tenant_id=tenant_id,
        account_domain=account_domain,
        token_payload=token_payload,
        refresh_only=True,
    )
