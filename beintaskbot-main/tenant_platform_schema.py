"""Additive PostgreSQL schema for the tenant SaaS platform."""
from threading import Lock

from tenant_platform_schema_crm import migrate_crm_schema
from tenant_platform_schema_identity import migrate_identity_schema
from tenant_platform_schema_operations import migrate_operations_schema

_schema_ready = False
_schema_lock = Lock()

def _ensure_schema(conn) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with conn.cursor() as cur:
            # Process-local locks do not protect concurrent web/worker startup.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('tenant-platform-schema',0))")
            migrate_identity_schema(cur)
            migrate_operations_schema(cur)
            migrate_crm_schema(cur)
            # Durable per-user fencing for the owner-scoped Realtime pilot.
            # Additive only; no provider traffic or account setting is enabled
            # by creating this table.
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_operator_voice_lease (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              channel_id UUID NOT NULL,
              conversation_id TEXT NOT NULL CHECK(length(conversation_id) BETWEEN 1 AND 200),
              session_id UUID NOT NULL UNIQUE,
              actor_id BIGINT NOT NULL CHECK(actor_id > 0),
              owner_id UUID NOT NULL,
              fence BIGINT NOT NULL CHECK(fence > 0),
              state TEXT NOT NULL CHECK(state IN ('starting','active','closed')),
              lease_until TIMESTAMPTZ NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              closed_at TIMESTAMPTZ,
              PRIMARY KEY(tenant_id,actor_id))''')
            cur.execute("CREATE INDEX IF NOT EXISTS saas_operator_voice_lease_expiry_idx ON saas_operator_voice_lease(lease_until) WHERE state IN ('starting','active')")
        conn.commit()
        _schema_ready = True
