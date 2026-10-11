"""Compatibility functions for tenant CRM snapshots and audit events."""
import uuid

from tenant_platform_core import _connect, _ensure_schema, _json

def _public_crm_row(row: dict) -> dict:
    """Turn a PostgreSQL CRM snapshot into a JSON-safe browser payload."""
    from tenant_crm_queries import public_crm_row
    return public_crm_row(row)


def upsert_crm_deals(*, tenant_id: str, deals: list[dict]) -> int:
    """Persist a tenant's Kommo deal snapshot without touching legacy data."""
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, serialize=_json).upsert_deals(
        tenant_id=tenant_id, deals=deals)


def list_crm_deals(*, tenant_id: str, search: str = "", pipeline_ids: list[int] | None = None,
                   status_ids: list[int] | None = None, limit: int = 100, offset: int = 0,
                   scope: list[dict] | None = None) -> tuple[list[dict], int]:
    """List only the selected tenant's cached deals, with DB-side filtering."""
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, payload=_public_crm_row).list_deals(
        tenant_id=tenant_id, search=search, pipeline_ids=pipeline_ids,
        status_ids=status_ids, limit=limit, offset=offset, scope=scope)


def get_crm_deal(*, tenant_id: str, kommo_lead_id: int) -> dict | None:
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, payload=_public_crm_row).get_deal(
        tenant_id=tenant_id, kommo_lead_id=kommo_lead_id)


def get_crm_task(*, tenant_id: str, kommo_task_id: int) -> dict | None:
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, payload=_public_crm_row).get_task(
        tenant_id=tenant_id, kommo_task_id=kommo_task_id)


def upsert_crm_tasks(*, tenant_id: str, tasks: list[dict]) -> int:
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, serialize=_json).upsert_tasks(
        tenant_id=tenant_id, tasks=tasks)


def list_crm_tasks(*, tenant_id: str, responsible_id: int | None = None, scope: dict | None = None, limit: int = 100) -> list[dict]:
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, payload=_public_crm_row).list_tasks(
        tenant_id=tenant_id, responsible_id=responsible_id, scope=scope, limit=limit)


def upsert_crm_messages(*, tenant_id: str, kommo_lead_id: int, messages: list[dict]) -> int:
    """Store a compact per-tenant chat snapshot; no messages cross tenants."""
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, serialize=_json, id_factory=uuid.uuid4).upsert_messages(
        tenant_id=tenant_id, kommo_lead_id=kommo_lead_id, messages=messages)


def list_crm_messages(*, tenant_id: str, kommo_lead_id: int, limit: int = 120) -> list[dict]:
    from tenant_crm_repository import CRMRepository
    return CRMRepository(_connect, _ensure_schema, payload=_public_crm_row).list_messages(
        tenant_id=tenant_id, kommo_lead_id=kommo_lead_id, limit=limit)


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
