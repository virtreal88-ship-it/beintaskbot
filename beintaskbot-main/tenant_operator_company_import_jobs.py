"""Durable single-confirmation Operator company import with atomic batch cursors."""
from __future__ import annotations

import asyncio

import tenant_beinsystems_operator as operator

TABLE = "saas_operator_company_import_jobs"
PUBLIC = """id::text, state, received, total, processed, created, reused, conflicts,
            limit_reached, expired_at, error_code"""
# Each committed slice is a small independent CRM transaction. No provider calls
# are made by the worker; it only consumes the source-confirmed account IDs.
BATCH_SIZE = 50


def migrate_jobs(cur):
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      actor bigint NOT NULL, scope text NOT NULL, accounts text[] NOT NULL,
      state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','completed','failed')),
      received int NOT NULL CHECK (received BETWEEN 1 AND 1000),
      total int NOT NULL CHECK (total BETWEEN 1 AND received),
      processed int NOT NULL DEFAULT 0 CHECK (processed BETWEEN 0 AND total),
      created int NOT NULL DEFAULT 0, reused int NOT NULL DEFAULT 0,
      conflicts int NOT NULL DEFAULT 0, limit_reached boolean NOT NULL DEFAULT false,
      attempts int NOT NULL DEFAULT 0, error_code text,
      expired_at timestamptz NOT NULL DEFAULT now()+interval '30 minutes',
      next_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK (state<>'queued' OR cardinality(accounts)=total))""")
    cur.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS operator_company_import_job_active
      ON {TABLE}(tenant_id) WHERE state='queued'""")
    cur.execute(f"""CREATE INDEX IF NOT EXISTS operator_company_import_job_ready
      ON {TABLE}(next_at,id) WHERE state='queued'""")


def _public(row):
    if not row:
        return None
    return {key: value for key, value in row.items() if key != "expired_at"}


def confirmed_job(tenant, actor, job_id, scope):
    with operator._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT {PUBLIC} FROM {TABLE}
              WHERE tenant_id=%s::uuid AND actor=%s AND id=%s::uuid AND scope=%s""",
              (tenant, actor, job_id, scope))
            return _public(cur.fetchone())


def confirm_job(conn, cur, tenant, actor, claims, digest, plan, report):
    # Caller already holds the tenant's name namespace, the owner and source
    # fences. Persist the scope and immutable originally-approved creation set.
    cur.execute(f"""SELECT {PUBLIC} FROM {TABLE}
        WHERE tenant_id=%s::uuid AND actor=%s AND id=%s::uuid AND scope=%s""",
        (tenant, actor, claims["nonce"], claims["scope"]))
    existing = cur.fetchone()
    if existing:
        return _public(existing)
    if claims["digest"] != digest:
        raise operator.OperatorError("Выборка или план изменились. Откройте новый предпросмотр.", 409)
    accounts = [row["account"] for row in plan if row["action"] == "create"]
    if not accounts:
        raise operator.OperatorError("Нет новых компаний для импорта.", 409)

    cur.execute(f"""INSERT INTO {TABLE}
      (id,tenant_id,actor,scope,accounts,received,total,reused,conflicts,limit_reached)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT DO NOTHING RETURNING {PUBLIC}""",
      (claims["nonce"],tenant,actor,claims["scope"],accounts,report["received"],len(accounts),
       report["already"],len(report["conflicts"]),report["limit_reached"]))
    row = cur.fetchone()
    if not row:
        raise operator.OperatorError("Импорт уже выполняется. Дождитесь завершения.", 409)
    conn.commit()
    return _public(row)


def latest_job(profile):
    from tenant_beinsystems_operator_company_import import _owner
    tenant = operator._tenant(profile)
    actor = int(profile["telegram_id"])
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            _owner(profile, operator._current_profile(cur, tenant, actor))
            cur.execute(f"""SELECT {PUBLIC} FROM {TABLE}
              WHERE tenant_id=%s::uuid AND actor=%s ORDER BY updated_at DESC,id DESC LIMIT 1""",
              (tenant, actor))
            return _public(cur.fetchone())


def lock_job(cur, job):
    cur.execute(f"""SELECT 1 FROM {TABLE}
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND actor=%s AND processed=%s
        AND scope=%s AND state='queued' AND expired_at>clock_timestamp() FOR UPDATE""",
      (job["tenant"],job["id"],job["actor"],job["processed"],job["scope"]))
    return bool(cur.fetchone())


def complete_batch(cur, job, count, created, reused, conflicts):
    # Same transaction as company and mapping writes. A process crash before
    # commit does not lose the slice, and executor overlap cannot advance it twice.
    cur.execute(f"""UPDATE {TABLE} SET
      processed=processed+%s, created=created+%s, reused=reused+%s, conflicts=conflicts+%s,
      state=CASE WHEN processed+%s=total THEN 'completed' ELSE 'queued' END,
      accounts=CASE WHEN processed+%s=total THEN '{{}}'::text[] ELSE accounts END,
      updated_at=now(), next_at=now(), attempts=0
      WHERE id=%s::uuid AND tenant_id=%s::uuid""",
      (count,created,reused,conflicts,count,count,job["id"],job["tenant"]))


def _next_job():
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            # Clear no business records; only drop the no-longer-needed pending
            # snapshot IDs and retain aggregate progress for the owner.
            cur.execute(f"""UPDATE {TABLE} SET state='failed',error_code='expired',
              accounts='{{}}'::text[],updated_at=now() WHERE state='queued' AND expired_at<=now()""")
            cur.execute(f"""SELECT id::text,tenant_id::text AS tenant,actor,scope,processed,
              accounts[(processed+1):(processed+%s)] AS batch
              FROM {TABLE} WHERE state='queued' AND next_at<=now()
              ORDER BY next_at,id LIMIT 1""",(BATCH_SIZE,))
            return cur.fetchone()


def _retry_or_fail(job, code, *, retry):
    with operator._connect() as conn, conn.cursor() as cur:
        cur.execute(f"""UPDATE {TABLE} SET attempts=attempts+1,
          next_at=now()+interval '15 seconds',
          state=CASE WHEN %s AND attempts<2 THEN 'queued' ELSE 'failed' END,
          accounts=CASE WHEN %s AND attempts<2 THEN accounts ELSE '{{}}'::text[] END,
          error_code=CASE WHEN %s AND attempts<2 THEN NULL ELSE %s END, updated_at=now()
          WHERE state='queued' AND id=%s::uuid AND tenant_id=%s::uuid AND processed=%s""",
          (retry,retry,retry,code,job["id"],job["tenant"],job["processed"]))


def run_once():
    from tenant_beinsystems_operator_company_import import (
        _apply_company_import, _first_batch_digest, _first_batch_scope, _selected_source_credentials,
    )
    job = _next_job()
    if not job:
        return
    profile = {"tenant_id":job["tenant"], "telegram_id":job["actor"], "role":"owner", "active":True}
    try:
        tenant, actor, source, credentials = _selected_source_credentials(profile)
        scope = _first_batch_digest(["catalogue_background",source,_first_batch_scope(tenant,actor,credentials)])
        if scope != job["scope"]:
            raise operator.OperatorError("Подключение Operator изменилось.", 409)
        _apply_company_import(profile,tenant,actor,[{"account":account} for account in job["batch"]],
                              credentials,first_batch=True,catalogue_source=source,job=job)
    except operator.OperatorError:
        _retry_or_fail(job, "access_or_plan_changed", retry=False)
    except Exception:
        # Keep provider/private identifiers out of logs; after bounded retries
        # the owner can preview the remainder without recreating prior batches.
        _retry_or_fail(job, "batch_failed", retry=True)


async def deliver(logger):
    if operator.first_company_batch_confirm_enabled():
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            logger.error("Operator company import worker unavailable")
