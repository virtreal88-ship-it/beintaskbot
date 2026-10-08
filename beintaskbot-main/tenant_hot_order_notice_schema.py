"""Additive outbox migration and transactional availability events."""


def migrate_hot_order_notices(cur) -> None:
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_hot_order_events (
        tenant_id UUID NOT NULL,
        id UUID NOT NULL,
        order_id UUID NOT NULL,
        order_version TIMESTAMPTZ NOT NULL,
        expanded BOOLEAN NOT NULL DEFAULT FALSE,
        recipient_cursor BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id,id),
        FOREIGN KEY (tenant_id,order_id) REFERENCES saas_hot_orders(tenant_id,id) ON DELETE CASCADE
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_hot_order_deliveries (
        tenant_id UUID NOT NULL,
        event_id UUID NOT NULL,
        recipient_id BIGINT NOT NULL,
        channel TEXT NOT NULL CHECK (channel IN ('telegram','push')),
        endpoint_hash TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','delivered','unknown','skipped','expired')),
        message_id BIGINT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id,event_id,recipient_id,channel,endpoint_hash),
        FOREIGN KEY (tenant_id,event_id) REFERENCES saas_hot_order_events(tenant_id,id) ON DELETE CASCADE
    )''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_hot_order_events_pending_idx
        ON saas_hot_order_events(created_at) WHERE expanded=FALSE''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_hot_order_deliveries_pending_idx
        ON saas_hot_order_deliveries(channel,updated_at) WHERE status='pending' ''')


def enqueue_hot_order_notice(cur, tenant: str, order_id: str, event_id: str) -> None:
    cur.execute('''INSERT INTO saas_hot_order_events (tenant_id,id,order_id,order_version)
        SELECT tenant_id,%s::uuid,id,updated_at FROM saas_hot_orders
        WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='open'
        ON CONFLICT (tenant_id,id) DO NOTHING''', (str(event_id),tenant,order_id))
