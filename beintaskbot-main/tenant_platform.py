"""PostgreSQL storage for the self-service CRM Smart Assistant platform.

The legacy BeinSystems workspace remains separate from these tables.  Every
new customer record is keyed by ``tenant_id`` so a future CRM module cannot
accidentally expose one company's data to another company.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock

import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet, InvalidToken


class TenantPlatformError(RuntimeError):
    pass


_schema_ready = False
_schema_lock = Lock()
_ROLE_PERMISSIONS = {
    "owner": ["deals", "tasks", "customers", "employees", "integrations", "finance", "settings"],
    "manager": ["deals", "tasks", "customers"],
    "worker": ["tasks", "hot_orders"],
}
_DEFAULT_MODULES = {"deals": True, "tasks": True, "customers": True, "hot_orders": False, "finance": False}
_DEFAULT_NOTIFICATIONS = {"new_lead": True, "incoming_message": True, "task_assigned": True, "task_overdue": True}


def _database_url() -> str:
    value = str(os.environ.get("DATABASE_URL") or "").strip()
    if not value:
        raise TenantPlatformError("Platforma bazası qoşulmayıb.")
    return value


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _ensure_schema(conn) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenants (
                    id UUID PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    industry TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'onboarding',
                    owner_telegram_id BIGINT NOT NULL,
                    modules JSONB NOT NULL DEFAULT '{}'::jsonb,
                    notification_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
                    onboarding JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_members (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    telegram_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK (role IN ('owner', 'manager', 'worker')),
                    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    invited_by BIGINT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, telegram_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_invites (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK (role IN ('manager', 'worker')),
                    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_by BIGINT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    accepted_by BIGINT NULL,
                    accepted_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_integrations (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_connected',
                    account_domain TEXT NOT NULL DEFAULT '',
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    connected_at TIMESTAMPTZ NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, provider)
                )
            """)
            cur.execute("ALTER TABLE saas_tenant_integrations ADD COLUMN IF NOT EXISTS secrets BYTEA NULL")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_oauth_states (
                    id TEXT PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS saas_member_telegram_idx ON saas_tenant_members(telegram_id, active)")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_invite_active_idx ON saas_tenant_invites(token_hash, expires_at) WHERE accepted_at IS NULL")
        conn.commit()
        _schema_ready = True


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fernet() -> Fernet:
    """Return the mandatory at-rest encryption key for OAuth tokens.

    A missing or invalid key is an explicit configuration error; silently
    storing customer credentials unencrypted would be unsafe.
    """
    key = str(os.environ.get("TENANT_ENCRYPTION_KEY") or "").strip()
    if not key:
        raise TenantPlatformError("OAuth təhlükəsizlik açarı quraşdırılmayıb.")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise TenantPlatformError("OAuth təhlükəsizlik açarı düzgün deyil.") from exc


def _kommo_domain(value: str) -> str:
    domain = str(value or "").strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not domain.endswith(".kommo.com") or "/" in domain or " " in domain:
        raise TenantPlatformError("Kommo hesab ünvanını daxil edin. Məsələn: company.kommo.com")
    return domain


def _role(value: str, *, owner: bool = False) -> str:
    if owner:
        return "owner"
    return value if value in {"manager", "worker"} else "worker"


def _permissions(value, role: str) -> list[str]:
    allowed = set(_ROLE_PERMISSIONS.get(role, _ROLE_PERMISSIONS["worker"]))
    if not isinstance(value, list):
        return list(_ROLE_PERMISSIONS.get(role, _ROLE_PERMISSIONS["worker"]))
    requested = [str(item) for item in value if str(item) in allowed]
    return requested or list(_ROLE_PERMISSIONS.get(role, _ROLE_PERMISSIONS["worker"]))


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-")[:42] or "company"
    return f"{base}-{secrets.token_hex(3)}"


def _tenant_payload(row: dict) -> dict:
    result = dict(row)
    result["id"] = str(result["id"])
    for key in ("created_at", "updated_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    for key in ("modules", "notification_rules", "onboarding"):
        if not isinstance(result.get(key), dict):
            result[key] = {}
    return result


def create_tenant(*, name: str, industry: str, owner_telegram_id: int, owner_name: str) -> dict:
    company = str(name or "").strip()[:120]
    if len(company) < 2:
        raise TenantPlatformError("Şirkətin adını yazın.")
    tenant_id = uuid.uuid4()
    onboarding = {"company_name": company, "industry": str(industry or "").strip()[:120], "completed_steps": ["company", "owner"]}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenants (id, slug, name, industry, owner_telegram_id, modules, notification_rules, onboarding)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                RETURNING *
            """, (tenant_id, _slug(company), company, onboarding["industry"], int(owner_telegram_id), _json(_DEFAULT_MODULES), _json(_DEFAULT_NOTIFICATIONS), _json(onboarding)))
            tenant = cur.fetchone()
            cur.execute("""
                INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions)
                VALUES (%s, %s, %s, 'owner', %s::jsonb)
            """, (tenant_id, int(owner_telegram_id), str(owner_name or "").strip()[:120], _json(_ROLE_PERMISSIONS["owner"])))
        conn.commit()
    return _tenant_payload(tenant)


def member(tenant_id: str, telegram_id: int) -> dict | None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT m.*, t.name AS tenant_name, t.slug AS tenant_slug, t.status AS tenant_status,
                       t.modules, t.notification_rules, t.onboarding, t.industry
                FROM saas_tenant_members m
                JOIN saas_tenants t ON t.id = m.tenant_id
                WHERE m.tenant_id = %s::uuid AND m.telegram_id = %s AND m.active = TRUE
            """, (tenant_id, int(telegram_id)))
            row = cur.fetchone()
    if not row:
        return None
    result = dict(row)
    result["tenant_id"] = str(result["tenant_id"])
    # ``member`` is returned directly by JSON API handlers. PostgreSQL gives
    # timestamps as datetime instances, which must be made JSON-safe here.
    for key in ("created_at", "updated_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    for key in ("permissions", "modules", "notification_rules", "onboarding"):
        if not isinstance(result.get(key), (dict, list)):
            result[key] = {} if key != "permissions" else []
    return result


def tenants_for_user(telegram_id: int) -> list[dict]:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.*, m.role, m.permissions, m.display_name
                FROM saas_tenants t JOIN saas_tenant_members m ON m.tenant_id = t.id
                WHERE m.telegram_id = %s AND m.active = TRUE ORDER BY t.created_at DESC
            """, (int(telegram_id),))
            rows = cur.fetchall()
    return [_tenant_payload(row) for row in rows]


def update_onboarding(*, tenant_id: str, owner_id: int, patch: dict) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    safe_patch = {key: value for key, value in patch.items() if key in {"company_name", "industry", "team", "sources", "pipeline", "modules", "notifications", "notes", "completed_steps"}}
    onboarding = dict(current.get("onboarding") or {})
    onboarding.update(safe_patch)
    modules = dict(current.get("modules") or _DEFAULT_MODULES)
    if isinstance(safe_patch.get("modules"), dict):
        modules.update({key: bool(value) for key, value in safe_patch["modules"].items() if key in _DEFAULT_MODULES})
    notifications = dict(current.get("notification_rules") or _DEFAULT_NOTIFICATIONS)
    if isinstance(safe_patch.get("notifications"), dict):
        notifications.update({key: bool(value) for key, value in safe_patch["notifications"].items() if key in _DEFAULT_NOTIFICATIONS})
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenants SET onboarding = %s::jsonb, modules = %s::jsonb,
                    notification_rules = %s::jsonb, updated_at = now()
                WHERE id = %s::uuid RETURNING *
            """, (_json(onboarding), _json(modules), _json(notifications), tenant_id))
            row = cur.fetchone()
        conn.commit()
    return _tenant_payload(row)


def finalize_onboarding(*, tenant_id: str, owner_id: int) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_tenants SET status = 'ready_for_integration', updated_at = now() WHERE id = %s::uuid RETURNING *", (tenant_id,))
            row = cur.fetchone()
        conn.commit()
    return _tenant_payload(row)


def create_invite(*, tenant_id: str, owner_id: int, display_name: str, role: str, permissions: list[str]) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    selected_role = _role(str(role or ""))
    token = secrets.token_urlsafe(24)
    # Store only a one-way digest. The original token appears once in the
    # invite URL and cannot be recovered from the database.
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    invite_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenant_invites (id, tenant_id, token_hash, display_name, role, permissions, created_by, expires_at)
                VALUES (%s, %s::uuid, %s, %s, %s, %s::jsonb, %s, %s)
            """, (invite_id, tenant_id, token_hash, str(display_name or "").strip()[:120], selected_role, _json(_permissions(permissions, selected_role)), int(owner_id), expires))
        conn.commit()
    return {"id": str(invite_id), "token": token, "role": selected_role, "expires_at": expires.isoformat()}


def accept_invite(*, token: str, telegram_id: int, display_name: str) -> dict:
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM saas_tenant_invites
                WHERE token_hash = %s AND accepted_at IS NULL AND expires_at > now()
                FOR UPDATE
            """, (token_hash,))
            invite = cur.fetchone()
            if not invite:
                raise TenantPlatformError("Dəvət etibarsızdır və ya müddəti bitib.")
            cur.execute("""
                INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions, invited_by)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, telegram_id) DO UPDATE SET active = TRUE, display_name = EXCLUDED.display_name,
                    role = EXCLUDED.role, permissions = EXCLUDED.permissions, updated_at = now()
            """, (invite["tenant_id"], int(telegram_id), str(display_name or invite["display_name"] or "").strip()[:120], invite["role"], _json(invite["permissions"]), invite["created_by"]))
            cur.execute("UPDATE saas_tenant_invites SET accepted_by = %s, accepted_at = now() WHERE id = %s", (int(telegram_id), invite["id"]))
        conn.commit()
    return member(str(invite["tenant_id"]), telegram_id) or {}


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


def consume_kommo_oauth_state(state: str) -> str:
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
                SELECT * FROM saas_tenant_oauth_states
                WHERE id = %s AND provider = 'kommo' AND used_at IS NULL AND expires_at > now()
                FOR UPDATE
            """, (state_id,))
            row = cur.fetchone()
            expected = str((row or {}).get("nonce_hash") or "")
            actual = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            if not row or not secrets.compare_digest(expected, actual):
                raise TenantPlatformError("Kommo quraşdırma keçidi etibarsızdır və ya müddəti bitib.")
            cur.execute("UPDATE saas_tenant_oauth_states SET used_at = now() WHERE id = %s", (state_id,))
        conn.commit()
    return str(row["tenant_id"])


def save_kommo_oauth_tokens(*, tenant_id: str, account_domain: str, token_payload: dict) -> dict:
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
                    secrets = %s, connected_at = now(), updated_at = now()
                WHERE tenant_id = %s::uuid AND provider = 'kommo'
                RETURNING provider, status, account_domain, metadata, connected_at, updated_at
            """, (domain, _json(metadata), encrypted, tenant_id))
            row = cur.fetchone()
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
    )
