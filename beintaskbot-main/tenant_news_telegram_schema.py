"""Additive verified channel and durable delivery receipts. No legacy migration edits."""
from threading import Lock
from tenant_news_ai_schema import ensure_ai_schema
_ready=False
_lock=Lock()


def ensure_schema(conn):
    global _ready
    ensure_ai_schema(conn)
    if _ready:return
    with _lock:
        if _ready:return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-news-telegram-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_news_channels(
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              chat_id BIGINT NOT NULL UNIQUE,bot_id BIGINT NOT NULL,verified_by BIGINT NOT NULL,
              title TEXT NOT NULL,binding_version UUID NOT NULL,config JSONB NOT NULL,
              next_at TIMESTAMPTZ NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,verified_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            # No FK to news: archive cleanup must not erase the once-only receipt.
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_news_telegram_queue(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,news_id UUID NOT NULL,
              requested_by BIGINT NOT NULL,chat_id BIGINT NOT NULL,binding_version UUID NOT NULL,
              payload JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','sending','sent','unknown','blocked','cancelled','expired')),
              message_id BIGINT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,news_id),FOREIGN KEY(tenant_id,requested_by)
                REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            cur.execute("CREATE INDEX IF NOT EXISTS saas_news_telegram_pending_idx ON saas_news_telegram_queue(created_at) WHERE status='pending'")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_news_telegram_cleanup_idx ON saas_news_telegram_queue(created_at) WHERE payload<>'{}'::jsonb")
        conn.commit();_ready=True
