"""Project private widget routes into the staff inbox without giving them a WhatsApp identity."""

import hashlib
import json
import uuid

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import widget_allowed
from tenant_crm_native_access import scoped_sql
from tenant_dialog_presence import is_online
from tenant_dialog_rollout import guard
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_operator_widget_sync import KEY, PROVIDER, SOURCE, enabled, scope


def source(cur, profile, action="view"):
    if (
        str(profile.get("tenant_id")) != enabled()
        or not widget_allowed(profile)
        or not widget_allowed(profile, action)
    ):
        return None
    row = operator._operator_row(cur, str(profile["tenant_id"]))
    generation, instance = operator._link_connection_scope(row or {})
    if not row or row["status"] != "connected" or not instance:
        return None
    cur.execute(
        """SELECT 1 FROM saas_widget_sources WHERE tenant_id=%s AND instance=%s AND generation=%s
        AND refreshed_at > now()-interval '2 minutes'""",
        (str(profile["tenant_id"]), instance, generation),
    )
    return instance if cur.fetchone() else None


def _allow_own_provider_assignment(cur, tenant, instance, actor, rows):
    owned = [r for r in rows if not r["writable"] and r["state"] == "accepted"
             and r["operator_id"] == actor and r.get("open_operator_login")]
    if not owned:
        return
    cur.execute("""SELECT l.operator_login || '@' || l.operator_account AS login
                   FROM saas_beinsystems_operator_employee_links l JOIN saas_widget_sources s
                   ON s.tenant_id=l.tenant_id AND s.instance=l.connection_instance_id
                   AND s.generation=l.connection_generation WHERE l.tenant_id=%s AND s.instance=%s
                   AND l.member_telegram_id=%s AND l.revoked_at IS NULL
                   AND l.widget_reply_ciphertext IS NOT NULL FOR SHARE OF l""", (tenant, instance, actor))
    link = cur.fetchone()
    if link:
        for row in owned:
            row["writable"] = row["open_operator_login"] == link["login"]


def authorized(cur, profile, params, *, update=False, lock=False, replay=False):
    tenant, actor, policy = access(cur, profile, "dialogs", action="update" if update else "view")
    instance = source(cur, policy.profile, "update" if update else "view")
    if not instance or instance != str(params.get("channel_id")) or params.get("provider") != PROVIDER:
        raise TenantLinearError("Источник недоступен.", 403)
    if not guard(cur, tenant).enforced:
        raise TenantLinearError("Очередь недоступна.", 409)
    key = (tenant, instance, int(params["lead_id"]), str(params["conversation_id"]))
    cur.execute(
        "SELECT * FROM saas_widget_dialogs WHERE "
        + KEY
        + " AND visible AND latest_in IS NOT NULL"
        + (" FOR UPDATE" if lock else " FOR SHARE"),
        key,
    )
    row = cur.fetchone()
    if not row or not replay and row["state"] == "accepted" and row["operator_id"] != actor:
        raise TenantLinearError("Диалог недоступен.", 403)
    _allow_own_provider_assignment(cur, tenant, instance, actor, [row])
    if update and not row["writable"]:
        raise TenantLinearError("Operator ограничил действия в этом чате.", 403)
    return row, actor, policy


def attach_companies(cur, policy, instance, rows):
    """Read confirmed links in one query under the same CRM company ACL as the company card."""
    accounts = sorted({row["operator_account"] for row in rows if row["operator_account"]})
    if not accounts or not policy.allows_action("companies", "view"):
        return
    where, params = scoped_sql(policy.profile, "companies", "view", "c")
    cur.execute(
        f"""SELECT l.external_id, c.id::text AS company_id, c.name
            FROM saas_widget_sources s JOIN saas_beinsystems_operator_links l
              ON l.tenant_id=s.tenant_id AND l.connection_instance_id=s.instance
              AND l.connection_generation=s.generation
            JOIN saas_crm_native_companies c ON c.tenant_id=l.tenant_id AND c.id=l.local_entity_id
            WHERE s.tenant_id=%s AND s.instance=%s AND l.external_id=ANY(%s::text[])
              AND l.local_entity_type='company' AND l.external_entity_type='account'
              AND l.external_scope_id='' AND l.revoked_at IS NULL AND c.archived_at IS NULL AND ({where})""",
        [str(policy.profile["tenant_id"]), instance, accounts, *params],
    )
    # A unique index enforces one active CRM company per account and generation.
    companies = {c["external_id"]: c for c in cur.fetchall()}
    for row in rows:
        if company := companies.get(row["operator_account"]):
            row["crm_company_id"] = company["company_id"]
            row["crm_company_name"] = company["name"]


def card(row, actor, writable, rollout, online):
    state = row["state"]
    own = state == "accepted" and row["operator_id"] == actor and writable and rollout.enforced
    inbound = str(row["latest_in"] or "")
    marked = bool(inbound and row["marked_in"] == row["latest_in"])
    lifecycle = {
        "state": "closed" if state == "archive" else state,
        "revision": row["version"],
        "operator_id": row["operator_id"],
        "last_inbound_id": inbound,
        "has_inbound": bool(inbound),
        "can_accept": bool(
            state == "waiting" and writable and online and rollout.admission_enabled and inbound
        ),
        "can_transfer": own,
        "can_close": own,
        "can_dismiss": bool(own and inbound and not marked and rollout.admission_enabled),
        "no_reply_needed": marked,
    }
    return {
        "provider": PROVIDER,
        "channel_id": str(row["instance"]),
        "channel_name": SOURCE,
        "channel_active": True,
        "conversation_id": row["identity"],
        "lead_id": row["chat_id"],
        "customer_name": row["title"],
        "customer_kind": "contact",
        "operator_account": row["operator_account"],
        "dialog_company_name": row.get("crm_company_name") or row["operator_account"],
        "crm_company_id": row.get("crm_company_id"),
        "can_use_copilot": False,
        "controls_allowed": False,
        "lifecycle_enabled": True,
        "lifecycle": lifecycle,
        "available_actions": [],
        "can_send": False,
        "send_block_reason": "dialog_accept_required" if not own else "widget_socket_unavailable",
        "can_add_internal_comment": own,
        "widget_revision": row["revision"],
        "last_message_at": str(row["latest_at"] or ""),
        "read_through_message_id": inbound or None,
        "unread_count": 0 if state in {"closed", "archive"} else row.get("unread", 0),
        "display_state": "human",
        "agent_status": "manual",
        "last_message_id": str(row["preview_id"]) if row.get("preview_id") else None,
        "last_message_direction": {"client": "incoming", "operator": "outgoing"}.get(
            row.get("preview_sender"), "system"
        ),
        "last_message_type": "text",
        "last_message_text": row.get("preview") or "",
        "last_message_preview": row.get("preview") or "",
        "archive_read_only": state in {"archive", "closed"},
    }


def cards(cur, policy, mode, rollout):
    profile = policy.profile
    instance = source(cur, profile)
    if not instance or not rollout.enforced:
        return [], None
    tenant, actor = str(profile["tenant_id"]), int(profile["telegram_id"])
    cur.execute(
        """SELECT d.*,
       (SELECT count(*) FROM saas_widget_messages m WHERE
           (m.tenant_id,m.instance,m.chat_id,m.identity)=(d.tenant_id,d.instance,d.chat_id,d.identity)
           AND m.sender='client' AND m.fresh AND m.mid>COALESCE(r.through_id,0)) AS unread,
       last.body AS preview,last.mid AS preview_id,last.sender AS preview_sender
       FROM saas_widget_dialogs d LEFT JOIN saas_widget_reads r ON
         (d.tenant_id,d.instance,d.chat_id,d.identity)=(r.tenant_id,r.instance,r.chat_id,r.identity) AND r.actor=%s
       LEFT JOIN LATERAL (SELECT m.body,m.mid,m.sender FROM saas_widget_messages m
           WHERE (m.tenant_id,m.instance,m.chat_id,m.identity)=(d.tenant_id,d.instance,d.chat_id,d.identity)
           ORDER BY m.mid DESC LIMIT 1) last ON true
       WHERE d.tenant_id=%s AND d.instance=%s AND d.visible AND d.latest_in IS NOT NULL AND (state<>'accepted' OR operator_id=%s)
         AND (%s='all' OR (state IN ('archive','closed'))=(%s='completed'))
       ORDER BY latest_at DESC NULLS LAST,chat_id DESC""",
        (actor, tenant, instance, actor, mode, mode),
    )
    rows = cur.fetchall()
    _allow_own_provider_assignment(cur, tenant, instance, actor, rows)
    attach_companies(cur, policy, instance, rows)
    writable = widget_allowed(profile, "update")
    online = is_online(cur, tenant, actor, source_channel=instance)
    return [card(row, actor, writable and row["writable"], rollout, online) for row in rows], {
        "id": PROVIDER + ":" + instance,
        "name": SOURCE,
    }


def merge_order(rows):
    """Insert widget cards by latest activity without reordering any other source."""
    from datetime import datetime
    from heapq import merge

    def timestamp(row):
        try:
            return datetime.fromisoformat(str(row.get("last_message_at"))).timestamp()
        except (ValueError, TypeError, OverflowError):
            return 0

    native = (r for r in rows if r["provider"] != PROVIDER)
    widget = (r for r in rows if r["provider"] == PROVIDER)
    return list(merge(native, widget, key=timestamp, reverse=True))


def history(profile, params, before=None):
    from tenant_operator_widget_transport import socket_block_reason
    from tenant_dialog_internal_comments import _event

    with operator._connect() as db, db.cursor() as cur:
        row, actor, policy = authorized(cur, profile, params)
        key = scope(row)
        rollout = guard(cur, key[0])
        if not rollout.enforced:
            raise TenantLinearError("Очередь недоступна.", 409)
        attach_companies(cur, policy, key[1], [row])
        dialog = card(
            row,
            actor,
            row["writable"] and widget_allowed(policy.profile, "update"),
            rollout,
            is_online(cur, key[0], actor, source_channel=key[1]),
        )
        cur.execute(
            "SELECT *,at AS message_at FROM saas_widget_messages WHERE "
            + KEY
            + " AND (%s::bigint IS NULL OR mid<%s) ORDER BY mid DESC LIMIT 51",
            (*key, before, before),
        )
        rows = cur.fetchall()
        cur.execute(
            """SELECT id,author_name,note,created_at FROM saas_dialog_internal_comments WHERE
            tenant_id=%s AND provider=%s AND channel_id=%s AND conversation_id=%s AND lead_id=%s
            ORDER BY created_at DESC LIMIT 60""",
            (key[0], PROVIDER, key[1], key[3], key[2]),
        )
        comments = [_event(n) for n in reversed(cur.fetchall())]
        cur.execute(
            """SELECT payload FROM saas_tenant_audit_events WHERE tenant_id=%s
             AND entity_type='operator_widget_dialog' AND entity_id=%s ORDER BY created_at DESC LIMIT 60""",
            (key[0], event_key(key)),
        )
        events = [e["payload"]["event"] for e in reversed(cur.fetchall())]
        db.commit()
    if dialog["lifecycle"]["can_close"]:
        credentials = operator._credentials(key[0])
        if credentials["connection_instance_id"] == key[1]:
            from tenant_operator_widget_sender import employee_sender
            try:
                with operator._connect() as db, db.cursor() as cur:
                    sender = employee_sender(cur, key[0], actor, credentials)
                reason = ("widget_socket_auth_denied" if sender and row["open_operator_login"]
                          and sender["widget_login"] != row["open_operator_login"]
                          else socket_block_reason(sender or credentials))
            except operator.OperatorError:
                reason = "widget_socket_auth_denied"
            dialog.update(can_send=not reason, send_block_reason=reason)
    directions = {"client": "incoming", "system": "system", "operator": "outgoing"}
    authors = {"client": "customer", "system": "system", "operator": "operator"}
    messages = [
        {
            "id": str(m["mid"]),
            "body": m["body"],
            "widget_attachment": m["attachment"],
            "direction": directions[m["sender"]],
            "author_name": m["author"],
            "author_type": authors[m["sender"]],
            "message_type": "text",
            "created_at": m["message_at"].isoformat(),
            "happened_at": m["message_at"].isoformat(),
        }
        for m in reversed(rows[:50])
    ]
    return {
        "dialog": dialog,
        "messages": messages,
        "internal_comments": comments,
        "operator_events": events,
        "next_before": str(rows[49]["mid"]) if len(rows) > 50 else None,
        "read_through_message_id": messages[-1]["id"] if messages and not before else None,
    }


def read(profile, params):
    with operator._connect() as db, db.cursor() as cur:
        row, actor, _ = authorized(cur, profile, params)
        key = scope(row)
        cur.execute(
            """INSERT INTO saas_widget_reads(tenant_id,instance,chat_id,identity,actor,through_id)
            SELECT tenant_id,instance,chat_id,identity,%s,mid FROM saas_widget_messages WHERE """
            + KEY
            + """ AND mid=%s ON CONFLICT(tenant_id,instance,chat_id,identity,actor) DO UPDATE
            SET through_id=GREATEST(saas_widget_reads.through_id,excluded.through_id) RETURNING through_id""",
            (actor, *key, int(params["message_id"])),
        )
        if not cur.fetchone():
            raise TenantLinearError("Сообщение недоступно.", 404)
        return {"success": True}


def targets(cur, tenant, actor, instance, *, online_only=False):
    from tenant_scoped_access_store import read_access_profile
    from tenant_dialog_presence import is_sidebar_transfer_online

    cur.execute(
        "SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s AND active AND telegram_id<>%s ORDER BY telegram_id",
        (tenant, actor),
    )
    result = []
    for item in cur.fetchall():
        emp = item["telegram_id"]
        profile = read_access_profile(cur, tenant, emp, lock=True)
        if not profile or not widget_allowed(profile) or not widget_allowed(profile, "update"):
            continue
        check = is_sidebar_transfer_online if online_only else is_online
        if check(cur, tenant, emp, source_channel=instance, lock=True):
            result.append({"employee_id": emp, "name": profile.get("display_name") or str(emp)})
    return result


def transfer_candidates(profile, params):
    with operator._connect() as db, db.cursor() as cur:
        row, actor, _ = authorized(cur, profile, params, update=True)
        if (
            row["state"] != "accepted"
            or row["operator_id"] != actor
            or int(params["expected_revision"]) != row["version"]
        ):
            raise TenantLinearError("Обновите диалог.", 409)
        return {"targets": targets(cur, str(row["tenant_id"]), actor, str(row["instance"]), online_only=True)}


def event_key(key):
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def mutate(profile, params):
    action = params["action"]
    if action not in {"accept", "transfer", "close", "dismiss"}:
        raise TenantLinearError("Действие недоступно.", 400)
    req = str(uuid.UUID(str(params["request_id"])))
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    with operator._connect() as db, db.cursor() as cur:
        rollout = guard(cur, str(profile["tenant_id"]))
        row, actor, policy = authorized(cur, profile, params, update=True, lock=True, replay=True)
        key = scope(row)
        cur.execute(
            "SELECT actor_id,input_hash,result FROM saas_dialog_lifecycle_receipts WHERE tenant_id=%s AND request_id=%s",
            (key[0], req),
        )
        receipt = cur.fetchone()
        if receipt:
            if receipt["actor_id"] == actor and receipt["input_hash"] == digest:
                return receipt["result"]
            raise TenantLinearError("Запрос уже использован.", 409)
        if row["state"] == "accepted" and row["operator_id"] != actor:
            raise TenantLinearError("Диалог недоступен.", 403)
        online = is_online(cur, key[0], actor, source_channel=key[1], lock=True)
        life = card(row, actor, True, rollout, online)["lifecycle"]
        if params.get("expected_revision") != row["version"] or not life["can_" + action]:
            raise TenantLinearError("Обновите диалог.", 409)
        assignee = actor
        target_name = None
        if action == "transfer":
            options = targets(cur, key[0], actor, key[1], online_only=bool(params.get("online_only")))
            target = next((v for v in options if v["employee_id"] == params.get("target_employee_id")), None)
            if not target:
                raise TenantLinearError("Сотрудник недоступен.", 403)
            assignee, target_name = target["employee_id"], target["name"]
        if action == "dismiss" and (
            params.get("individual_no_reply") is not True
            or str(params.get("message_id")) != life["last_inbound_id"]
        ):
            raise TenantLinearError("Входящее сообщение изменилось.", 409)
        state = "closed" if action == "close" else "accepted"
        cur.execute(
            """UPDATE saas_widget_dialogs SET state=%s,operator_id=%s,version=version+1,
            marked_in=CASE WHEN %s='dismiss' THEN latest_in ELSE marked_in END,
            closed_at=CASE WHEN %s='close' THEN clock_timestamp() ELSE closed_at END
            WHERE """
            + KEY
            + " RETURNING *,clock_timestamp() AS event_at",
            (state, assignee, action, action, *key),
        )
        updated = cur.fetchone()
        event = {
            "id": req,
            "kind": "no_reply" if action == "dismiss" else action,
            "at": updated["event_at"].isoformat(),
            "employee_name": target_name or policy.profile.get("display_name"),
        }
        result = {
            "lifecycle": card(updated, actor, True, rollout, online)["lifecycle"],
            "operator_event": event,
        }
        cur.execute(
            """INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
            VALUES(%s,%s,%s,%s,'operator_widget_dialog',%s,%s::jsonb)""",
            (req, key[0], actor, "widget_" + action, event_key(key), json.dumps({"event": event})),
        )
        cur.execute(
            """INSERT INTO saas_dialog_lifecycle_receipts(tenant_id,request_id,actor_id,input_hash,result)
            VALUES(%s,%s,%s,%s,%s::jsonb)""",
            (key[0], req, actor, digest, json.dumps(result)),
        )
        return result
