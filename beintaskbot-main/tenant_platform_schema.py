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
        conn.commit()
        _schema_ready = True
