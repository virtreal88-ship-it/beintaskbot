"""Fail-fast AI Agent database preparation and worker startup."""
import asyncio
from contextlib import closing
import logging
import os
import time

from aiohttp.web import AppKey


_RETRYABLE_MIGRATION_SQLSTATES = {"40P01", "40001", "55P03", "25P04"}
_STARTUP_SCHEMA_LOCK='tenant-service-startup-schema'
_KOMMO_BINDING_PROVISION_ENV='BEINSYSTEMS_DIALOG_COMPANY_BINDINGS_PROVISION_ON_STARTUP'
_HISTORY_MERGE_CLEANUP_SECONDS = 1.0


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
        _provision_dialog_duplicates(conn)
        if os.environ.get('DIALOG_HISTORY_MERGE_ENABLED') == '1':
            try:
                from tenant_dialog_history_merge_jobs import enabled as history_merge_enabled, provision as provision_history_merge_jobs
                if history_merge_enabled():
                    with conn.transaction(), conn.cursor() as cur:
                        provision_history_merge_jobs(cur)
            except Exception:
                logging.getLogger(__name__).warning('Dialogue history merge schema preflight unavailable')
        # Opt-in only, on the dedicated startup connection. This fresh
        # transaction inherits the existing startup lock and timeout budget.
        # Closing the connection rolls back any partial template DDL.
        if os.environ.get('DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP', '').lower() == 'true':
            _provision_reply_template_schema(conn)
        elif os.environ.get('DIALOG_REPLY_TEMPLATES_OBSERVE_ON_STARTUP', '').lower() == 'true':
            _observe_reply_template_schema(conn)
        if os.environ.get(_KOMMO_BINDING_PROVISION_ENV, '').lower() == 'true':
            _prepare_optional_kommo_dialog_company(conn)
        if os.environ.get('TENANT_PERSONAL_DIALOG_REMINDERS_PROVISION_ON_STARTUP') == '1':
            _provision_personal_reminders(conn)
        if os.environ.get('TENANT_SHARED_DIALOG_REMINDERS_PROVISION_ON_STARTUP') == '1':
            _provision_shared_reminders(conn)
        from tenant_dialog_presence import STATUS_PROVISION_ENV
        if os.environ.get(STATUS_PROVISION_ENV, '').strip().lower() == 'true':
            _provision_employee_status(conn)


def _provision_dialog_duplicates(conn):
    """Optional UI schema under the bounded startup lock; never blocks boot."""
    try:
        from tenant_dialog_duplicate_actions import prepare_schema
        with conn.transaction():
            prepare_schema(conn)
    except Exception:
        logging.getLogger(__name__).warning('Dialogue duplicates schema preflight: unverified')


def _provision_reply_template_schema(conn):
    from tenant_beinsystems_operator_access import configured_workspace_id
    from tenant_dialog_reply_templates import provision_schema
    logger = logging.getLogger(__name__)
    if not configured_workspace_id():
        logger.warning('Reply template schema preflight: workspace_not_configured')
        return
    try:
        # The optional transaction starts after every base commit, under the
        # existing session lock and budget. Failure never cuts off normal boot.
        with conn.transaction(), conn.cursor() as cur:
            provision_schema(cur)
    except Exception:
        logger.warning('Reply template schema preflight: unverified')
    else:
        logger.info('Reply template schema preflight: ready')


def _observe_reply_template_schema(conn):
    """One-time, read-only catalog observation; never fetch template content."""
    from tenant_beinsystems_operator_access import configured_workspace_id
    from tenant_dialog_reply_templates import _schema_ready, verify_provisioned_schema
    outcome = 'workspace_not_configured'
    if configured_workspace_id():
        try:
            # A fresh bounded transaction under the existing startup lock.
            # Never allow verification to write, and roll back before continuing.
            with conn.transaction(), conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                if not _schema_ready(cur):
                    outcome = 'absent'
                else:
                    verify_provisioned_schema(cur)
                    outcome = 'matching'
        except Exception:
            # A failed observation is not evidence of an absent or safe schema.
            # Do not emit detailed exceptions, SQL data, or identifiers.
            outcome = 'unverified'
    logging.getLogger(__name__).info('Reply template schema observation: %s', outcome)


def _prepare_optional_kommo_dialog_company(conn):
    """Bounded by the retained startup lock; unavailable Kommo must not block boot."""
    from tenant_beinsystems_operator_access import configured_workspace_id
    from tenant_dialog_company_binding import provision_kommo
    ready=False
    try:
        if configured_workspace_id():
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='4000ms'")
                provision_kommo(cur)
            conn.commit()
            ready=True
    except Exception:
        # Closing the dedicated startup connection handles a terminated backend.
        try:
            if not conn.closed:
                conn.rollback()
        except Exception:
            pass
    log=logging.getLogger(__name__)
    if ready:
        log.info('Optional Kommo dialogue company schema ready')
    else:
        log.warning('Optional Kommo dialogue company schema unverified')


def _provision_personal_reminders(conn):
    from tenant_beinsystems_operator_access import configured_workspace_id
    from tenant_personal_dialog_reminders import provision_schema
    logger = logging.getLogger(__name__)
    if not configured_workspace_id():
        logger.warning('Personal reminders schema preflight: workspace_not_configured')
        return
    try:
        # Independent of base schema commits; the startup lock and timeouts
        # remain in force. An unsuccessful opt-in must not take the app down.
        with conn.transaction(), conn.cursor() as cur:
            provision_schema(cur)
    except Exception:
        logger.warning('Personal reminders schema preflight: unverified')
    else:
        logger.info('Personal reminders schema preflight: ready')


def _provision_shared_reminders(conn):
    from tenant_beinsystems_operator_access import configured_workspace_id
    from tenant_dialog_presence import work_status_routing_sources
    from tenant_shared_dialog_reminders import provision_schema
    logger = logging.getLogger(__name__)
    if not work_status_routing_sources(configured_workspace_id()):
        logger.warning('Shared reminders schema preflight: workspace_not_configured')
        return
    try:
        with conn.transaction(), conn.cursor() as cur:
            provision_schema(cur)
    except Exception:
        logger.warning('Shared reminders schema preflight: unverified')
    else:
        logger.info('Shared reminders schema preflight: ready')


def _provision_employee_status(conn):
    from tenant_dialog_presence import provision_status_schema
    logger = logging.getLogger(__name__)
    try:
        # Last optional operation under the existing startup session lock.
        # A failed transaction (including a terminated connection) cannot
        # interrupt the base schema already committed before this point.
        with conn.transaction(), conn.cursor() as cur:
            created = provision_status_schema(cur)
    except Exception:
        logger.warning('Employee status schema preflight: unverified')
    else:
        logger.info('Employee status schema preflight: %s', 'created' if created else 'ready')


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


class _HistoryMergeWorkerState:
    def __init__(self):
        self.task = None
        self.stopping = False


_HISTORY_MERGE_WORKER = AppKey('tenant_history_merge_worker', _HistoryMergeWorkerState)


def install_history_merge_worker_cleanup(app):
    """Register before AppRunner.setup(); deferred worker startup may run later."""
    if _HISTORY_MERGE_WORKER not in app:
        app.on_cleanup.append(_cleanup_history_merge_worker)
        app[_HISTORY_MERGE_WORKER] = _HistoryMergeWorkerState()


def _history_merge_worker_done(task):
    # Observe any late exception, without including source identifiers or values.
    if not task.cancelled() and (error := task.exception()) is not None:
        logging.getLogger(__name__).warning('Dialogue history merge worker exited (%s)', type(error).__name__)


async def _cleanup_history_merge_worker(app):
    state = app[_HISTORY_MERGE_WORKER]
    if state.stopping:
        return
    state.stopping = True  # Fence deferred readiness before the first await.
    task = state.task
    if task is None or task.done():
        return
    # No job mutation from shutdown. Cancelling a provider wait leaves its
    # durable lease recoverable; an already-started finite DB thread may finish.
    task.cancel()
    # wait_for waits for cancellation acknowledgement and can overrun. This
    # timeout bounds the aiohttp cleanup even if cancellation is delayed.
    _, pending = await asyncio.wait({task}, timeout=_HISTORY_MERGE_CLEANUP_SECONDS)
    if pending:
        logging.getLogger(__name__).warning('Dialogue history merge worker cleanup reached its time limit')


def _start_history_merge_worker(app):
    state = app.get(_HISTORY_MERGE_WORKER)
    if state is None or state.stopping or state.task is not None:
        return
    from tenant_dialog_history_merge_jobs import enabled, worker
    if enabled():
        state.task = asyncio.create_task(worker(), name='tenant-history-merge-worker')
        state.task.add_done_callback(_history_merge_worker_done)


async def start_worker(app, worker, kommo_request, logger, wait_slot,
                       *, prepare_database_first=False,
                       simulation_worker=None, prepare=prepare_database,require_ready=require_database_ready) -> None:
    """Optionally migrate an isolated staging DB before the worker can run."""
    if prepare_database_first:
        await asyncio.to_thread(_prepare_database_with_retry, prepare)
    await asyncio.to_thread(require_ready)
    app["tenant_ai_agent_worker"] = asyncio.create_task(
        worker(kommo_request, logger, wait_slot), name="tenant-ai-agent-worker")
    _start_history_merge_worker(app)
    if simulation_worker is not None:
        from tenant_ai_agent_runtime_flags import enabled as runtime_flag
        if runtime_flag('AI_SIMULATION_ENABLED'):
            app['tenant_ai_simulation_worker'] = asyncio.create_task(
                simulation_worker(logger), name='tenant-ai-simulation-worker')
