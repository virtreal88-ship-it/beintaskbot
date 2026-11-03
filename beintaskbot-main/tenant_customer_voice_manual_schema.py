"""Separate employee notes about an uncertain WhatsApp send from provider status."""
from tenant_platform import _ensure_schema


def ensure_schema(conn):
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_voice_manual_checks (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
          actor_id BIGINT NOT NULL,
          request_id UUID NOT NULL,
          lead_id BIGINT NOT NULL,
          outcome TEXT NOT NULL CHECK(outcome IN ('seen','not_seen','unsure')),
          recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,actor_id,request_id))''')
