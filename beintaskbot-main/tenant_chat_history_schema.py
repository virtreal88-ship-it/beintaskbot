"""Additive index for cached history keyset pagination."""
from tenant_platform import _ensure_schema


def ensure_schema(conn) -> None:
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_messages_history_position
          ON saas_crm_messages(tenant_id,kommo_lead_id,(COALESCE(happened_at,created_at)) DESC,external_id DESC)''')
