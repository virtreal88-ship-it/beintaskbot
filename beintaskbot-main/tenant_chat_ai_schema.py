"""Additive operation receipts and tenant-scoped transcription cache."""
from threading import Lock
from tenant_platform import _ensure_schema
_ready = False
_lock = Lock()


def ensure_schema(conn):
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_chat_ai_runs(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              request_id UUID NOT NULL,requested_by BIGINT NOT NULL,lead_id BIGINT NOT NULL,
              input_hash TEXT NOT NULL,config_version TEXT NOT NULL,status TEXT NOT NULL,
              reserved_calls INTEGER NOT NULL CHECK(reserved_calls>=0),result JSONB,
              usage_day DATE NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')::date,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,request_id),CHECK(status IN ('sending','completed','unknown','skipped')),
              FOREIGN KEY(tenant_id,requested_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_chat_transcripts(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,lead_id BIGINT NOT NULL,
              media_hash TEXT NOT NULL,transcript TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,lead_id,media_hash))''')
            cur.execute('CREATE INDEX IF NOT EXISTS saas_chat_ai_usage_idx ON saas_chat_ai_runs(tenant_id,usage_day)')
            cur.execute('CREATE INDEX IF NOT EXISTS saas_chat_transcript_age_idx ON saas_chat_transcripts(created_at)')
        conn.commit()
        _ready = True
