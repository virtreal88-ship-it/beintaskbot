"""Additive queues for final approval outcomes, no historical backfill."""
from tenant_platform import _ensure_schema


def ensure_schema(conn) -> None:
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_task_decision_events (
          tenant_id UUID NOT NULL,actor_id BIGINT NOT NULL,request_id UUID NOT NULL,
          outcome TEXT NOT NULL CHECK(outcome IN ('approved','rejected')),
          expanded BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,actor_id,request_id),
          FOREIGN KEY(tenant_id,actor_id,request_id) REFERENCES saas_task_commands(tenant_id,actor_id,request_id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_task_decision_deliveries (
          tenant_id UUID NOT NULL,actor_id BIGINT NOT NULL,request_id UUID NOT NULL,
          recipient_id BIGINT NOT NULL,channel TEXT NOT NULL CHECK(channel IN ('telegram','push')),
          endpoint_hash TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','sending','delivered','unknown','skipped','expired')),
          message_id BIGINT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,actor_id,request_id,recipient_id,channel,endpoint_hash),
          FOREIGN KEY(tenant_id,actor_id,request_id) REFERENCES saas_task_decision_events(tenant_id,actor_id,request_id))''')
        cur.execute("CREATE INDEX IF NOT EXISTS saas_task_decision_pending_events ON saas_task_decision_events(created_at) WHERE expanded=FALSE")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_task_decision_pending_delivery ON saas_task_decision_deliveries(channel,updated_at) WHERE status='pending'")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_task_decision_sending_delivery ON saas_task_decision_deliveries(updated_at) WHERE status='sending'")
