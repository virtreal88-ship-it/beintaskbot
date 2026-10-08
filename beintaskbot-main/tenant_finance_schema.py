"""Separate SaaS ledger; no legacy balances or existing migrations modified."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready=False
_lock=Lock()


def ensure_finance_schema(conn) -> None:
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-finance-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_finance_entries (
                tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE RESTRICT,
                id UUID NOT NULL,
                member_id BIGINT NOT NULL,
                actor_id BIGINT NOT NULL,
                request_id UUID NOT NULL,
                fingerprint TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('credit','debit','reverse')),
                amount_minor BIGINT NOT NULL CHECK (amount_minor<>0 AND abs(amount_minor)<=99999999999),
                currency TEXT NOT NULL DEFAULT 'AZN' CHECK (currency='AZN'),
                note TEXT NOT NULL,
                reverses_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id,id),
                UNIQUE (tenant_id,actor_id,request_id),
                UNIQUE (tenant_id,reverses_id),
                FOREIGN KEY (tenant_id,member_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
                FOREIGN KEY (tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
                FOREIGN KEY (tenant_id,reverses_id) REFERENCES saas_finance_entries(tenant_id,id) ON DELETE RESTRICT,
                CHECK ((kind='reverse')=(reverses_id IS NOT NULL)),
                CHECK (kind='reverse' OR (kind='credit' AND amount_minor>0) OR (kind='debit' AND amount_minor<0))
            )''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_finance_member_history_idx
                ON saas_finance_entries(tenant_id,member_id,created_at DESC,id DESC) INCLUDE (amount_minor)''')
        conn.commit()
        _ready=True
