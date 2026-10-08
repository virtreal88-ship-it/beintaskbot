"""Additive AI receipts/quota index and public feed index; no old DDL changed."""
from threading import Lock
from tenant_linear_observer_schema import ensure_observer_schema

_ready = False
_lock = Lock()


def ensure_ai_schema(conn):
    global _ready
    ensure_observer_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-news-ai-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_news_ai_runs(
              tenant_id UUID NOT NULL,news_id UUID NOT NULL,status TEXT NOT NULL
                CHECK(status IN ('sending','completed','unknown','skipped')),
              source_version TIMESTAMPTZ NOT NULL,config_version TEXT NOT NULL,
              usage_day DATE NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')::date,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,news_id),FOREIGN KEY(tenant_id,news_id)
                REFERENCES saas_linear_news(tenant_id,id) ON DELETE CASCADE)''')
            cur.execute('CREATE INDEX IF NOT EXISTS saas_news_ai_quota_idx ON saas_news_ai_runs(tenant_id,usage_day)')
            cur.execute("CREATE INDEX IF NOT EXISTS saas_news_public_idx ON saas_linear_news(tenant_id,published_at DESC,id DESC) WHERE status='published'")
        conn.commit()
        _ready = True
