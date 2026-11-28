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


def source(cur, profile, action="view", *, provider_action=True):
    if (
        str(profile.get("tenant_id")) != enabled()
        or not widget_allowed(profile, assigned=True)
        or not widget_allowed(profile, action, assigned=True, provider_action=provider_action)
    ):
        return None
    row = operator._operator_row(cur, str(profile["tenant_id"]))
    generation, instance = operator._link_connection_scope(row or {})
    if not row or row["status"] != "connected" or not instance:
        return None
    cur.execute(
        """SELECT refreshed_at > now()-interval '2 minutes' AS fresh FROM saas_widget_sources
        WHERE tenant_id=%s AND instance=%s AND generation=%s""",
        (str(profile["tenant_id"]), instance, generation),
    )
    snapshot = cur.fetchone()
    return (instance, snapshot["fresh"] is True) if snapshot else None


def _allow_provider_assignment(cur, tenant, instance, rows, fresh):
    if not rows:
        return
    cur.execute("""SELECT l.operator_login || '@' || l.operator_account AS login
                   FROM saas_beinsystems_operator_employee_links l JOIN saas_widget_sources s
                   ON s.tenant_id=l.tenant_id AND s.instance=l.connection_instance_id
                   AND s.generation=l.connection_generation WHERE l.tenant_id=%s AND s.instance=%s
                   AND l.revoked_at IS NULL AND EXISTS (SELECT 1 FROM saas_tenant_members m
                       WHERE m.tenant_id=l.tenant_id AND m.telegram_id=l.member_telegram_id AND m.active)
                   AND l.widget_reply_ciphertext IS NOT NULL FOR SHARE OF l""", (tenant, instance))
    link = cur.fetchone()
    for row in rows:
        row["reply_sender"] = bool(link)
        row["provider_block"] = ""
        if not fresh:
            row["provider_block"] = "widget_sync_stale"
        elif not row["visible"]:
            row["provider_block"] = "widget_session_unavailable"
        elif row["upstream_status"] == "closed":
            row["provider_block"] = "widget_session_closed"
        elif row["upstream_status"] != "open":
            row["provider_block"] = "widget_session_unavailable"
        elif row["open_operator_login"] and not (
            row["open_operator_login"] == link["login"] if link else row["writable"]
        ):
            row["provider_block"] = "widget_assigned_elsewhere"
        row["writable"] = not row["provider_block"]


def _unassigned(row):
    return row["state"] == "waiting" and row["operator_id"] is None


def _within_work_scope(row, actor):
    return _unassigned(row) or row["state"] in {"accepted", "closed"} and row["operator_id"] == actor


def _may_comment(profile, row, actor):
    within_scope = _within_work_scope(row, actor)
    return widget_allowed(profile, assigned=within_scope) and widget_allowed(
        profile, "update", assigned=within_scope, provider_action=False)


def authorized(cur, profile, params, *, update=False, lock=False, replay=False, waiting=False,
               require_provider_write=True, local_comment=False):
    tenant, actor, policy = access(cur, profile, "dialogs", action="update" if update else "view")
    binding = source(cur, policy.profile, "update" if update else "view", provider_action=not local_comment)
    if not binding or binding[0] != str(params.get("channel_id")) or params.get("provider") != PROVIDER:
        raise TenantLinearError("Источник недоступен.", 403)
    instance, fresh = binding
    if not guard(cur, tenant).enforced:
        raise TenantLinearError("Очередь недоступна.", 409)
    key = (tenant, instance, int(params["lead_id"]), str(params["conversation_id"]))
    cur.execute(
        "SELECT * FROM saas_widget_dialogs WHERE "
        + KEY
        + " AND latest_in IS NOT NULL"
        + (" FOR UPDATE" if lock else " FOR SHARE"),
        key,
    )
    row = cur.fetchone()
    if not row or not replay and row["state"] == "accepted" and row["operator_id"] != actor:
        raise TenantLinearError("Диалог недоступен.", 403)
    if not widget_allowed(policy.profile) or (
        update and not widget_allowed(policy.profile, "update", provider_action=not local_comment)
    ):
        own = row["state"] in {"accepted", "closed"} and row["operator_id"] == actor
        if not own and not (waiting and _unassigned(row) and _may_comment(policy.profile, row, actor)):
            raise TenantLinearError("Диалог недоступен.", 403)
    _allow_provider_assignment(cur, tenant, instance, [row], fresh)
    if update and require_provider_write and not row["writable"]:
        raise TenantLinearError("Operator ограничил действия в этом чате.", 403)
    return row, actor, policy


def attach_companies(cur, policy, instance, rows, *, details=False):
    """Read confirmed links in one query under the same CRM company ACL as the company card."""
    accounts = sorted({row["operator_account"] for row in rows if row["operator_account"]})
    if not accounts:
        return
    where, params = (scoped_sql(policy.profile, "companies", "view", "c")
                     if policy.allows_action("companies", "view") else ("FALSE", []))
    fields = ",c.phone,c.email,c.description" if details else ""
    cur.execute(
        f"""SELECT l.external_id, c.id::text AS company_id, c.name {fields}
            FROM saas_widget_sources s JOIN saas_beinsystems_operator_links l
              ON l.tenant_id=s.tenant_id AND l.connection_instance_id=s.instance
              AND l.connection_generation=s.generation
            LEFT JOIN saas_crm_native_companies c ON c.tenant_id=l.tenant_id AND c.id=l.local_entity_id
              AND c.archived_at IS NULL AND ({where})
            WHERE s.tenant_id=%s AND s.instance=%s AND l.external_id=ANY(%s::text[])
              AND l.local_entity_type='company' AND l.external_entity_type='account'
              AND l.external_scope_id='' AND l.revoked_at IS NULL""",
        [*params, str(policy.profile["tenant_id"]), instance, accounts],
    )
    # A unique index enforces one active CRM company per account and generation.
    companies = {c["external_id"]: c for c in cur.fetchall()}
    for row in rows:
        company = companies.get(row["operator_account"])
        if company and company["company_id"]:
            row["crm_company_id"] = company["company_id"]
            row["crm_company_name"] = company["name"]
        if details:
            row["company_status"] = "linked" if row.get("crm_company_id") else "denied" if company else "unlinked"
            if row["company_status"] == "linked":
                row["company_details"] = {field: company[field] for field in ("phone", "email", "description")}


def card(row, actor, can_update, rollout, online, *, can_comment=False):
    state = row["state"]
    local_own = state == "accepted" and row["operator_id"] == actor and can_update and rollout.enforced
    writable = can_update and row["writable"]
    own = local_own and writable
    inbound = str(row["latest_in"] or "")
    marked = bool(inbound and row["marked_in"] == row["latest_in"])
    admission = can_update and online and rollout.admission_enabled and bool(inbound)
    action_block = row["provider_block"]
    if not action_block:
        if not can_update:
            action_block = "widget_read_only"
        elif not rollout.admission_enabled:
            action_block = "widget_admission_paused"
        elif not online:
            action_block = "widget_staff_offline"
        elif state in {"archive", "closed"} and not row["reply_sender"]:
            action_block = "widget_reply_identity_missing"
    lifecycle = {
        "state": "closed" if state == "archive" else state,
        "revision": row["version"],
        "operator_id": row["operator_id"],
        "last_inbound_id": inbound,
        "has_inbound": bool(inbound),
        "can_accept": bool(_unassigned(row) and row["writable"] and admission),
        "can_start": bool(state in {"archive", "closed"} and row["writable"] and row["reply_sender"] and admission),
        "action_block_reason": action_block,
        "can_transfer": own,
        # Completion only changes the CRM queue, not the provider chat.
        "can_close": local_own,
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
        **({field: row[field] for field in ("company_status", "company_details") if field in row}),
        "can_use_copilot": False,
        "controls_allowed": False,
        "lifecycle_enabled": True,
        "lifecycle": lifecycle,
        "available_actions": [],
        "can_send": False,
        "send_block_reason": row["provider_block"] or ("widget_socket_unavailable" if own else "dialog_accept_required"),
        "can_add_internal_comment": can_comment,
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
    binding = source(cur, profile)
    if not binding or not rollout.enforced:
        return [], None
    instance, fresh = binding
    tenant, actor = str(profile["tenant_id"]), int(profile["telegram_id"])
    full_view = widget_allowed(profile)
    can_work = widget_allowed(profile, "update", assigned=True, provider_action=False)
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
           AND (%s OR d.state<>'waiting' OR m.mid=d.latest_in)
           ORDER BY m.mid DESC LIMIT 1) last ON true
       WHERE d.tenant_id=%s AND d.instance=%s AND (d.visible OR state='closed') AND d.latest_in IS NOT NULL AND (state<>'accepted' OR operator_id=%s)
         AND NOT EXISTS(SELECT 1 FROM saas_operator_dialog_removals removal
           WHERE removal.provider='operator_widget' AND removal.tenant_id=d.tenant_id
             AND removal.channel_id=d.instance AND removal.conversation_id=d.identity
             AND removal.lead_id=d.chat_id AND removal.widget_inbound_id=d.latest_in)
         AND (%s OR (state IN ('accepted','closed') AND operator_id=%s) OR (%s AND state='waiting' AND operator_id IS NULL))
         AND (%s='all' OR (state IN ('archive','closed'))=(%s='completed'))
       ORDER BY latest_at DESC NULLS LAST,chat_id DESC""",
        (actor, full_view, tenant, instance, actor, full_view, actor, can_work, mode, mode),
    )
    rows = cur.fetchall()
    _allow_provider_assignment(cur, tenant, instance, rows, fresh)
    attach_companies(cur, policy, instance, rows)
    online = is_online(cur, tenant, actor, source_channel=instance)
    return [card(row, actor, widget_allowed(profile, "update", assigned=_within_work_scope(row, actor)),
                 rollout, online, can_comment=_may_comment(profile, row, actor)) for row in rows], {
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


def _reply_block(cur, row, *, confirm_session=False, require_reply=True):
    from tenant_operator_widget_transport import socket_block_reason
    from tenant_operator_widget_sender import widget_sender

    tenant, instance, chat, identity = scope(row)
    try:
        credentials = operator._credentials(tenant)
        if credentials["connection_instance_id"] != instance:
            return "widget_session_unavailable"
        sender = widget_sender(cur, tenant, credentials)
        if confirm_session:
            from tenant_operator_widget import _post, _routes, _hash, _can_reply
            snapshot = _routes(_post("/chat/getActiveChats", {}, credentials), credentials).get(chat)
            if not snapshot or snapshot[1] != row["revision"] or _hash([snapshot[0]["client_id"]]) != identity:
                return "widget_session_unavailable"
            upstream = snapshot[0]
            if upstream.get("status") == "closed":
                return "widget_session_closed"
            login = sender["widget_login"] if sender else operator._connected_login(
                operator._operator_row(cur, tenant), tenant)
            if not _can_reply(upstream, login):
                return "widget_assigned_elsewhere"
        if not require_reply:
            return ""
        if not sender:
            return "widget_reply_identity_missing"
        if not confirm_session and row["open_operator_login"] and sender["widget_login"] != row["open_operator_login"]:
            return "widget_assigned_elsewhere"
        return socket_block_reason(sender)
    except operator.OperatorError as exc:
        return "widget_socket_auth_denied" if exc.status in (401, 403, 409) else "widget_socket_unavailable"


def history(profile, params, before=None):
    from tenant_dialog_internal_comments import _event

    with operator._connect() as db, db.cursor() as cur:
        row, actor, policy = authorized(cur, profile, params, waiting=True)
        key = scope(row)
        rollout = guard(cur, key[0])
        if not rollout.enforced:
            raise TenantLinearError("Очередь недоступна.", 409)
        attach_companies(cur, policy, key[1], [row], details=True)
        dialog = card(
            row,
            actor,
            widget_allowed(policy.profile, "update", assigned=_within_work_scope(row, actor)),
            rollout,
            is_online(cur, key[0], actor, source_channel=key[1]),
            can_comment=_may_comment(policy.profile, row, actor),
        )
        from tenant_operator_dialog_removal import can_remove
        dialog["can_remove_dialog"] = can_remove(policy, dialog)
        admission_preview = _unassigned(row) and not widget_allowed(policy.profile)
        cur.execute(
            "SELECT *,at AS message_at FROM saas_widget_messages WHERE "
            + KEY
            + " AND (%s::bigint IS NULL OR mid<%s) AND (NOT %s OR (mid=%s AND sender='client')) ORDER BY mid DESC LIMIT 51",
            (*key, before, before, admission_preview, row["latest_in"]),
        )
        rows = cur.fetchall()
        from tenant_operator_widget import confirmed_authors
        crm_authors = confirmed_authors(cur, key[0], row["revision"], [str(m["mid"]) for m in rows])
        cur.execute(
            """SELECT id,author_name,note,created_at FROM saas_dialog_internal_comments WHERE
            tenant_id=%s AND provider=%s AND channel_id=%s AND conversation_id=%s AND lead_id=%s
            AND (NOT %s OR actor_id=%s) ORDER BY created_at DESC LIMIT 60""",
            (key[0], PROVIDER, key[1], key[3], key[2], admission_preview, actor),
        )
        comments = [_event(n) for n in reversed(cur.fetchall())]
        cur.execute(
            """SELECT payload FROM saas_tenant_audit_events WHERE tenant_id=%s
             AND entity_type='operator_widget_dialog' AND entity_id=%s AND NOT %s ORDER BY created_at DESC LIMIT 60""",
            (key[0], event_key(key), admission_preview),
        )
        events = [e["payload"]["event"] for e in reversed(cur.fetchall())]
        db.commit()
    life = dialog["lifecycle"]
    if row["writable"] and (life["can_close"] or life["can_start"]):
        with operator._connect() as db, db.cursor() as cur:
            reason = _reply_block(cur, row)
        if life["can_start"] and reason:
            life.update(can_start=False, action_block_reason=reason)
        if life["can_close"]:
            dialog.update(can_send=not reason, send_block_reason=reason)
    directions = {"client": "incoming", "system": "system", "operator": "outgoing"}
    authors = {"client": "customer", "system": "system", "operator": "operator"}
    messages = [
        {
            "id": str(m["mid"]),
            "body": m["body"],
            "widget_attachment": m["attachment"],
            "direction": directions[m["sender"]],
            "author_name": crm_authors.get(str(m["mid"]), m["author"]) if m["sender"] == "operator" else m["author"],
            "author_type": "employee" if m["sender"] == "operator" and str(m["mid"]) in crm_authors else authors[m["sender"]],
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
        "read_through_message_id": messages[-1]["id"] if messages and not before and not admission_preview else None,
    }


def read(profile, params):
    with operator._connect() as db, db.cursor() as cur:
        row, actor, policy = authorized(cur, profile, params, waiting=True)
        if (_unassigned(row) and not widget_allowed(policy.profile)
                and int(params["message_id"]) != row["latest_in"]):
            raise TenantLinearError("Сообщение недоступно.", 404)
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
        if not profile or not widget_allowed(profile, assigned=True) or not widget_allowed(profile, "update", assigned=True):
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
    if action not in {"accept", "start", "transfer", "close", "dismiss"}:
        raise TenantLinearError("Действие недоступно.", 400)
    req = str(uuid.UUID(str(params["request_id"])))
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    with operator._connect() as db, db.cursor() as cur:
        rollout = guard(cur, str(profile["tenant_id"]))
        row, actor, policy = authorized(
            cur, profile, params, update=True, lock=True, replay=True, waiting=action == "accept",
            require_provider_write=action in {"transfer", "dismiss"},
        )
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
        if action in {"accept", "start"}:
            reason = _reply_block(cur, row, confirm_session=True, require_reply=action == "start")
            if reason:
                message = {
                    "widget_session_closed": "Сессия закрыта в Operator. Дождитесь нового сообщения клиента.",
                    "widget_assigned_elsewhere": "В Operator этот чат назначен другому оператору.",
                    "widget_socket_auth_denied": "Operator отклонил авторизацию чата.",
                }.get(reason, "Operator не подтвердил доступ к этой сессии. Обновите историю.")
                raise TenantLinearError(message, 409)
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
        updated = {**row, **cur.fetchone()}
        updated["writable"] = row["writable"]
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
