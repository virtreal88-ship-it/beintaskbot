"""Tenant-isolated storage for proactive risk findings and owner alert outbox."""
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
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_proactive_risk_scope_checks (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              source_kind TEXT NOT NULL CHECK(source_kind IN ('linear','crm_task')),
              source_scope TEXT NOT NULL, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,source_kind,source_scope)
            )''')
            # Dedicated, opt-in outbox. Alert rows contain routing/evidence
            # hashes only; source text is re-read from its tenant-scoped store
            # immediately before either in-app or Web Push delivery.
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_proactive_risk_alert_events (
              id UUID PRIMARY KEY,
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              recipient_id BIGINT NOT NULL,
              source_kind TEXT NOT NULL CHECK(source_kind IN ('linear','crm_task')),
              source_id TEXT NOT NULL, rule_id TEXT NOT NULL, source_scope TEXT NOT NULL,
              source_version TEXT NOT NULL, alert_fingerprint TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending'
                CONSTRAINT saas_proactive_risk_alert_events_state_check
                CHECK(state IN ('pending','expanded','deferred','cancelled','suppressed')),
              retry_after TIMESTAMPTZ NULL,
              quota_reserved_at TIMESTAMPTZ NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(tenant_id,id)
            )''')
            cur.execute('''DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='saas_proactive_risk_alert_events'::regclass
                  AND conname='saas_proactive_risk_alert_events_state_check'
                  AND position('deferred' in pg_get_constraintdef(oid))=0) THEN
                ALTER TABLE saas_proactive_risk_alert_events
                  DROP CONSTRAINT saas_proactive_risk_alert_events_state_check;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='saas_proactive_risk_alert_events'::regclass
                  AND conname='saas_proactive_risk_alert_events_state_check') THEN
                ALTER TABLE saas_proactive_risk_alert_events
                  ADD CONSTRAINT saas_proactive_risk_alert_events_state_check
                  CHECK(state IN ('pending','expanded','deferred','cancelled','suppressed'));
              END IF;
            END $$''')
            cur.execute("ALTER TABLE saas_proactive_risk_alert_events ADD COLUMN IF NOT EXISTS retry_after TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE saas_proactive_risk_alert_events ADD COLUMN IF NOT EXISTS quota_reserved_at TIMESTAMPTZ NULL")
            cur.execute('''UPDATE saas_proactive_risk_alert_events SET quota_reserved_at=created_at
              WHERE quota_reserved_at IS NULL AND state NOT IN ('deferred','suppressed')
                AND created_at>=now()-interval '1 day' ''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_alert_pending_idx
              ON saas_proactive_risk_alert_events(created_at,tenant_id) WHERE state='pending' ''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_alert_retry_pending_idx
              ON saas_proactive_risk_alert_events(retry_after,created_at,tenant_id) WHERE state='pending' ''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_proactive_risk_alert_deliveries (
              tenant_id UUID NOT NULL,
              event_id UUID NOT NULL,
              recipient_id BIGINT NOT NULL,
              channel TEXT NOT NULL CHECK(channel IN ('internal','push')),
              endpoint_hash TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending'
              CHECK(status IN ('pending','sending','accepted','delivered','unknown','skipped','expired')),
              retry_after TIMESTAMPTZ NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,event_id,recipient_id,channel,endpoint_hash),
              FOREIGN KEY(tenant_id,event_id)
                REFERENCES saas_proactive_risk_alert_events(tenant_id,id) ON DELETE CASCADE
            )''')
            cur.execute("ALTER TABLE saas_proactive_risk_alert_deliveries ADD COLUMN IF NOT EXISTS retry_after TIMESTAMPTZ NULL")
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_alert_delivery_pending_idx
              ON saas_proactive_risk_alert_deliveries(channel,updated_at)
              WHERE status='pending' ''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_alert_delivery_retry_idx
              ON saas_proactive_risk_alert_deliveries(channel,retry_after,updated_at)
              WHERE status='pending' ''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_alert_delivery_sending_idx
              ON saas_proactive_risk_alert_deliveries(updated_at) WHERE status='sending' ''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_proactive_risk_alert_backfills (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              owner_id BIGINT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done')),
              cursor_source_kind TEXT NOT NULL DEFAULT '',
              cursor_source_id TEXT NOT NULL DEFAULT '',
              cursor_rule_id TEXT NOT NULL DEFAULT '',
              retry_after TIMESTAMPTZ NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,owner_id)
            )''')
            cur.execute("ALTER TABLE saas_proactive_risk_alert_backfills ADD COLUMN IF NOT EXISTS retry_after TIMESTAMPTZ NULL")
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_proactive_risk_alert_backfill_pending_idx
              ON saas_proactive_risk_alert_backfills(updated_at,tenant_id) WHERE state='pending' ''')
        conn.commit()
        _ready = True
