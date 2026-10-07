"""Additive schema for durable, tenant-local CRM read jobs."""
from threading import Lock

from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_sync_schema(conn) -> None:
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('crm-sync-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_sync_jobs (
                tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                resource TEXT NOT NULL CHECK (resource IN ('leads','tasks')),
                run_id UUID NOT NULL,
                connection_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
                next_page INTEGER NOT NULL DEFAULT 1 CHECK (next_page > 0),
                last_record_id BIGINT NOT NULL DEFAULT 0,
                pages_done INTEGER NOT NULL DEFAULT 0,
                records_saved INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at TIMESTAMPTZ NULL,
                PRIMARY KEY (tenant_id, resource)
            )''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_sync_jobs_pending_idx
                ON saas_crm_sync_jobs(next_attempt_at, updated_at)
                WHERE status IN ('queued','running')''')
        conn.commit()
        _ready = True
