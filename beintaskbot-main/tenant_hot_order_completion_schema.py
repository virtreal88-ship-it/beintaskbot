"""Additive completion fields and command receipts; existing rows retained."""


def migrate_hot_order_completion(cur) -> None:
    cur.execute("ALTER TABLE saas_hot_orders ADD COLUMN IF NOT EXISTS result_text TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_hot_orders ADD COLUMN IF NOT EXISTS review_reason TEXT NOT NULL DEFAULT ''")
    cur.execute('ALTER TABLE saas_hot_orders ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL')
    cur.execute('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='saas_hot_orders'::regclass
            AND conname='saas_hot_orders_status_check' AND pg_get_constraintdef(oid) NOT LIKE '%submitted%') THEN
            ALTER TABLE saas_hot_orders DROP CONSTRAINT saas_hot_orders_status_check;
            ALTER TABLE saas_hot_orders ADD CONSTRAINT saas_hot_orders_status_check
                CHECK (status IN ('open','claimed','cancelled','submitted','completed'));
        END IF;
    END $$''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_hot_order_commands (
        tenant_id UUID NOT NULL,
        actor_id BIGINT NOT NULL,
        request_id UUID NOT NULL,
        order_id UUID NOT NULL,
        fingerprint TEXT NOT NULL,
        result JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id,actor_id,request_id),
        FOREIGN KEY (tenant_id,order_id) REFERENCES saas_hot_orders(tenant_id,id) ON DELETE CASCADE
    )''')
    cur.execute("ALTER TABLE saas_hot_order_events ADD COLUMN IF NOT EXISTS event TEXT NOT NULL DEFAULT 'hot_order_available'")
    cur.execute('ALTER TABLE saas_hot_order_events ADD COLUMN IF NOT EXISTS target_recipient BIGINT NULL')
    cur.execute("CREATE INDEX IF NOT EXISTS saas_hot_orders_review_idx ON saas_hot_orders (tenant_id,created_at DESC,id DESC) WHERE status='submitted'")


def enqueue_completion_notice(cur, tenant: str, order_id: str, event_id: str, event: str, target: int | None) -> None:
    cur.execute('''INSERT INTO saas_hot_order_events (tenant_id,id,order_id,order_version,event,target_recipient)
        SELECT tenant_id,%s::uuid,id,updated_at,%s,%s FROM saas_hot_orders
        WHERE tenant_id=%s::uuid AND id=%s::uuid ON CONFLICT (tenant_id,id) DO NOTHING''',
        (event_id,event,target,tenant,order_id))
