"""Live projection of the private Operator widget. Operator remains the message store."""
import hashlib
import json
import uuid
from collections import Counter
from urllib.parse import urlencode

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import widget_allowed
from tenant_linear_policy import TenantLinearError

PAGE = 50


def _positive(value):
    value = str(value)
    if 0 < len(value) <= 19 and value.isascii() and value.isdecimal():
        number = int(value)
        if 0 < number < 2**63:
            return number
    return None


def _string(value, limit):
    return value if isinstance(value, str) and len(value) <= limit else ""


def _hash(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _post(path, payload, credentials):
    raw = operator._post_read_with_requests(operator.OPERATOR_API_BASE_URL + path, payload,
                                           credentials["token"], accept_json=True)
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        envelope = None
    if (not isinstance(envelope, dict) or not isinstance(envelope.get("data"), list)
            or envelope.get("error") or envelope.get("errors") or envelope.get("success") is False
            or envelope.get("status", "success") != "success"):
        raise operator.OperatorError("Operator не подтвердил список диалогов или сообщений.", 502)
    return envelope["data"]


def _routes(rows, credentials):
    all_rows = [r for r in rows if isinstance(r, dict)]
    ids = Counter(_positive(r.get("id")) for r in all_rows)
    clients = Counter(str(r.get("client_id")) for r in all_rows)
    routes = {}
    for row in all_rows:
        chat = _positive(row.get("id"))
        client = _string(row.get("client_id"), 256)
        if (row.get("channel") != "widget" or not chat or ids[chat] != 1 or not client.strip()
                or clients[client] != 1 or any(ord(c) < 32 for c in client)):
            continue
        revision = _hash([credentials["connection_instance_id"], credentials["generation"],
                          chat, client, row.get("account")])
        routes[chat] = (row, revision)
    return routes


def _snapshot(cur, profile, credentials, action="view"):
    tenant = operator._tenant(profile)
    current = operator._current_profile(cur, tenant, int(profile["telegram_id"]))
    if not widget_allowed(current, "view") or not widget_allowed(current, action):
        raise operator.OperatorError("Нет доступа к этому источнику диалогов.", 403)
    row = operator._operator_row(cur, tenant)
    if (not row or row["status"] != "connected" or operator._link_connection_scope(row) !=
            (credentials["generation"], credentials["connection_instance_id"])):
        raise operator.OperatorError("Подключение Operator изменилось. Откройте источник заново.", 409)
    return current, operator._connected_login(row, tenant)


def _can_reply(row, login):
    return (row.get("status") == "open" and "operator" in row and
            (row["operator"] is None or (login and row["operator"] == login)))


def _card(chat_id, row, revision, writable, login, socket_ready=False):
    return {"id": str(chat_id), "revision": revision, "name": _string(row.get("client_name"), 256) or row["client_id"],
            "status": _string(row.get("status"), 40), "operator": _string(row.get("operator"), 240),
            "updated_at": _string(row.get("updated_at"), 64), "unread": _positive(row.get("unread_count")) or 0,
            "operator_url": "https://operator.beinsystems.az/chats?" + urlencode({"clientId": row["client_id"]}),
            "can_reply": bool(socket_ready and writable and _can_reply(row, login)),
            "reply_in_operator": bool(writable and _can_reply(row, login) and not socket_ready)}


def _messages(rows, chat_id, client_id):
    messages = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (_positive(row.get("chat_id")) != chat_id
                or row.get("client_id") is not None and row["client_id"] != client_id
                or row.get("channel") is not None and row["channel"] != "widget"):
            raise operator.OperatorError("Operator вернул историю другого диалога.", 502)
        mid = _positive(row.get("id"))
        if not mid or row.get("sender_type") not in {"client", "operator", "system"}:
            continue
        messages[mid] = {"id": str(mid), "sender": row["sender_type"],
                         "name": _string(row.get("operator_name") or row.get("sender_name"), 240),
                         "text": _string(row.get("text"), 65536), "attachment": bool(row.get("media_url")),
                         "created_at": _string(row.get("created_at"), 64)}
    return [messages[mid] for mid in sorted(messages)]


def _route(routes, chat, revision):
    match = routes.get(chat)
    if not match or match[1] != revision:
        raise operator.OperatorError("Диалог изменился или больше недоступен. Обновите список.", 409)
    return match[0]


def _inputs(data):
    action = data.get("action")
    fields = {"list": set(), "history": {"chat_id", "revision"},
              "older": {"chat_id", "revision", "before_id"},
              "reply": {"chat_id", "revision", "text", "request_id"}}
    if not isinstance(action, str) or action not in fields or set(data) != fields[action] | {"action"}:
        raise operator.OperatorError("Некорректный запрос к виджету.")
    chat = _positive(data.get("chat_id"))
    if action != "list" and (not chat or len(_string(data["revision"], 64)) != 64):
        raise operator.OperatorError("Некорректный диалог.")
    if action == "older" and not _positive(data["before_id"]):
        raise operator.OperatorError("Некорректная граница истории.")
    if action == "reply":
        try:
            key = uuid.UUID(str(data["request_id"]))
            if key.version != 4 or str(key) != data["request_id"] or not _string(data["text"], 4000).strip():
                raise ValueError()
        except ValueError:
            raise operator.OperatorError("Укажите текст и идентификатор отправки.") from None
    return action, chat


def handle(profile, data):
    action, chat = _inputs(data)
    tenant = operator._tenant(profile)
    if not widget_allowed(profile):
        raise operator.OperatorError("Нет доступа к этому источнику диалогов.", 403)
    credentials = operator._credentials(tenant)
    with operator._connect() as db, db.cursor() as cur:
        current, login = _snapshot(cur, profile, credentials, "update" if action == "reply" else "view")
    operator._record_operator_request(profile, "widget", {})
    if action == "reply":
        return _reply(profile, credentials, chat, data)
    routes = _routes(_post("/chat/getActiveChats", {}, credentials), credentials)
    socket_ready = False
    if action != "list":
        row = _route(routes, chat, data["revision"])
        query = {"chat_id": chat, "client_id": row["client_id"], "limit": PAGE}
        if action == "older":
            query["before_id"] = _positive(data["before_id"])
        rows = _post("/chat/getMessages", query, credentials)
        if widget_allowed(current, "update") and _can_reply(row, login):
            from tenant_operator_widget_transport import socket_available
            socket_ready = socket_available(credentials)
    with operator._connect() as db, db.cursor() as cur:
        current, login = _snapshot(cur, profile, credentials)
        writable = widget_allowed(current, "update")
        from tenant_operator_widget_sync import enabled
        managed = enabled() == tenant
        assignments = {}
        if managed:
            cur.execute("""SELECT chat_id,identity,state,operator_id FROM saas_widget_dialogs
                WHERE tenant_id=%s AND instance=%s""",(tenant,credentials["connection_instance_id"]))
            assignments = {(r["chat_id"],r["identity"]):r for r in cur.fetchall()}
        def access_for(i, provider_row):
            workflow = assignments.get((i,_hash([provider_row["client_id"]]))) or {}
            own = workflow.get("state")=="accepted" and workflow.get("operator_id")==int(profile["telegram_id"])
            return workflow.get("state")!="accepted" or own, writable and (not managed or own)
        if action == "list":
            cards = []
            for i,(r,rev) in routes.items():
                visible, allowed = access_for(i,r)
                if visible:
                    cards.append(_card(i,r,rev,allowed,login))
            return {"chats":cards}
        visible,writable = access_for(chat,row)
        if not visible:
            raise operator.OperatorError("Диалог недоступен.",403)
        messages = _messages(rows, chat, row["client_id"])
        _reconcile(cur, tenant, chat, data["revision"], rows)
        if action == "older":
            messages = [m for m in messages if int(m["id"]) < _positive(data["before_id"])]
        return {"chat": _card(chat, row, data["revision"], writable, login, socket_ready),
                "messages": messages, "has_older": bool(messages and len(rows) >= PAGE)}


def _reconcile(cur, tenant, chat, revision, rows):
    # Some history responses echo temp_id. Text alone never proves which send was saved.
    observed = []
    for row in rows:
        if not isinstance(row, dict) or row.get("sender_type") != "operator":
            continue
        temp = _string(row.get("temp_id"), 41)
        if not temp.startswith("temp-") or not (mid := _positive(row.get("id"))):
            continue
        try:
            key = uuid.UUID(temp[5:])
        except ValueError:
            continue
        if key.version == 4 and str(key) == temp[5:]:
            observed.append({"request_id": str(key), "message_id": str(mid),
                             "signature": _hash([chat, revision, row.get("text")])})
    if observed:
        cur.execute("""UPDATE saas_operator_widget_replies r SET state='saved',message_id=m.message_id
            FROM jsonb_to_recordset(%s::jsonb) AS m(request_id uuid, message_id text, signature text)
            WHERE r.tenant_id=%s AND r.request_id=m.request_id AND r.signature=m.signature AND r.state='unknown'""",
            (json.dumps(observed), tenant))


def _require_queue_owner(cur, profile, credentials, chat, row):
    from tenant_operator_widget_sync import enabled, PROVIDER
    if enabled() != operator._tenant(profile):
        return
    from tenant_operator_widget_queue import authorized
    try:
        route,actor,_ = authorized(cur,profile,{
            "provider":PROVIDER,"channel_id":credentials["connection_instance_id"],"lead_id":chat,
            "conversation_id":_hash([row["client_id"]])},update=True,lock=True)
    except TenantLinearError as error:
        raise operator.OperatorError(str(error),error.status) from error
    if route["state"]!="accepted" or route["operator_id"]!=actor:
        raise operator.OperatorError("Сначала примите диалог.",409)


def _reply(profile, credentials, chat, data):
    from tenant_operator_widget_transport import send_reply
    tenant, actor = operator._tenant(profile), int(profile["telegram_id"])
    text = data["text"].strip()
    signature = _hash([chat, data["revision"], text])
    key = (tenant, data["request_id"])
    # Commit an uncertain outcome before dispatch so a crash or a second worker cannot replay it.
    with operator._connect() as db, db.cursor() as cur:
        _snapshot(cur, profile, credentials, "update")
        cur.execute("""INSERT INTO saas_operator_widget_replies
            (tenant_id, request_id, actor, signature, state) VALUES (%s,%s,%s,%s,'unknown')
            ON CONFLICT DO NOTHING RETURNING request_id""", (*key, actor, signature))
        if not cur.fetchone():
            cur.execute("""SELECT actor,signature,state,message_id FROM saas_operator_widget_replies
                           WHERE tenant_id=%s AND request_id=%s""", key)
            receipt = cur.fetchone()
            if not receipt:
                cur.execute("""SELECT request_id, actor FROM saas_operator_widget_replies
                    WHERE tenant_id=%s AND signature=%s AND state='unknown'""", (tenant, signature))
                pending = cur.fetchone()
                if pending and pending["actor"] == actor:
                    return {"state": "unknown", "request_id": str(pending["request_id"])}
                raise operator.OperatorError("Такой ответ уже отправляли. Проверьте историю в Operator.", 409)
            if receipt["actor"] != actor or receipt["signature"] != signature:
                raise operator.OperatorError("Идентификатор отправки уже используется.", 409)
            return {"state": receipt["state"], "message_id": receipt["message_id"]}
    # Keep the local grant and exact credential instance locked during the single dispatch.
    with operator._connect() as db, db.cursor() as cur:
        try:
            _, login = _snapshot(cur, profile, credentials, "update")
            routes = _routes(_post("/chat/getActiveChats", {}, credentials), credentials)
            row = _route(routes, chat, data["revision"])
            _require_queue_owner(cur, profile, credentials, chat, row)
            if not _can_reply(row, login):
                raise operator.OperatorError("Диалог закрыт или закреплён за другим оператором.", 409)
        except operator.OperatorError:
            cur.execute("UPDATE saas_operator_widget_replies SET state='rejected' WHERE tenant_id=%s AND request_id=%s", key)
            db.commit()
            raise
        state, mid = send_reply(credentials, row, text, data["request_id"])
        cur.execute("""UPDATE saas_operator_widget_replies SET state=%s,message_id=%s
            WHERE tenant_id=%s AND request_id=%s AND state='unknown' RETURNING state,message_id""",
            (state, mid, *key))
        receipt = cur.fetchone()
        if not receipt:  # A parallel history read already reconciled this exact send.
            cur.execute("SELECT state,message_id FROM saas_operator_widget_replies WHERE tenant_id=%s AND request_id=%s", key)
            receipt = cur.fetchone()
        return {"state": receipt["state"], "message_id": receipt["message_id"]}
