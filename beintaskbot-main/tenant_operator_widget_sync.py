"""Incremental Operator widget ingestion. No customer sends or Connect/WhatsApp rows."""

import asyncio
import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import configured_workspace_id
from tenant_operator_widget import _can_reply, _hash, _messages, _reconcile, _post, _routes, _string, PAGE

PROVIDER = "operator_widget"
SOURCE = "Чат сайта BeinSystems"


def enabled():
    return (
        os.getenv("BEINSYSTEMS_WIDGET_QUEUE_ENABLED", "").lower() in {"true", "primary"}
        and configured_workspace_id()
    )


def stamp(value):
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("Asia/Baku"))).astimezone(timezone.utc)


def scope(row):
    return (str(row["tenant_id"]), str(row["instance"]), row["chat_id"], row["identity"])


KEY = "tenant_id=%s AND instance=%s AND chat_id=%s AND identity=%s"


def snapshot(cur, tenant, credentials, routes, login):
    instance = credentials["connection_instance_id"]
    cur.execute(
        """INSERT INTO saas_widget_sources(tenant_id,instance,generation,refreshed_at)
        VALUES(%s,%s,%s,now()) ON CONFLICT(tenant_id,instance) DO UPDATE
        SET generation=excluded.generation,refreshed_at=excluded.refreshed_at RETURNING started_at""",
        (tenant, instance, credentials["generation"]),
    )
    cutoff = stamp(cur.fetchone()["started_at"]).replace(microsecond=0)
    cur.execute(
        "SELECT chat_id,identity FROM saas_widget_dialogs WHERE tenant_id=%s AND instance=%s AND latest_in IS NOT NULL",
        (tenant, instance),
    )
    admitted = {(r["chat_id"], r["identity"]) for r in cur.fetchall()}
    cur.execute(
        "UPDATE saas_widget_dialogs SET visible=false WHERE tenant_id=%s AND instance=%s AND visible",
        (tenant, instance),
    )
    for chat, (row, rev) in routes.items():
        try:
            if stamp(row.get("updated_at")) < cutoff and (chat, _hash([row["client_id"]])) not in admitted:
                continue
        except (ValueError, TypeError):
            # Without an activity timestamp only the newest page can establish freshness.
            pass
        identity = _hash([row["client_id"]])
        account = _string(row.get("account"), 240)
        if not account or account != account.strip() or not account.isprintable():
            account = None
        cur.execute(
            """INSERT INTO saas_widget_dialogs
            (tenant_id,instance,chat_id,identity,revision,title,operator_account,upstream_at,writable,open_operator_login,visible)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT(tenant_id,instance,chat_id,identity) DO UPDATE SET
            revision=excluded.revision,title=excluded.title,upstream_at=excluded.upstream_at,
            operator_account=excluded.operator_account,writable=excluded.writable,
            open_operator_login=excluded.open_operator_login,visible=true""",
            (
                tenant,
                instance,
                chat,
                identity,
                rev,
                str(row.get("client_name") or row["client_id"])[:256],
                account,
                str(row.get("updated_at") or ""),
                bool(_can_reply(row, login)),
                (_string(row.get("operator"), 240) or None) if row.get("status") == "open" else None,
            ),
        )


def ingest(cur, route, messages, full):
    key = scope(route)
    cur.execute("SELECT * FROM saas_widget_dialogs WHERE " + KEY + " AND visible FOR UPDATE", key)
    current = cur.fetchone()
    if not current or any(
        current[k] != route[k] for k in ("revision", "high_id", "before_id", "upstream_at")
    ):
        return
    # Resume an interrupted traversal from its durable page boundary. Newly arriving
    # messages are picked up by the next traversal from the head.
    cur.execute("SELECT started_at FROM saas_widget_sources WHERE tenant_id=%s AND instance=%s", key[:2])
    cutoff = stamp(cur.fetchone()["started_at"])
    reached_cutoff = False
    ids = []
    for msg in messages:
        mid = int(msg["id"])
        if (route["before_id"] and mid >= route["before_id"]) or mid <= route["high_id"]:
            continue
        at = stamp(msg["created_at"])
        ids.append(mid)
        if at <= cutoff:
            reached_cutoff = True
            continue
        cur.execute(
            """INSERT INTO saas_widget_messages
            (tenant_id,instance,chat_id,identity,mid,sender,author,body,attachment,at,fresh)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT DO NOTHING RETURNING fresh""",
            (*key, mid, msg["sender"], msg["name"], msg["text"], msg["attachment"], at),
        )
        inserted = cur.fetchone()
        if inserted and msg["sender"] == "client":
            cur.execute(
                """UPDATE saas_widget_dialogs SET latest_in=%s,
                state=CASE WHEN state='accepted' THEN state ELSE 'waiting' END,
                operator_id=CASE WHEN state='accepted' THEN operator_id END,version=version+1
                WHERE """
                + KEY
                + " AND COALESCE(latest_in,0)<%s AND (closed_at IS NULL OR closed_at<%s)",
                (mid, *key, mid, at),
            )
        cur.execute(
            "UPDATE saas_widget_dialogs SET latest_at=GREATEST(latest_at,%s) WHERE " + KEY, (at, *key)
        )
    head = route["active_snapshot"] or max(ids, default=route["high_id"])
    more = bool(full and ids and not reached_cutoff and min(int(m["id"]) for m in messages) > route["high_id"])
    cur.execute(
        """UPDATE saas_widget_dialogs SET checked_at=now(), before_id=%s,active_snapshot=%s,
        high_id=%s, fetched_at=CASE WHEN %s THEN fetched_at ELSE %s END WHERE """
        + KEY,
        (
            min(ids) if more else None,
            head if more else None,
            route["high_id"] if more else head,
            more,
            route["upstream_at"] if not route["before_id"] else None,
            *key,
        ),
    )


def _safe_failure(exc):
    if isinstance(exc, operator.OperatorError):
        upstream = getattr(exc, "upstream_http_status", None)
        if upstream == 401:
            return "operator_http_401"
        if upstream == 403:
            return "operator_http_403"
        if exc.status == 502:
            return "operator_invalid_response"
        return "operator_error"
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate in {"42P01", "42703", "42501", "08006", "57014", "55P03"}:
        return "postgres_" + sqlstate
    return "internal_error"


def sync_once(tenant, *, stage=None):
    if stage is None:
        stage = lambda _: None
    stage("credentials")
    credentials = operator._credentials(tenant)
    instance = credentials["connection_instance_id"]
    # A session-level lock serializes workers without holding a transaction during REST calls.
    stage("database_lock")
    with operator._connect() as db, db.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held", ("widget-sync/" + tenant,))
        if not cur.fetchone()["held"]:
            return
        db.commit()
        try:
            stage("active_chats")
            routes = _routes(_post("/chat/getActiveChats", {}, credentials), credentials)
            stage("snapshot")
            connection = operator._operator_row(cur, tenant)
            if (
                not connection
                or connection["status"] != "connected"
                or operator._link_connection_scope(connection) != (credentials["generation"], instance)
            ):
                return
            snapshot(cur, tenant, credentials, routes, operator._connected_login(connection, tenant))
            db.commit()
            cur.execute(
                """SELECT * FROM saas_widget_dialogs WHERE tenant_id=%s AND instance=%s AND visible
                AND (checked_at IS NULL OR before_id IS NOT NULL OR fetched_at IS DISTINCT FROM upstream_at
                     OR checked_at < now() - CASE WHEN writable THEN interval '45 seconds' ELSE interval '5 minutes' END)
                ORDER BY (fetched_at IS DISTINCT FROM upstream_at OR before_id IS NOT NULL) DESC,
                (checked_at IS NULL) DESC, checked_at NULLS FIRST, upstream_at DESC LIMIT 12""",
                (tenant, instance),
            )
            batch = cur.fetchall()
            db.commit()
            stage("history")
            for route in batch:
                row, _ = routes[route["chat_id"]]
                query = {"chat_id": route["chat_id"], "client_id": row["client_id"], "limit": PAGE}
                if route["before_id"]:
                    query["before_id"] = route["before_id"]
                try:
                    raw = _post("/chat/getMessages", query, credentials)
                    msgs = _messages(raw, route["chat_id"], row["client_id"])
                    if route["before_id"] and any(int(m["id"]) >= route["before_id"] for m in msgs):
                        raise ValueError("Invalid widget pagination")
                    ingest(cur, route, msgs, len(raw) >= PAGE)
                    _reconcile(cur, tenant, route["chat_id"], route["revision"], raw)
                    db.commit()
                except (operator.OperatorError, ValueError):
                    db.rollback()
                    # Other chats keep progressing if one upstream history is unavailable.
                    cur.execute("UPDATE saas_widget_dialogs SET checked_at=now() WHERE " + KEY, scope(route))
                    db.commit()
                    logging.getLogger(__name__).warning("Operator widget history unavailable")
        finally:
            db.rollback()
            cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", ("widget-sync/" + tenant,))


def install(app, logger):
    tenant = enabled()
    if not tenant:
        return

    def check_realtime():
        from tenant_operator_widget_transport import socket_block_reason
        socket_block_reason(operator._credentials(tenant))

    def probe_alternate_realtime():
        from tenant_operator_widget_transport import probe_realtime_origin
        probe_realtime_origin(operator._credentials(tenant))

    async def diagnostic():
        try:
            await asyncio.to_thread(probe_alternate_realtime)
        except Exception:
            logger.warning("Operator alternate realtime auth probe could not start")

    async def run():
        try:
            await asyncio.to_thread(check_realtime)
        except Exception:
            logger.warning("Operator widget realtime check could not start")
        while True:
            current_stage = "start"

            def stage(value):
                nonlocal current_stage
                current_stage = value

            try:
                await asyncio.to_thread(sync_once, tenant, stage=stage)
            except Exception as exc:
                logger.warning("Operator widget queue refresh failed: stage=%s reason=%s",
                               current_stage, _safe_failure(exc))
            await asyncio.sleep(10)

    async def context(_):
        task = asyncio.create_task(run(), name="operator-widget-queue")
        probe = asyncio.create_task(diagnostic(), name="operator-widget-rt-auth-probe")
        try:
            yield
        finally:
            task.cancel()
            probe.cancel()
            await asyncio.gather(task, probe, return_exceptions=True)

    app.cleanup_ctx.append(context)
