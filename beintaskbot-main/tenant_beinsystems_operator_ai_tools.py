"""Server-side read-only tool surface for the internal support copilot.

This registry is intentionally not enabled in a customer-facing agent. The
current copilot does not execute server function tools; integrations can wire
these functions only after the internal assistant's permission/context review.
"""
from __future__ import annotations

import uuid

import tenant_beinsystems_operator as operator


TOOLS = (
    {
        "name": "operator_case_context",
        "description": "Read the confirmed Operator account saved on this support case.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "read_only": True,
        "customer_visible": False,
    },
    {
        "name": "operator_account_summary",
        "description": "Read documented, nonfinancial account summary fields for the confirmed case account.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "read_only": True,
        "customer_visible": False,
    },
    {
        "name": "operator_profile_users",
        "description": "List logins under the confirmed account; login is paired with account as the profile identity.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "read_only": True,
        "customer_visible": False,
    },
    {
        "name": "operator_related_tasks",
        "description": "Read tasks for the confirmed account after /task/list response fields are confirmed.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        "read_only": True,
        "customer_visible": False,
        "enabled": False,
    },
)


def registry():
    """Return immutable-by-convention metadata; never return credentials."""
    return [dict(tool) for tool in TOOLS]


def _case_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise operator.OperatorError("Идентификатор кейса некорректен.") from None


def _confirmed_case_account(profile, ticket_id):
    links = operator.links_for_local(
        profile, local_entity_type="support_ticket", local_entity_id=_case_id(ticket_id))
    accounts = {
        row["external_id"] for row in links
        if row["status"] == "confirmed" and row["external_type"] == "account"
    }
    if len(accounts) != 1:
        return "", "ambiguous" if accounts else "unlinked"
    return next(iter(accounts)), "confirmed"


def invoke(profile, ticket_id, name, arguments=None):
    """Execute a read-only tool against one authorized, confirmed support case.

    The model cannot provide a tenant, account, profile login, or external ID.
    The authenticated employee and current support-case mapping supply scope.
    """
    args = arguments if isinstance(arguments, dict) else {}
    if args:
        raise operator.OperatorError("Инструмент Operator не принимает свободные идентификаторы.")
    item = next((tool for tool in TOOLS if tool["name"] == name), None)
    if not item or item.get("enabled") is False:
        raise operator.OperatorError("Инструмент Operator пока отключён.", 409)
    account, status = _confirmed_case_account(profile, ticket_id)
    if status != "confirmed":
        return {"status": status, "source": "Beintaskbot support case", "data": []}
    if name == "operator_case_context":
        return {"status": "confirmed", "account": account,
                "source": "Manually confirmed case relation"}
    if name == "operator_account_summary":
        response = operator.read(profile, "account_info", {"account": account})
        return {"status": "confirmed", "account_info": operator.account_info_payload(response, account),
                "source": "/user/info allowlisted fields"}
    if name == "operator_profile_users":
        response = operator.read(profile, "profile_users", {"account": account})
        return {"status": "confirmed", "users": operator.profile_users_payload(response),
                "source": "/user/profileUsers, scoped by account"}
    raise operator.OperatorError("Инструмент Operator не поддерживается.")
