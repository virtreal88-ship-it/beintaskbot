"""Incremental Operator widget ingestion. No customer sends or Connect/WhatsApp rows."""

import asyncio
import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import configured_workspace_id
from tenant_operator_widget import _can_reply, _hash, _messages, _post, _routes, PAGE

PROVIDER = "operator_widget"
SOURCE = "Чат сайта BeinSystems"


def enabled():
    return (
        os.getenv("BEINSYSTEMS_WIDGET_QUEUE_ENABLED", "").lower() in {"true", "primary"}
        and configured_workspace_id()
    )


def stamp(value):
    dt = datetime.fromisoformat(value)
    return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("Asia/Baku"))).astimezone(timezone.utc)


def scope(row):
    return (str(row["tenant_id"]), str(row["instance"]), row["chat_id"], row["identity"])


KEY = "tenant_id=%s AND instance=%s AND chat_id=%s AND identity=%s"


def snapshot(cur, tenant, credentials, routes, login):
    instance = credentials["connection_instance_id"]
    cur.execute(
        """INSERT INTO saas_widget_sources(tenant_id,instance,generation,refreshed_at)
        VALUES(%s,%s,%s,now()) ON CONFLICT(tenant_id,instance) DO UPDATE
        SET generation=excluded.generation,refreshed_at=excluded.refreshed_at""",
        (tenant, instance, credentials["generation"]),
    )
    cur.execute(
        "UPDATE saas_widget_dialogs SET visible=false WHERE tenant_id=%s AND instance=%s AND visible",
        (tenant, instance),
    )
    for chat, (row, rev) in routes.items():
        identity = _hash([row["client_id"]])
        cur.execute(
            """INSERT INTO saas_widget_dialogs
            (tenant_id,instance,chat_id,identity,revision,title,upstream_at,writable,visible)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT(tenant_id,instance,chat_id,identity) DO UPDATE SET
            revision=excluded.revision,title=excluded.title,upstream_at=excluded.upstream_at,
            writable=excluded.writable,visible=true""",
            (
                tenant,
                instance,
                chat,
                identity,
                rev,
                str(row.get("client_name") or row["client_id"])[:256],
                str(row.get("updated_at") or ""),
                bool(_can_reply(row, login)),
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
    ids = []
    for msg in messages:
        mid = int(msg["id"])
        if (route["before_id"] and mid >= route["before_id"]) or mid <= route["high_id"]:
            continue
        at = stamp(msg["created_at"])
        cur.execute(
            """INSERT INTO saas_widget_messages
            (tenant_id,instance,chat_id,identity,mid,sender,author,body,attachment,at,fresh)
            SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz>s.started_at
            FROM saas_widget_sources s WHERE s.tenant_id=%s AND s.instance=%s
            ON CONFLICT DO NOTHING RETURNING fresh""",
            (*key, mid, msg["sender"], msg["name"], msg["text"], msg["attachment"], at, at, *key[:2]),
        )
        inserted = cur.fetchone()
        ids.append(mid)
        if inserted and msg["sender"] == "client" and inserted["fresh"]:
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
    more = bool(full and ids and min(int(m["id"]) for m in messages) > route["high_id"])
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


def sync_once(tenant):
    credentials = operator._credentials(tenant)
    instance = credentials["connection_instance_id"]
    # A session-level lock serializes workers without holding a transaction during REST calls.
    with operator._connect() as db, db.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held", ("widget-sync/" + tenant,))
        if not cur.fetchone()["held"]:
            return
        db.commit()
        try:
            routes = _routes(_post("/chat/getActiveChats", {}, credentials), credentials)
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
                ORDER BY (fetched_at IS DISTINCT FROM upstream_at OR before_id IS NOT NULL) DESC,
                (checked_at IS NULL) DESC, checked_at NULLS FIRST, upstream_at DESC LIMIT 12""",
                (tenant, instance),
            )
            batch = cur.fetchall()
            db.commit()
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

    async def run():
        while True:
            try:
                await asyncio.to_thread(sync_once, tenant)
            except Exception:
                logger.warning("Operator widget queue refresh failed")
            await asyncio.sleep(10)

    async def context(_):
        task = asyncio.create_task(run(), name="operator-widget-queue")
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app.cleanup_ctx.append(context)
