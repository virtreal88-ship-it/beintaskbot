"""Tenant-isolated storage for read-only proactive risk dry runs."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_schema(conn) -> None:
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('tenant-proactive-risk-schema',0))")
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_proactive_risk_findings (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              source_kind TEXT NOT NULL CHECK(source_kind IN ('linear','crm_task')),
              source_id TEXT NOT NULL, rule_id TEXT NOT NULL,
              source_scope TEXT NOT NULL DEFAULT '',
              source_version TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('P1','P2')),
              confidence TEXT NOT NULL CHECK(confidence IN ('low','medium','high')),
              source_title TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
              facts JSONB NOT NULL DEFAULT '[]'::jsonb, assessment TEXT NOT NULL,
              recommended_step TEXT NOT NULL, fingerprint TEXT NOT NULL,
              checked_at TIMESTAMPTZ NOT NULL DEFAULT now(), source_checked_at TIMESTAMPTZ NULL,
              active BOOLEAN NOT NULL DEFAULT TRUE,
              PRIMARY KEY(tenant_id,source_kind,source_id,rule_id)
            )''')
            cur.execute("ALTER TABLE saas_proactive_risk_findings ADD COLUMN IF NOT EXISTS source_scope TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE saas_proactive_risk_findings ADD COLUMN IF NOT EXISTS source_checked_at TIMESTAMPTZ NULL")
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_active_idx
              ON saas_proactive_risk_findings(tenant_id,checked_at DESC)
              WHERE active=TRUE''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_proactive_risk_checks (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              source_kind TEXT NOT NULL CHECK(source_kind IN ('linear','crm_task')),
              checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,source_kind)
            )''')
        conn.commit()
        _ready = True
