"""Additive tenant checkpoints, events, 90-day news and permanent dedup keys."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_observer_schema(conn) -> None:
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-linear-observer-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_sync (
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              config_version TEXT NOT NULL DEFAULT '', cursor TEXT NULL, since_at TIMESTAMPTZ NULL,
              until_at TIMESTAMPTZ NULL, due_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_snapshots (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, issue_id UUID NOT NULL,
              source_version TIMESTAMPTZ NOT NULL, state_id TEXT NOT NULL, snapshot JSONB NOT NULL,
              PRIMARY KEY(tenant_id,issue_id)
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_notice_events (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, id UUID NOT NULL,
              issue_id UUID NOT NULL, source_version TIMESTAMPTZ NOT NULL, event TEXT NOT NULL
              CHECK(event IN ('linear_done','linear_status_changed')), recipient_cursor BIGINT NOT NULL DEFAULT 0,
              expanded BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,issue_id)
              REFERENCES saas_linear_snapshots(tenant_id,issue_id) ON DELETE CASCADE
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_notice_deliveries (
              tenant_id UUID NOT NULL, event_id UUID NOT NULL, recipient_id BIGINT NOT NULL,
              channel TEXT NOT NULL CHECK(channel IN ('telegram','push')), endpoint_hash TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','delivered','unknown','skipped','expired')),
              message_id BIGINT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,event_id,recipient_id,channel,endpoint_hash),
              FOREIGN KEY(tenant_id,event_id) REFERENCES saas_linear_notice_events(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,recipient_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
            )''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_linear_pending_idx
              ON saas_linear_notice_deliveries(channel,updated_at) WHERE status='pending' ''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_news_seen (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, issue_id UUID NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,issue_id)
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_news (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, id UUID NOT NULL,
              issue_id UUID NOT NULL, identifier TEXT NOT NULL, project_name TEXT NOT NULL, title TEXT NOT NULL,
              summary TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', source JSONB NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','published','rejected')),
              reviewed_by BIGINT NULL, published_at TIMESTAMPTZ NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,issue_id),
              FOREIGN KEY(tenant_id,reviewed_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT
            )''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_linear_news_queue_idx
              ON saas_linear_news(tenant_id,status,created_at DESC,id DESC)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_linear_news_expiry_idx
              ON saas_linear_news((COALESCE(published_at,created_at)))''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_linear_event_expiry_idx
              ON saas_linear_notice_events(created_at)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_linear_event_queue_idx
              ON saas_linear_notice_events(created_at) WHERE expanded=FALSE''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_linear_snapshot_expiry_idx
              ON saas_linear_snapshots(source_version) WHERE snapshot<>'{}'::jsonb''')
        conn.commit()
        _ready = True
