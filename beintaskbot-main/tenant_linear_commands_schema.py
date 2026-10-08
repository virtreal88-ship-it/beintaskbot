"""Additive durable command intents. No legacy task/news migrations."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_commands_schema(conn):
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-linear-commands-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_linear_commands (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE RESTRICT,
              actor_id BIGINT NOT NULL, request_id UUID NOT NULL, issue_id UUID NOT NULL,
              fingerprint TEXT NOT NULL, intent JSONB NOT NULL, result JSONB NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ NULL,
              PRIMARY KEY(tenant_id,actor_id,request_id),
              FOREIGN KEY(tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT
            )''')
        conn.commit()
        _ready = True
