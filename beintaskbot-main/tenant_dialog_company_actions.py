"""Explicit Operator writes against a verified dialogue company."""
import hashlib
import json
import uuid

from requests import RequestException

import tenant_beinsystems_operator as operator
import tenant_dialog_company_public_link as links
from tenant_finance_policy import minor_amount, TenantFinanceError
from tenant_beinsystems_operator_access import operator_api_allowed


PROJECTS = ("Akul Admin", "Akul POS", "Akul PWA", "Akul Mobile", "Akul Counting",
            "Dine Admin", "Dine POS", "Dine PWA", "Dine QR", "Umumi")
PATHS = {"task": "/task/create", "credit": "/finance/addCredit"}


def capabilities(profile):
    enabled = operator_api_allowed(profile)
    return {"task": enabled, "credit": enabled}


def _text(fields, key, limit):
    value = fields.get(key)
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        raise operator.OperatorError("Invalid " + key, 400)
    return value.strip()


def payload(kind, fields):
    if not isinstance(fields, dict):
        raise operator.OperatorError("Invalid fields", 400)
    if kind == "task" and set(fields) == {"title","description","project","priority"}:
        priority = fields["priority"]
        if type(priority) is not int or priority not in (2,3,4) or fields["project"] not in PROJECTS:
            raise operator.OperatorError("Invalid task options", 400)
        return {"title":_text(fields,"title",240), "description":_text(fields,"description",8000),
                "project":fields["project"], "priority":priority}
    if kind == "credit" and set(fields) == {"amount","info"}:
        try:
            amount = minor_amount(fields["amount"])
        except TenantFinanceError as error:
            raise operator.OperatorError("Invalid amount", 400) from error
        return {"amount":amount / 100, "info":_text(fields,"info",500)}
    raise operator.OperatorError("Invalid operation", 400)


def _send(kind, data, credentials):
    base = credentials["base_url"]
    if base != operator.OPERATOR_API_BASE_URL or base != operator.api_base_url():
        raise operator.OperatorError("Connection changed", 409)
    with operator._NoRedirectRequestsSession() as client:
        client.trust_env = False
        try:
            with client.post(base + PATHS[kind], json=data, allow_redirects=False, stream=True,
                             headers={"Authorization":"Bearer "+credentials["token"]},
                             timeout=operator.REQUEST_TIMEOUT_SECONDS) as reply:
                if reply.status_code in (401,403):
                    return "denied"
                if not 200 <= reply.status_code < 300:
                    return "unconfirmed"
                raw = bytearray()
                for chunk in reply.iter_content(4096):
                    if len(raw) + len(chunk) > 65536:
                        return "unconfirmed"
                    raw.extend(chunk)
                result = json.loads(raw)
                if isinstance(result,dict) and (result.get("success") is True or
                        result.get("status") == "success") and not result.get("error"):
                    return "done"
        except (RequestException, ValueError):
            pass
    return "unconfirmed"


def perform(profile, *, provider, lead_id, channel_id, conversation_id, expected_company_id,
            kind, request_id, fields):
    tenant = operator._tenant(profile)
    actor = int(profile["telegram_id"])
    route = links.dialogue._key(provider,lead_id,channel_id,conversation_id)
    company = links.dialogue.normalize_company_id(expected_company_id)
    data = payload(kind,fields)
    try:
        token = uuid.UUID(str(request_id))
        if token.version != 4 or not company:
            raise ValueError()
    except ValueError:
        raise operator.OperatorError("Invalid operation identity",400) from None
    credentials = operator._credentials(tenant)
    with links.dialogue._connect() as db, db.cursor() as cur:
        scope = links._locked_snapshot(cur,profile,route,company)
        if isinstance(scope,str):
            raise operator.OperatorError("No verified company",409)
        _, account, generation, instance = scope
        if (generation,instance) != (credentials["generation"],credentials["connection_instance_id"]):
            raise operator.OperatorError("Connection changed",409)
        if not capabilities(operator._current_profile(cur,tenant,actor))[kind]:
            raise operator.OperatorError("Access denied",403)
        data["account"] = account
        signature = hashlib.sha256(json.dumps([company,kind,data],sort_keys=True).encode()).hexdigest()
        key = (tenant,str(token))
        cur.execute("""INSERT INTO saas_operator_company_actions
          (tenant_id,request_id,actor,company_id,kind,fingerprint,state)
          VALUES (%s,%s,%s,%s,%s,%s,'unconfirmed') ON CONFLICT DO NOTHING RETURNING request_id""",
                    (*key,actor,company,kind,signature))
        if not cur.fetchone():
            cur.execute("""SELECT actor,fingerprint,state FROM saas_operator_company_actions
                WHERE tenant_id=%s AND request_id=%s""",key)
            row = cur.fetchone()
            if row and (row["actor"] != actor or row["fingerprint"] != signature):
                raise operator.OperatorError("Operation identity changed",409)
            return row["state"] if row else "unconfirmed"
        db.commit()
    # Reacquire the dialogue and current member locks after committing the reservation.
    with links.dialogue._connect() as db, db.cursor() as cur:
        try:
            fresh = links._locked_snapshot(cur,profile,route,company)
            allowed = capabilities(operator._current_profile(cur,tenant,actor))[kind]
            if fresh != scope or not allowed:
                raise operator.OperatorError("Context changed",409)
        except (operator.OperatorError, links.TenantLinearError):
            cur.execute("DELETE FROM saas_operator_company_actions WHERE tenant_id=%s AND request_id=%s",key)
            db.commit()
            raise
        state = _send(kind,data,credentials)
        if state != "unconfirmed":
            cur.execute("""UPDATE saas_operator_company_actions SET state=%s
                WHERE tenant_id=%s AND request_id=%s""",(state,*key))
            db.commit()
    return state
