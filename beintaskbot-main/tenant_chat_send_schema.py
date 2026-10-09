"""Additive receipts; no legacy tables or jobs are changed."""
from tenant_platform import _ensure_schema


def ensure_schema(conn):
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_chat_send_receipts (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id), actor_id BIGINT NOT NULL,
          request_id UUID NOT NULL, lead_id BIGINT NOT NULL, input_hash TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('preparing','sending','accepted','unknown','blocked')),
          route JSONB NOT NULL DEFAULT '{}'::jsonb, message_id TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,actor_id,request_id))''')
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_chat_send_active_lead
          ON saas_chat_send_receipts(tenant_id,lead_id) WHERE state IN ('preparing','sending','unknown')''')
