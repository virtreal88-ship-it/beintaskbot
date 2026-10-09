"""Additive manual-review metadata; original receipts remain compatible."""
from tenant_chat_send_schema import ensure_schema as ensure_receipts


def ensure_schema(conn):
    ensure_receipts(conn)
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE saas_chat_send_receipts ADD COLUMN IF NOT EXISTS resolution JSONB NOT NULL DEFAULT '{}'::jsonb")
        cur.execute('CREATE INDEX IF NOT EXISTS saas_chat_send_review_lead ON saas_chat_send_receipts(tenant_id,lead_id,created_at DESC)')
