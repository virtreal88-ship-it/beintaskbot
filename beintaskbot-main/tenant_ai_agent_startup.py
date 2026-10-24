"""Fail-fast AI Agent database preparation and worker startup."""
import asyncio
import time


_RETRYABLE_MIGRATION_SQLSTATES = {"40P01", "40001"}


def _retryable_migration_error(exc: Exception) -> bool:
    """Only retry PostgreSQL deadlocks/serialization conflicts."""
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

    _fernet()
    with _connect() as conn:
        ensure_agents_schema(conn)
        ensure_chat_ai_schema(conn)
        ensure_simulation_schema(conn)
        ensure_drafts_schema(conn)
        ensure_approvals_schema(conn)
        ensure_observations_schema(conn)
        ensure_apply_schema(conn)


async def start_worker(app, worker, kommo_request, logger, wait_slot,
                       *, prepare_database_first=False,
                       simulation_worker=None, prepare=prepare_database) -> None:
    """Optionally migrate an isolated staging DB before the worker can run."""
    if prepare_database_first:
        await asyncio.to_thread(_prepare_database_with_retry, prepare)
    app["tenant_ai_agent_worker"] = asyncio.create_task(
        worker(kommo_request, logger, wait_slot), name="tenant-ai-agent-worker")
    if simulation_worker is not None:
        from tenant_ai_agent_runtime_flags import enabled as runtime_flag
        if runtime_flag('AI_SIMULATION_ENABLED'):
            app['tenant_ai_simulation_worker'] = asyncio.create_task(
                simulation_worker(logger), name='tenant-ai-simulation-worker')
