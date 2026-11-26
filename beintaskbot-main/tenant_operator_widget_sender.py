"""Personal credentials bound to an existing CRM employee link, for widget replies only."""
import json
import uuid

from cryptography.fernet import InvalidToken

import tenant_beinsystems_operator as operator


def _binding(tenant, link):
    return {"purpose": "operator_widget_reply", "tenant": tenant, "link": str(link["id"]),
            "actor": int(link["member_telegram_id"]), "instance": str(link["connection_instance_id"]),
            "generation": link["connection_generation"],
            "login": link["operator_login"] + "@" + link["operator_account"]}


def employee_sender(cur, tenant, actor, credentials):
    cur.execute("""SELECT id, member_telegram_id, connection_instance_id, connection_generation,
                   operator_login, operator_account, widget_reply_ciphertext
                   FROM saas_beinsystems_operator_employee_links
                   WHERE tenant_id=%s AND member_telegram_id=%s AND connection_instance_id=%s
                   AND connection_generation=%s AND revoked_at IS NULL
                   AND widget_reply_ciphertext IS NOT NULL FOR SHARE""",
                (tenant, actor, credentials["connection_instance_id"], credentials["generation"]))
    link = cur.fetchone()
    if not link:
        return None
    binding = _binding(tenant, link)
    try:
        envelope = json.loads(operator._fernet().decrypt(bytes(link["widget_reply_ciphertext"])))
        if envelope["binding"] == binding and isinstance(envelope["token"], str) and envelope["token"]:
            return {**credentials, "token": envelope["token"], "widget_login": binding["login"]}
    except (InvalidToken, ValueError, KeyError, TypeError):
        pass
    raise operator.OperatorError("Авторизацию сотрудника для ответов в виджете нужно обновить.", 409)


def bind_pending(profile, expected_login):
    """Owner-initiated provisioning. Never activate this login as the main integration."""
    from tenant_operator_widget_transport import probe_realtime_origin
    tenant = operator._tenant(profile)
    actor = int(profile["telegram_id"])
    candidate = operator._pending_auth_candidate(profile)
    identity = operator.operator_me_payload(operator._request_operator(
        "me", {}, token=candidate["token"], base_url=candidate["base_url"], requests_me=True))
    if identity != {"identifier": expected_login, "role": "operator"}:
        raise operator.OperatorError("Учётная запись или роль Operator не совпадает.", 409)
    if probe_realtime_origin(candidate) != "connected":
        raise operator.OperatorError("Operator не подтвердил вход в чат.", 409)
    with operator._connect() as db, db.cursor() as cur:
        if operator._current_profile(cur, tenant, actor).get("role") != "owner":
            raise operator.OperatorError("Требуются права владельца.", 403)
        integration = operator._operator_row(cur, tenant, lock=True)
        generation, instance = operator._link_connection_scope(integration)
        if not instance or integration["status"] != "connected" or not operator._pending_timestamp_is_current(
                cur, tenant, actor, candidate["received_at"]):
            raise operator.OperatorError("Подключение изменилось.", 409)
        login, account = expected_login.split("@")
        cur.execute("""SELECT l.* FROM saas_beinsystems_operator_employee_links l
                   JOIN saas_tenant_members m ON m.tenant_id=l.tenant_id AND m.telegram_id=l.member_telegram_id
                   WHERE l.tenant_id=%s AND l.connection_instance_id=%s AND l.connection_generation=%s
                   AND l.operator_login=%s AND l.operator_account=%s AND l.revoked_at IS NULL AND m.active
                   FOR UPDATE OF l FOR SHARE OF m""", (tenant, instance, generation, login, account))
        link = cur.fetchone()
        if not link:
            raise operator.OperatorError("Нет действующей привязки сотрудника.", 409)
        envelope = {"binding": _binding(tenant, link), "token": candidate["token"]}
        sealed = operator._fernet().encrypt(operator._json(envelope).encode())
        cur.execute("UPDATE saas_beinsystems_operator_employee_links SET widget_reply_ciphertext=%s WHERE id=%s",
                    (sealed, link["id"]))
        cur.execute("""UPDATE saas_beinsystems_operator_pending_auth_v2 SET activated_at=now()
                       WHERE tenant_id=%s AND owner_telegram_id=%s AND received_at=%s""",
                    (tenant, actor, candidate["received_at"]))
        cur.execute("""INSERT INTO saas_tenant_audit_events
                       (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                       VALUES(%s,%s,%s,'widget_reply_login_bound','employee',%s,%s::jsonb)""",
                    (str(uuid.uuid4()), tenant, actor, str(link["member_telegram_id"]),
                     operator._json({"employee_link": str(link["id"])})))
        db.commit()
