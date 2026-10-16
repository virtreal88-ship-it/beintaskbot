"""Fail-fast AI Agent database preparation and worker startup."""
import asyncio
import os
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

    _fernet()
    with _connect() as conn:
        ensure_agents_schema(conn)
        ensure_chat_ai_schema(conn)


async def start_worker(app, worker, kommo_request, logger, wait_slot,
                       *, prepare_database_first=False,
                       prepare=prepare_database) -> None:
    """Optionally migrate an isolated staging DB before the worker can run."""
    if prepare_database_first:
        await asyncio.to_thread(_prepare_database_with_retry, prepare)
    probe_tenant = str(os.environ.get('AI_AGENTS_OPENAI_PROBE_TENANT_ID') or '').strip()
    if probe_tenant:
        try:
            from tenant_ai_openai_diagnostics import run_probe
            await asyncio.to_thread(run_probe, probe_tenant, logger)
        except Exception:
            # Do not prevent the queue worker from starting if diagnostics fail.
            logger.error('AI OpenAI access probe could not complete for the explicitly configured tenant')
    app["tenant_ai_agent_worker"] = asyncio.create_task(
        worker(kommo_request, logger, wait_slot), name="tenant-ai-agent-worker")
