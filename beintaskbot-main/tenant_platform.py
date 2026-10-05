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
from urllib.parse import unquote
from datetime import datetime, timedelta, timezone
from threading import Lock

import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet, InvalidToken


class TenantPlatformError(RuntimeError):
    pass


def _invite_token(value: object) -> str:
    """Normalize a token copied from Telegram/WhatsApp/browser URL.

    Mobile clients sometimes URL-decode the path or append punctuation when a
    link is copied from a message.  The stored digest is calculated from the
    original URL-safe token, so normalizing here prevents a valid invite from
    being rejected just because of that transport formatting.
    """
    token = unquote(str(value or "")).strip()
    token = token.rstrip(".,;:!?)]}>\"'")
    return token


_schema_ready = False
_schema_lock = Lock()
_ROLE_PERMISSIONS = {
    "owner": ["deals", "tasks", "customers", "employees", "integrations", "finance", "settings", "hot_orders", "linear"],
    "manager": ["deals", "tasks", "customers"],
    "worker": ["tasks", "hot_orders"],
    "master": ["hot_orders"],
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
                    role TEXT NOT NULL CHECK (role IN ('owner', 'manager', 'worker', 'master')),
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
                    role TEXT NOT NULL CHECK (role IN ('manager', 'worker', 'master')),
                    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_by BIGINT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    accepted_by BIGINT NULL,
                    accepted_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # Existing SaaS databases were created before the master role was
            # introduced. Replace only the generated role constraints; data is
            # preserved and the migration is safe to run on every startup.
            cur.execute("ALTER TABLE saas_tenant_members DROP CONSTRAINT IF EXISTS saas_tenant_members_role_check")
            cur.execute("ALTER TABLE saas_tenant_members ADD CONSTRAINT saas_tenant_members_role_check CHECK (role IN ('owner', 'manager', 'worker', 'master'))")
            cur.execute("ALTER TABLE saas_tenant_invites DROP CONSTRAINT IF EXISTS saas_tenant_invites_role_check")
            cur.execute("ALTER TABLE saas_tenant_invites ADD CONSTRAINT saas_tenant_invites_role_check CHECK (role IN ('manager', 'worker', 'master'))")
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_invite_requests (
                    id UUID PRIMARY KEY,
                    invite_id UUID NOT NULL REFERENCES saas_tenant_invites(id) ON DELETE CASCADE,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    telegram_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    decided_by BIGINT NULL,
                    decided_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (invite_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS saas_invite_request_tenant_idx ON saas_tenant_invite_requests(tenant_id, status, created_at DESC)")
            # CRM records for self-service companies are deliberately kept in
            # separate tables from the original BeinSystems workspace.  The
            # tenant id is part of every key, so an accidental query without
            # an application filter cannot join two companies' data.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_crm_deals (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    kommo_lead_id BIGINT NOT NULL,
                    pipeline_id BIGINT NOT NULL DEFAULT 0,
                    status_id BIGINT NOT NULL DEFAULT 0,
                    stage_name TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    contact_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    last_message TEXT NOT NULL DEFAULT '',
                    last_message_at TIMESTAMPTZ NULL,
                    source_updated_at TIMESTAMPTZ NULL,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, kommo_lead_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_crm_tasks (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    kommo_task_id BIGINT NOT NULL,
                    kommo_lead_id BIGINT NOT NULL DEFAULT 0,
                    text TEXT NOT NULL DEFAULT '',
                    due_at TIMESTAMPTZ NULL,
                    responsible_id BIGINT NOT NULL DEFAULT 0,
                    completed BOOLEAN NOT NULL DEFAULT FALSE,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, kommo_task_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_crm_messages (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    kommo_lead_id BIGINT NOT NULL,
                    external_id TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'incoming',
                    channel TEXT NOT NULL DEFAULT '',
                    author_name TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT 'text',
                    media_url TEXT NOT NULL DEFAULT '',
                    happened_at TIMESTAMPTZ NULL,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (tenant_id, external_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_notifications (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    telegram_id BIGINT NOT NULL,
                    event_key TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    target_url TEXT NOT NULL DEFAULT '',
                    read_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_audit_events (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    actor_telegram_id BIGINT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL DEFAULT '',
                    entity_id TEXT NOT NULL DEFAULT '',
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crm_linear_accounts (
                    id UUID PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # Public product news is a durable snapshot of selected Linear
            # issues.  Linear issues may later be deleted or moved out of
            # Done, so the site keeps its own copy for a short, predictable
            # retention window.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crm_linear_news (
                    id UUID PRIMARY KEY,
                    source_issue_id TEXT NOT NULL UNIQUE,
                    identifier TEXT NOT NULL DEFAULT '',
                    project_key TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    account TEXT NOT NULL DEFAULT '',
                    environment TEXT NOT NULL DEFAULT '',
                    source_created_at TIMESTAMPTZ NULL,
                    done_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    telegram_message_id BIGINT NULL,
                    telegram_published_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # Keep existing installations compatible with the Telegram news
            # publisher without requiring a destructive migration.
            cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT NULL")
            cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS telegram_published_at TIMESTAMPTZ NULL")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_webhook_events (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, fingerprint)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crm_linear_news_migrations (
                    migration_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_deals_list_idx ON saas_crm_deals(tenant_id, pipeline_id, status_id, last_message_at DESC NULLS LAST, synced_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_tasks_list_idx ON saas_crm_tasks(tenant_id, responsible_id, completed, due_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_messages_list_idx ON saas_crm_messages(tenant_id, kommo_lead_id, happened_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_notifications_list_idx ON saas_tenant_notifications(tenant_id, telegram_id, read_at, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS crm_linear_news_project_idx ON crm_linear_news(project_key, done_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS crm_linear_news_expiry_idx ON crm_linear_news(expires_at)")
        conn.commit()
        _schema_ready = True


def list_linear_accounts() -> list[dict]:
    """Return the shared account dictionary used by Linear task creation."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, created_at, updated_at
                FROM crm_linear_accounts
                WHERE active = TRUE
                ORDER BY name_key ASC
            """)
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        for key in ("created_at", "updated_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        result.append(item)
    return result


def create_linear_account(*, name: str, created_by: int) -> dict:
    """Add an account to the shared dictionary, returning an existing match."""
    clean_name = re.sub(r"\s+", " ", str(name or "").strip())[:160]
    if not clean_name:
        raise TenantPlatformError("Hesab adını yazın.")
    name_key = clean_name.casefold()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_accounts (id, name, name_key, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name_key) DO UPDATE SET active = TRUE, updated_at = now()
                RETURNING id, name, created_at, updated_at
            """, (uuid.uuid4(), clean_name, name_key, int(created_by)))
            row = cur.fetchone()
        conn.commit()
    item = dict(row)
    item["id"] = str(item["id"])
    for key in ("created_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def upsert_linear_news(*, source_issue_id: str, identifier: str, project_key: str,
                       title: str, summary: str, body: str, account: str,
                       environment: str, source_created_at: str | None,
                       done_at: str | None, source_url: str, raw: dict | None = None) -> dict:
    """Persist one customer-facing Linear release note for 90 days."""
    issue_id = str(source_issue_id or "").strip()
    project = str(project_key or "").strip().upper()
    if not issue_id or project not in {"AKUL", "DINE"}:
        raise TenantPlatformError("Linear xəbəri üçün mənbə və layihə tələb olunur.")
    expires_at = datetime.now(timezone.utc) + timedelta(days=90)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news
                (id, source_issue_id, identifier, project_key, title, summary, body,
                 account, environment, source_created_at, done_at, expires_at,
                 source_url, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::timestamptz, COALESCE(%s::timestamptz, now()), %s,
                        %s, %s::jsonb)
                ON CONFLICT (source_issue_id) DO UPDATE SET
                    identifier = EXCLUDED.identifier,
                    project_key = EXCLUDED.project_key,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    body = EXCLUDED.body,
                    account = EXCLUDED.account,
                    environment = EXCLUDED.environment,
                    source_created_at = EXCLUDED.source_created_at,
                    source_url = EXCLUDED.source_url,
                    raw = EXCLUDED.raw,
                    updated_at = now()
                RETURNING id, source_issue_id, identifier, project_key, title,
                          summary, body, account, environment, source_created_at,
                          done_at, expires_at, source_url, created_at, updated_at
            """, (
                uuid.uuid4(), issue_id, str(identifier or ""), project,
                str(title or "")[:500], str(summary or "")[:1200], str(body or "")[:12000],
                str(account or "")[:255], str(environment or "")[:255],
                source_created_at or None, done_at or None, expires_at,
                str(source_url or "")[:1000], _json(raw or {}),
            ))
            row = cur.fetchone()
        conn.commit()
    item = dict(row)
    for key in ("id",):
        if item.get(key):
            item[key] = str(item[key])
    for key in ("source_created_at", "done_at", "expires_at", "telegram_published_at", "created_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def list_linear_news(*, project_key: str = "", limit: int = 60) -> list[dict]:
    """Return non-expired release notes, newest first."""
    project = str(project_key or "").strip().upper()
    try:
        size = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        size = 60
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if project in {"AKUL", "DINE"}:
                cur.execute("""
                    SELECT * FROM crm_linear_news
                    WHERE project_key = %s AND expires_at > now()
                    ORDER BY done_at DESC, created_at DESC LIMIT %s
                """, (project, size))
            else:
                cur.execute("""
                    SELECT * FROM crm_linear_news
                    WHERE expires_at > now()
                    ORDER BY done_at DESC, created_at DESC LIMIT %s
                """, (size,))
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item.get("id") or "")
        if not isinstance(item.get("raw"), dict):
            item["raw"] = {}
        for key in ("source_created_at", "done_at", "expires_at", "created_at", "updated_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        result.append(item)
    return result


def mark_linear_news_published(*, source_issue_id: str, telegram_message_id: int | None = None) -> None:
    """Record the channel message so each release is published only once."""
    issue_id = str(source_issue_id or "").strip()
    if not issue_id:
        return
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm_linear_news
                SET telegram_message_id = %s,
                    telegram_published_at = now(),
                    updated_at = now()
                WHERE source_issue_id = %s
            """, (telegram_message_id, issue_id))
        conn.commit()


def delete_linear_news(*, source_issue_id: str) -> bool:
    """Remove one archived release note by its source Linear issue id."""
    issue_id = str(source_issue_id or "").strip()
    if not issue_id:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crm_linear_news WHERE source_issue_id = %s", (issue_id,))
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)


def reset_linear_news_publishing_once(*, migration_key: str) -> bool:
    """Clear channel delivery marks once for an intentional feed reseed."""
    key = str(migration_key or "").strip()
    if not key:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES (%s)
                ON CONFLICT (migration_key) DO NOTHING
                RETURNING migration_key
            """, (key,))
            applied = bool(cur.fetchone())
            if applied:
                cur.execute("""
                    UPDATE crm_linear_news
                    SET telegram_message_id = NULL,
                        telegram_published_at = NULL,
                        updated_at = now()
                """)
        conn.commit()
    return applied


def claim_linear_news_publish_slot(*, slot_key: str) -> bool:
    """Atomically reserve one scheduled Telegram publication slot."""
    key = str(slot_key or "").strip()
    if not key:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES (%s)
                ON CONFLICT (migration_key) DO NOTHING
                RETURNING migration_key
            """, (key,))
            claimed = bool(cur.fetchone())
        conn.commit()
    return claimed


def prune_linear_news() -> int:
    """Delete release notes after their 90-day public retention window."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crm_linear_news WHERE expires_at <= now()")
            deleted = cur.rowcount
        conn.commit()
    return int(deleted or 0)


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
    return value if value in {"manager", "worker", "master"} else "worker"


def _permissions(value, role: str) -> list[str]:
    # Roles provide sensible defaults. The owner may still grant a smaller
    # custom set of ordinary workspace modules to a manager or worker.
    if role == "owner":
        return list(_ROLE_PERMISSIONS["owner"])
    allowed = {permission for values in _ROLE_PERMISSIONS.values() for permission in values}
    if not isinstance(value, list):
        return list(_ROLE_PERMISSIONS.get(role, _ROLE_PERMISSIONS["worker"]))
    requested = [str(item) for item in value if str(item) in allowed]
    if role == "master":
        requested = [item for item in requested if item == "hot_orders"]
        return requested or list(_ROLE_PERMISSIONS["master"])
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


def _public_crm_row(row: dict) -> dict:
    """Turn a PostgreSQL CRM snapshot into a JSON-safe browser payload."""
    result = dict(row)
    for key in ("last_message_at", "source_updated_at", "synced_at", "due_at", "happened_at", "created_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if not isinstance(result.get("raw"), dict):
        result["raw"] = {}
    return result


def upsert_crm_deals(*, tenant_id: str, deals: list[dict]) -> int:
    """Persist a tenant's Kommo deal snapshot without touching legacy data."""
    saved = 0
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            for item in deals:
                try:
                    lead_id = int(item.get("kommo_lead_id") or item.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if lead_id <= 0:
                    continue
                cur.execute("""
                    INSERT INTO saas_crm_deals
                    (tenant_id, kommo_lead_id, pipeline_id, status_id, stage_name, name, contact_name, phone,
                     channel, last_message, last_message_at, source_updated_at, raw, synced_at)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::timestamptz, %s::timestamptz, %s::jsonb, now())
                    ON CONFLICT (tenant_id, kommo_lead_id) DO UPDATE SET
                        pipeline_id = EXCLUDED.pipeline_id, status_id = EXCLUDED.status_id,
                        stage_name = EXCLUDED.stage_name, name = EXCLUDED.name,
                        contact_name = EXCLUDED.contact_name, phone = EXCLUDED.phone,
                        channel = EXCLUDED.channel, last_message = EXCLUDED.last_message,
                        last_message_at = EXCLUDED.last_message_at,
                        source_updated_at = EXCLUDED.source_updated_at, raw = EXCLUDED.raw, synced_at = now()
                """, (
                    tenant_id, lead_id, int(item.get("pipeline_id") or 0), int(item.get("status_id") or 0),
                    str(item.get("stage_name") or "")[:240], str(item.get("name") or "")[:500],
                    str(item.get("contact_name") or "")[:500], str(item.get("phone") or "")[:80],
                    str(item.get("channel") or "")[:100], str(item.get("last_message") or "")[:4000],
                    item.get("last_message_at") or None, item.get("source_updated_at") or None,
                    _json(item.get("raw") if isinstance(item.get("raw"), dict) else {}),
                ))
                saved += 1
        conn.commit()
    return saved


def list_crm_deals(*, tenant_id: str, search: str = "", pipeline_ids: list[int] | None = None,
                   status_ids: list[int] | None = None, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """List only the selected tenant's cached deals, with DB-side filtering."""
    safe_limit = max(1, min(int(limit or 100), 200))
    safe_offset = max(0, int(offset or 0))
    clauses = ["tenant_id = %s::uuid"]
    values: list = [tenant_id]
    needle = str(search or "").strip()[:160]
    if needle:
        clauses.append("(name ILIKE %s OR contact_name ILIKE %s OR phone ILIKE %s OR stage_name ILIKE %s)")
        values.extend([f"%{needle}%"] * 4)
    normalized_pipelines = [int(value) for value in (pipeline_ids or []) if str(value).isdigit()]
    normalized_statuses = [int(value) for value in (status_ids or []) if str(value).isdigit()]
    if normalized_pipelines:
        clauses.append("pipeline_id = ANY(%s)")
        values.append(normalized_pipelines)
    if normalized_statuses:
        clauses.append("status_id = ANY(%s)")
        values.append(normalized_statuses)
    where = " AND ".join(clauses)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS total FROM saas_crm_deals WHERE {where}", values)
            total = int((cur.fetchone() or {}).get("total") or 0)
            cur.execute(
                f"SELECT * FROM saas_crm_deals WHERE {where} "
                "ORDER BY last_message_at DESC NULLS LAST, source_updated_at DESC NULLS LAST, synced_at DESC "
                "LIMIT %s OFFSET %s",
                [*values, safe_limit, safe_offset],
            )
            rows = cur.fetchall()
    return [_public_crm_row(row) for row in rows], total


def get_crm_deal(*, tenant_id: str, kommo_lead_id: int) -> dict | None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM saas_crm_deals WHERE tenant_id = %s::uuid AND kommo_lead_id = %s", (tenant_id, int(kommo_lead_id)))
            row = cur.fetchone()
    return _public_crm_row(row) if row else None


def upsert_crm_tasks(*, tenant_id: str, tasks: list[dict]) -> int:
    saved = 0
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            for item in tasks:
                try:
                    task_id = int(item.get("kommo_task_id") or item.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if task_id <= 0:
                    continue
                cur.execute("""
                    INSERT INTO saas_crm_tasks
                    (tenant_id, kommo_task_id, kommo_lead_id, text, due_at, responsible_id, completed, raw, synced_at)
                    VALUES (%s::uuid, %s, %s, %s, %s::timestamptz, %s, %s, %s::jsonb, now())
                    ON CONFLICT (tenant_id, kommo_task_id) DO UPDATE SET
                        kommo_lead_id = EXCLUDED.kommo_lead_id, text = EXCLUDED.text, due_at = EXCLUDED.due_at,
                        responsible_id = EXCLUDED.responsible_id, completed = EXCLUDED.completed,
                        raw = EXCLUDED.raw, synced_at = now()
                """, (
                    tenant_id, task_id, int(item.get("kommo_lead_id") or 0), str(item.get("text") or "")[:4000],
                    item.get("due_at") or None, int(item.get("responsible_id") or 0), bool(item.get("completed")),
                    _json(item.get("raw") if isinstance(item.get("raw"), dict) else {}),
                ))
                saved += 1
        conn.commit()
    return saved


def list_crm_tasks(*, tenant_id: str, responsible_id: int | None = None, limit: int = 100) -> list[dict]:
    clauses = ["tenant_id = %s::uuid"]
    values: list = [tenant_id]
    if responsible_id:
        clauses.append("responsible_id = %s")
        values.append(int(responsible_id))
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM saas_crm_tasks WHERE {' AND '.join(clauses)} "
                "ORDER BY completed ASC, due_at ASC NULLS LAST, synced_at DESC LIMIT %s",
                [*values, max(1, min(int(limit or 100), 200))],
            )
            rows = cur.fetchall()
    return [_public_crm_row(row) for row in rows]


def upsert_crm_messages(*, tenant_id: str, kommo_lead_id: int, messages: list[dict]) -> int:
    """Store a compact per-tenant chat snapshot; no messages cross tenants."""
    saved = 0
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            for item in messages:
                external_id = str(item.get("external_id") or item.get("id") or "").strip()
                if not external_id:
                    fingerprint = _json({"at": item.get("happened_at"), "body": item.get("body"), "direction": item.get("direction")})
                    external_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                cur.execute("""
                    INSERT INTO saas_crm_messages
                    (id, tenant_id, kommo_lead_id, external_id, direction, channel, author_name,
                     body, message_type, media_url, happened_at, raw)
                    VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s::jsonb)
                    ON CONFLICT (tenant_id, external_id) DO UPDATE SET
                        direction = EXCLUDED.direction, channel = EXCLUDED.channel,
                        author_name = EXCLUDED.author_name, body = EXCLUDED.body,
                        message_type = EXCLUDED.message_type, media_url = EXCLUDED.media_url,
                        happened_at = EXCLUDED.happened_at, raw = EXCLUDED.raw
                """, (
                    uuid.uuid4(), tenant_id, int(kommo_lead_id), external_id[:500],
                    str(item.get("direction") or "incoming")[:30], str(item.get("channel") or "")[:100],
                    str(item.get("author_name") or "")[:240], str(item.get("body") or "")[:12000],
                    str(item.get("message_type") or "text")[:60], str(item.get("media_url") or "")[:3000],
                    item.get("happened_at") or None,
                    _json(item.get("raw") if isinstance(item.get("raw"), dict) else {}),
                ))
                saved += 1
        conn.commit()
    return saved


def list_crm_messages(*, tenant_id: str, kommo_lead_id: int, limit: int = 120) -> list[dict]:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT external_id, direction, channel, author_name, body, message_type, media_url, happened_at, created_at
                FROM saas_crm_messages
                WHERE tenant_id = %s::uuid AND kommo_lead_id = %s
                ORDER BY happened_at ASC NULLS LAST, created_at ASC
                LIMIT %s
            """, (tenant_id, int(kommo_lead_id), max(1, min(int(limit or 120), 300))))
            rows = cur.fetchall()
    return [_public_crm_row(row) for row in rows]


def append_audit_event(*, tenant_id: str, action: str, actor_telegram_id: int | None = None,
                       entity_type: str = "", entity_id: str = "", payload: dict | None = None) -> None:
    """Keep an append-only tenant audit trail for security-relevant actions."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenant_audit_events
                (id, tenant_id, actor_telegram_id, action, entity_type, entity_id, payload)
                VALUES (%s, %s::uuid, %s, %s, %s, %s, %s::jsonb)
            """, (uuid.uuid4(), tenant_id, int(actor_telegram_id) if actor_telegram_id else None,
                  str(action or "")[:120], str(entity_type or "")[:120], str(entity_id or "")[:180], _json(payload or {})))
        conn.commit()


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


def _member_payload(row: dict) -> dict:
    """Make a member row safe to return from the platform API."""
    result = dict(row)
    result["tenant_id"] = str(result["tenant_id"])
    for key in ("created_at", "updated_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if not isinstance(result.get("permissions"), list):
        result["permissions"] = []
    return result


def list_members(*, tenant_id: str, owner_id: int) -> list[dict]:
    """Return the tenant's employee cards to its owner only."""
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT tenant_id, telegram_id, display_name, role, permissions, active, created_at, updated_at
                FROM saas_tenant_members
                WHERE tenant_id = %s::uuid
                ORDER BY CASE WHEN role = 'owner' THEN 0 ELSE 1 END, display_name, telegram_id
            """, (tenant_id,))
            rows = cur.fetchall()
    return [_member_payload(row) for row in rows]


def upsert_member(*, tenant_id: str, owner_id: int, telegram_id: int, display_name: str,
                  role: str, permissions: list[str] | None = None) -> dict:
    """Create or update an employee card keyed by their Telegram account ID.

    This intentionally needs no invitation link: a person is admitted only
    after Telegram proves that they own this exact numeric account ID.
    """
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    try:
        target_id = int(telegram_id)
    except (TypeError, ValueError):
        target_id = 0
    if target_id <= 0:
        raise TenantPlatformError("Telegram ID düzgün deyil.")
    name = str(display_name or "").strip()[:120]
    if not name:
        raise TenantPlatformError("Əməkdaşın adını yazın.")
    is_owner = target_id == int(current["telegram_id"])
    selected_role = _role(str(role or ""), owner=is_owner)
    selected_permissions = _permissions(permissions or [], selected_role)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions, active, invited_by)
                VALUES (%s::uuid, %s, %s, %s, %s::jsonb, TRUE, %s)
                ON CONFLICT (tenant_id, telegram_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    role = CASE WHEN saas_tenant_members.role = 'owner' THEN 'owner' ELSE EXCLUDED.role END,
                    permissions = CASE WHEN saas_tenant_members.role = 'owner' THEN saas_tenant_members.permissions ELSE EXCLUDED.permissions END,
                    active = TRUE,
                    updated_at = now()
                RETURNING tenant_id, telegram_id, display_name, role, permissions, active, created_at, updated_at
            """, (tenant_id, target_id, name, selected_role, _json(selected_permissions), int(owner_id)))
            row = cur.fetchone()
        conn.commit()
    return _member_payload(row)


def deactivate_member(*, tenant_id: str, owner_id: int, telegram_id: int) -> dict:
    """Disable a former employee without deleting their audit record."""
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    if int(telegram_id) == int(current["telegram_id"]):
        raise TenantPlatformError("Öz girişinizi bağlaya bilməzsiniz.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenant_members SET active = FALSE, updated_at = now()
                WHERE tenant_id = %s::uuid AND telegram_id = %s AND role <> 'owner'
                RETURNING tenant_id, telegram_id, display_name, role, permissions, active, created_at, updated_at
            """, (tenant_id, int(telegram_id)))
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise TenantPlatformError("Əməkdaş tapılmadı.")
    return _member_payload(row)


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
    company_name = str(safe_patch.get("company_name") or "").strip()[:120]
    if len(company_name) < 2:
        company_name = str(current.get("tenant_name") or "Yeni şirkət")[:120]
    industry = str(safe_patch.get("industry") or current.get("industry") or "").strip()[:120]
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenants SET name = %s, industry = %s, onboarding = %s::jsonb, modules = %s::jsonb,
                    notification_rules = %s::jsonb, updated_at = now()
                WHERE id = %s::uuid RETURNING *
            """, (company_name, industry, _json(onboarding), _json(modules), _json(notifications), tenant_id))
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
    name = str(display_name or "").strip()[:120]
    if not name:
        raise TenantPlatformError("Əməkdaşın adını yazın.")
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
            # Keep previously sent links valid until they are used or expire.
            # This matters when an administrator opens the settings page again
            # and creates a replacement link before the employee taps the first
            # one in Telegram.  Each token is still one-use and time-limited.
            cur.execute("""
                INSERT INTO saas_tenant_invites (id, tenant_id, token_hash, display_name, role, permissions, created_by, expires_at)
                VALUES (%s, %s::uuid, %s, %s, %s, %s::jsonb, %s, %s)
            """, (invite_id, tenant_id, token_hash, name, selected_role, _json(_permissions(permissions, selected_role)), int(owner_id), expires))
        conn.commit()
    return {"id": str(invite_id), "token": token, "role": selected_role, "expires_at": expires.isoformat()}


def request_invite_acceptance(*, token: str, telegram_id: int, display_name: str) -> dict:
    """Bind a Telegram identity to an invite, awaiting owner approval."""
    normalized_token = _invite_token(token)
    token_hash = hashlib.sha256(normalized_token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM saas_tenant_invites WHERE token_hash = %s FOR UPDATE", (token_hash,))
            invite = cur.fetchone()
            if not invite:
                raise TenantPlatformError("Dəvət linki etibarlı deyil. Administratorla əlaqə saxlayın.")
            if invite.get("accepted_at"):
                raise TenantPlatformError("Bu dəvət linki artıq istifadə olunub.")
            if invite.get("expires_at") and invite["expires_at"] <= datetime.now(timezone.utc):
                raise TenantPlatformError("Dəvət linkinin müddəti bitib. Administratorla əlaqə saxlayın.")
            cur.execute("SELECT * FROM saas_tenant_invite_requests WHERE invite_id = %s FOR UPDATE", (invite["id"],))
            existing = cur.fetchone()
            if existing:
                if int(existing["telegram_id"]) != int(telegram_id):
                    raise TenantPlatformError("Bu dəvət artıq başqa hesab tərəfindən istifadə olunub.")
                # A rejected request is not a permanent technical failure.
                # Let the same invited employee ask again after correcting a
                # mistaken decision, while keeping the audit timestamp.
                if existing["status"] == "rejected":
                    cur.execute("""
                        UPDATE saas_tenant_invite_requests
                        SET status = 'pending', decided_by = NULL, decided_at = NULL, display_name = %s
                        WHERE id = %s RETURNING *
                    """, (str(display_name or invite["display_name"] or "").strip()[:120], existing["id"]))
                    result = dict(cur.fetchone())
                else:
                    result = dict(existing)
            else:
                request_id = uuid.uuid4()
                cur.execute("""
                    INSERT INTO saas_tenant_invite_requests (id, invite_id, tenant_id, telegram_id, display_name)
                    VALUES (%s, %s, %s, %s, %s) RETURNING *
                """, (request_id, invite["id"], invite["tenant_id"], int(telegram_id), str(display_name or invite["display_name"] or "").strip()[:120]))
                result = dict(cur.fetchone())
        conn.commit()
    return {
        "id": str(result["id"]), "tenant_id": str(result["tenant_id"]), "status": result["status"],
        "owner_id": int(invite["created_by"]), "employee_name": str(result.get("display_name") or invite.get("display_name") or "Əməkdaş"),
        "card_name": str(invite.get("display_name") or "Əməkdaş"),
    }


def list_invite_requests(*, tenant_id: str, owner_id: int) -> list[dict]:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.telegram_id, r.display_name, r.status, r.created_at,
                       i.display_name AS card_name, i.role, i.permissions
                FROM saas_tenant_invite_requests r
                JOIN saas_tenant_invites i ON i.id = r.invite_id
                WHERE r.tenant_id = %s::uuid
                ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END, r.created_at DESC
            """, (tenant_id,))
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        if not isinstance(item.get("permissions"), list):
            item["permissions"] = []
        result.append(item)
    return result


def decide_invite_request(*, tenant_id: str, owner_id: int, request_id: str, approve: bool) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    try:
        rid = uuid.UUID(str(request_id))
    except (ValueError, TypeError):
        raise TenantPlatformError("Sorğu tapılmadı.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.*, i.display_name AS card_name, i.role, i.permissions, i.created_by
                FROM saas_tenant_invite_requests r JOIN saas_tenant_invites i ON i.id = r.invite_id
                WHERE r.id = %s AND r.tenant_id = %s::uuid FOR UPDATE
            """, (rid, tenant_id))
            request = cur.fetchone()
            if not request or request["status"] != "pending":
                raise TenantPlatformError("Sorğu artıq işlənib və ya tapılmadı.")
            if approve:
                cur.execute("""
                    INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions, active, invited_by)
                    VALUES (%s::uuid, %s, %s, %s, %s::jsonb, TRUE, %s)
                    ON CONFLICT (tenant_id, telegram_id) DO UPDATE SET active = TRUE, display_name = EXCLUDED.display_name,
                        role = EXCLUDED.role, permissions = EXCLUDED.permissions, updated_at = now()
                """, (tenant_id, int(request["telegram_id"]), str(request["display_name"] or request["card_name"] or "").strip()[:120], request["role"], _json(request["permissions"]), int(owner_id)))
                cur.execute("UPDATE saas_tenant_invites SET accepted_by = %s, accepted_at = now() WHERE id = %s", (int(request["telegram_id"]), request["invite_id"]))
            cur.execute("UPDATE saas_tenant_invite_requests SET status = %s, decided_by = %s, decided_at = now() WHERE id = %s", ("approved" if approve else "rejected", int(owner_id), rid))
        conn.commit()
    return {"telegram_id": int(request["telegram_id"]), "display_name": str(request["display_name"] or request["card_name"] or ""), "approved": bool(approve)}


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
