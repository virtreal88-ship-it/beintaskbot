"""Additive CRM notification observations/outbox, no historical backfill."""
from tenant_platform import _ensure_schema


def ensure_schema(conn) -> None:
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_notice_scan (
          tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id),
          baseline TIMESTAMPTZ NOT NULL DEFAULT now(),positions JSONB NOT NULL DEFAULT '{}',
          next_scan TIMESTAMPTZ NOT NULL DEFAULT now(),connection_id TEXT NOT NULL DEFAULT '')''')
        cur.execute("ALTER TABLE saas_crm_notice_scan ADD COLUMN IF NOT EXISTS connection_id TEXT NOT NULL DEFAULT ''")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_notice_scan_due ON saas_crm_notice_scan(next_scan,tenant_id)")
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_notice_observations (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id),kind TEXT NOT NULL,source_key TEXT NOT NULL,
          state JSONB NOT NULL,PRIMARY KEY(tenant_id,kind,source_key))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_notice_events (
          id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
          event TEXT NOT NULL CHECK(event IN ('new_lead','incoming_message','task_assigned','task_overdue')),
          kind TEXT NOT NULL,source_key TEXT NOT NULL,version TEXT NOT NULL,payload JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(tenant_id,event,source_key,version),UNIQUE(tenant_id,id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_notice_deliveries (
          tenant_id UUID NOT NULL,event_id UUID NOT NULL,recipient_id BIGINT NOT NULL,
          channel TEXT NOT NULL CHECK(channel IN ('telegram','push')),endpoint_hash TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','delivered','unknown','skipped','expired')),
          message_id BIGINT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,event_id,recipient_id,channel,endpoint_hash),
          FOREIGN KEY(tenant_id,event_id) REFERENCES saas_crm_notice_events(tenant_id,id))''')
        cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_notice_pending ON saas_crm_notice_deliveries(channel,updated_at) WHERE status='pending'")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_notice_sending ON saas_crm_notice_deliveries(updated_at) WHERE status='sending'")
        for table, field in (('saas_crm_deals', 'kommo_lead_id'), ('saas_crm_tasks', 'kommo_task_id'), ('saas_crm_messages', 'external_id')):
            cur.execute(f'CREATE INDEX IF NOT EXISTS {table}_notice_scan ON {table}(tenant_id, ({field}::text))')
