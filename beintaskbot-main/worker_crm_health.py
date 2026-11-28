"""Read-only CRM readiness and bounded, parameter-free PostgreSQL lock evidence."""
import asyncio
import time

from aiohttp import web
import psycopg

from tenant_platform_core import _database_url
from worker_crm_capacity import WorkPool

_probe_pool = WorkPool('crm-readiness', 1, 1, 4, 1000)


def _connection():
    return psycopg.connect(_database_url(), connect_timeout=2, application_name='crm-witness',
                           options='-c default_transaction_read_only=on -c lock_timeout=500 -c statement_timeout=1000')


def _crm_read():
    # Resolve the actual membership and dialog tables and acquire their read locks.
    # The planner returns zero rows; identities and message contents never leave PostgreSQL.
    with _connection() as conn:
        conn.execute("""SELECT t.modules, m.tenant_id, c.conversation_id FROM saas_tenants t
                        JOIN saas_tenant_members m ON m.tenant_id=t.id
                        JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id LIMIT 0""")


def readiness_handler():
    cached_until = 0
    status = 503

    pending = None

    async def refresh():
        nonlocal cached_until, status
        try:
            await _probe_pool.run(_crm_read)
        except Exception:
            status = 503
        else:
            status = 200
        cached_until = time.monotonic() + (5 if status == 200 else 2)

    async def handle(_request):
        nonlocal pending
        if time.monotonic() >= cached_until:
            if pending is None or pending.done():
                pending = asyncio.create_task(refresh())
            await asyncio.shield(pending)
        return web.Response(status=status, text='ready' if status == 200 else 'unavailable',
                            headers={'Cache-Control':'no-store'})
    return handle


_LOCKS = """
WITH waiting AS MATERIALIZED (
    SELECT pid, wait_event, pg_blocking_pids(pid) AS holders FROM pg_stat_activity
    WHERE datname=current_database() AND wait_event_type='Lock'
    ORDER BY query_start LIMIT 8
)
SELECT w.pid, w.wait_event, b.pid, b.backend_xid::text,
       floor(extract(epoch FROM clock_timestamp()-b.xact_start))::bigint,
       CASE WHEN b.state='idle in transaction' THEN 'idle_in_transaction'
            WHEN b.state='active' THEN 'active' ELSE 'other' END,
       CASE WHEN b.application_name IN ('crm-sync','crm-session','crm-avatar-auth','crm-avatar',
                                        'crm-dialog','crm-migration','crm-connect','crm-record-read','crm-legacy-access','crm-legacy-deal') THEN b.application_name ELSE 'other' END,
       CASE upper(split_part(ltrim(b.query),' ',1))
            WHEN 'SELECT' THEN 'select' WHEN 'UPDATE' THEN 'update' WHEN 'INSERT' THEN 'insert'
            WHEN 'ALTER' THEN 'alter' WHEN 'CREATE' THEN 'create' ELSE 'other' END
FROM waiting w CROSS JOIN LATERAL unnest(w.holders) h(pid)
JOIN pg_stat_activity b ON b.pid=h.pid ORDER BY w.pid,b.pid LIMIT 12
"""


def lock_snapshot():
    with _connection() as conn:
        return [dict(zip(('waiter_pid','lock_type','holder_pid','holder_xid','transaction_s',
                          'state','component','command'), row)) for row in conn.execute(_LOCKS)]
