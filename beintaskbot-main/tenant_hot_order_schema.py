"""Additive, isolated SaaS queue; never migrates the legacy hot_orders table."""
from threading import Lock
from tenant_platform import _ensure_schema
from tenant_hot_order_notice_schema import migrate_hot_order_notices

_ready = False
_lock = Lock()


def ensure_hot_order_schema(conn) -> None:
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-hot-order-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_hot_orders (
                tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                id UUID NOT NULL,
                request_id UUID NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open','claimed','cancelled')),
                service_id TEXT NOT NULL,
                client_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','urgent')),
                created_by BIGINT NOT NULL,
                claimed_by BIGINT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                claimed_at TIMESTAMPTZ NULL,
                PRIMARY KEY (tenant_id,id),
                UNIQUE (tenant_id,created_by,request_id)
            )''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_hot_orders_list_idx
                ON saas_hot_orders(tenant_id,status,service_id,created_at DESC,id)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_hot_orders_worker_idx
                ON saas_hot_orders(tenant_id,claimed_by,created_at DESC,id)''')
            migrate_hot_order_notices(cur)
        conn.commit()
        _ready = True
