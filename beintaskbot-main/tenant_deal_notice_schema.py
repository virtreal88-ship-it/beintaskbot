"""Additive event/delivery journal for new deal approval requests."""


def ensure_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_deal_notice_events (
      tenant_id UUID NOT NULL,actor_id BIGINT NOT NULL,request_id UUID NOT NULL,
      expanded BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,actor_id,request_id),
      FOREIGN KEY(tenant_id,actor_id,request_id)
        REFERENCES saas_deal_completion_commands(tenant_id,actor_id,request_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_deal_notice_deliveries (
      tenant_id UUID NOT NULL,actor_id BIGINT NOT NULL,request_id UUID NOT NULL,
      recipient_id BIGINT NOT NULL,channel TEXT NOT NULL CHECK(channel IN ('telegram','push')),
      endpoint_hash TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','delivered','unknown','skipped','expired')),
      message_id BIGINT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,actor_id,request_id,recipient_id,channel,endpoint_hash),
      FOREIGN KEY(tenant_id,actor_id,request_id) REFERENCES saas_deal_notice_events(tenant_id,actor_id,request_id))''')
    cur.execute("CREATE INDEX IF NOT EXISTS saas_deal_notice_pending ON saas_deal_notice_deliveries(channel,updated_at) WHERE status='pending'")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_deal_notice_unexpanded ON saas_deal_notice_events(created_at) WHERE expanded=FALSE")
