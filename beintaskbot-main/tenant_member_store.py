"""Tenant, membership and onboarding persistence."""
import uuid

from tenant_platform_common import (
    _DEFAULT_MODULES, _DEFAULT_NOTIFICATIONS, _ROLE_PERMISSIONS,
    _permissions, _role, _slug, _tenant_payload,
)
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json
from tenant_workflow_store import _read_tenant_workflow

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
                SELECT m.*, au.email, t.name AS tenant_name, t.slug AS tenant_slug, t.status AS tenant_status,
                       t.modules, t.notification_rules, t.onboarding, t.industry
                FROM saas_tenant_members m
                JOIN saas_tenants t ON t.id = m.tenant_id
                LEFT JOIN saas_auth_users au ON au.actor_id = m.telegram_id
                WHERE m.tenant_id = %s::uuid AND m.telegram_id = %s AND m.active = TRUE
            """, (tenant_id, int(telegram_id)))
            row = cur.fetchone()
            workflow = _read_tenant_workflow(cur, tenant_id) if row else {}
    if not row:
        return None
    result = dict(row)
    result['workflow'] = workflow
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
                SELECT m.tenant_id, m.telegram_id, m.display_name, m.role, m.permissions, m.active,
                       m.created_at, m.updated_at, au.email
                FROM saas_tenant_members m
                LEFT JOIN saas_auth_users au ON au.actor_id = m.telegram_id
                WHERE m.tenant_id = %s::uuid
                ORDER BY CASE WHEN m.role = 'owner' THEN 0 ELSE 1 END, m.display_name, m.telegram_id
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
    selected_permissions = _permissions(permissions, selected_role)
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
            cur.execute("""
                UPDATE saas_tenants t SET status = CASE
                    WHEN t.status IN ('onboarding', 'ready_for_integration') THEN 'active'
                    ELSE t.status END, updated_at = now()
                WHERE t.id = %s::uuid RETURNING t.*
            """, (tenant_id,))
            row = cur.fetchone()
        conn.commit()
    return _tenant_payload(row)
