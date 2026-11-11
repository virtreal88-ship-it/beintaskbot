"""Fail-fast AI Agent database preparation and worker startup."""
import asyncio
from contextlib import closing
import time


_RETRYABLE_MIGRATION_SQLSTATES = {"40P01", "40001", "55P03", "25P04"}
_STARTUP_SCHEMA_LOCK='tenant-service-startup-schema'


def _retryable_migration_error(exc: Exception) -> bool:
    """Only retry known PostgreSQL migration conflicts/transaction expiry."""
    return getattr(exc, "sqlstate", None) in _RETRYABLE_MIGRATION_SQLSTATES


def _prepare_database_with_retry(prepare, *, attempts=5, delay_seconds=1):
    """Retry additive, idempotent schema setup after transient DB conflicts."""
    for attempt in range(attempts):
        try:
            prepare()
            return
        except Exception as exc:
            if not _retryable_migration_error(exc) or attempt + 1 >= attempts:
                raise
            time.sleep(delay_seconds * (attempt + 1))


def prepare_database() -> None:
    """Apply the tenant, Chat AI and AI Agents schemas before serving work."""
    from tenant_platform_core import _connect, _fernet
    from tenant_ai_agents_schema import ensure_schema as ensure_agents_schema
    from tenant_chat_ai_schema import ensure_schema as ensure_chat_ai_schema
    from tenant_ai_simulation_schema import ensure_schema as ensure_simulation_schema
    from tenant_ai_administrator_drafts import ensure_schema as ensure_drafts_schema
    from tenant_ai_administrator_approvals import ensure_schema as ensure_approvals_schema
    from tenant_ai_administrator_observations import ensure_schema as ensure_observations_schema
    from tenant_ai_administrator_apply import ensure_schema as ensure_apply_schema
    from tenant_connect_health import prepare_database as prepare_connect_health

    _fernet()
    # This is a dedicated, non-pooled startup connection. Closing it releases
    # the session lock and rolls back any unfinished transaction, including
    # after transaction_timeout has terminated the backend. Cleanup must not
    # issue rollback/unlock SQL that could hide the original SQLSTATE.
    with closing(_connect()) as conn:
        if conn.info.server_version < 170000:
            raise RuntimeError('Startup migrations require PostgreSQL 17 or newer for bounded transactions.')
        # Nested legacy initializers commit. A session lock deliberately
        # survives those boundaries and serializes complete cold startups.
        with conn.cursor() as cur:
            # lock_timeout alone restarts for every acquisition. A session
            # transaction_timeout additionally bounds time holding earlier
            # locks across later statements/waits, including after nested
            # commits. Neither setting applies to serving connections.
            cur.execute("SET transaction_timeout='5000ms'")
            cur.execute("SET lock_timeout='2000ms'")
            cur.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(_STARTUP_SCHEMA_LOCK,))
        conn.commit()
        ensure_agents_schema(conn)
        ensure_pilot_case_schema(conn)
        ensure_chat_ai_schema(conn)
        ensure_simulation_schema(conn)
        ensure_drafts_schema(conn)
        ensure_approvals_schema(conn)
        ensure_observations_schema(conn)
        ensure_apply_schema(conn)
        prepare_connect_health(conn)
        conn.commit()
        from tenant_dialog_consultations import require_schema
        require_schema(conn)
        conn.commit()


def ensure_pilot_case_schema(conn) -> None:
    """Idempotently install the additive consultation/follow-up schema."""
    from tenant_dialog_consultations import schema as ensure_schema
    ensure_schema(conn)


def require_database_ready() -> None:
    """Direct HTTP/worker entry points verify committed shape without repair."""
    from tenant_platform_core import _connect
    from tenant_ai_agents_schema import _schema_current
    from tenant_connect_health import require_schema as require_connect_health
    from tenant_dialog_consultations import require_schema
    with _connect() as conn:
        if not _schema_current(conn):raise RuntimeError('Tenant startup schema preparation is required.')
        require_connect_health(conn)
        require_schema(conn)


async def start_worker(app, worker, kommo_request, logger, wait_slot,
                       *, prepare_database_first=False,
                       simulation_worker=None, prepare=prepare_database,require_ready=require_database_ready) -> None:
    """Optionally migrate an isolated staging DB before the worker can run."""
    if prepare_database_first:
        await asyncio.to_thread(_prepare_database_with_retry, prepare)
    await asyncio.to_thread(require_ready)
    app["tenant_ai_agent_worker"] = asyncio.create_task(
        worker(kommo_request, logger, wait_slot), name="tenant-ai-agent-worker")
    if simulation_worker is not None:
        from tenant_ai_agent_runtime_flags import enabled as runtime_flag
        if runtime_flag('AI_SIMULATION_ENABLED'):
            app['tenant_ai_simulation_worker'] = asyncio.create_task(
                simulation_worker(logger), name='tenant-ai-simulation-worker')
