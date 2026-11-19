"""One-way creation of native CRM companies from a fresh active Operator account list.

This path never consumes the pending-token slot or the merged account catalogue,
and it never writes to the Operator API.
"""
from __future__ import annotations

import uuid

import tenant_beinsystems_operator as operator
from tenant_crm_native_access import (
    authorize_create,
    current_access,
    locked_resource,
)
from tenant_policy import TenantPolicy


def _owner(profile, current):
    if profile.get("role") != "owner" or current.get("role") != "owner":
        raise operator.OperatorError(
            "Создавать компании из Operator может только владелец workspace.", 403
        )


def _connection_is_current(row, credentials, *, locked=False):
    metadata = row.get("metadata") if isinstance(row, dict) else None
    if not isinstance(row, dict) or row.get("status") not in {"connected", "configured"}:
        return False
    if not row.get("secrets") or not isinstance(metadata, dict):
        return False
    return (
        str(metadata.get("generation") or "") == credentials.get("generation")
        and str(metadata.get("connection_instance_id") or "")
        == credentials.get("connection_instance_id")
    )


def _same_credentials(left, right):
    return all(
        left.get(key) == right.get(key)
        for key in ("token", "base_url", "generation", "connection_instance_id", "status")
    )


def _fresh_active_accounts(profile, tenant, actor):
    """Return one validated /me + /accounts snapshot from the current active token."""
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            row = operator._operator_row(cur, tenant)
            if not isinstance(row, dict) or row.get("status") not in {"connected", "configured"}:
                raise operator.OperatorError(
                    "Сначала подключите и проверьте действующее подключение Operator.", 409
                )
    credentials = operator._credentials(tenant, allow_configured=True)
    if credentials.get("status") not in {"connected", "configured"}:
        raise operator.OperatorError("Действующее подключение Operator недоступно.", 409)

    operator._record_operator_read(profile, "me", {})
    identity = operator.operator_me_payload(operator._request_operator(
        "me", {}, token=credentials["token"], base_url=credentials["base_url"]
    ))
    operator._record_operator_read(profile, "accounts", {})
    response = operator._request_operator(
        "accounts", {}, token=credentials["token"], base_url=credentials["base_url"]
    )
    accounts = operator.accounts_import_payload(response)

    fresh = operator._credentials(tenant, allow_configured=True)
    if not _same_credentials(credentials, fresh):
        raise operator.OperatorError(
            "Подключение Operator изменилось во время чтения. Повторите попытку.", 409
        )
    # Identity is deliberately validated but never used as Beintaskbot permission.
    if not identity.get("identifier"):
        raise operator.OperatorError("Operator не подтвердил подключённый профиль.", 403)
    return accounts, credentials


def _plan_company_import(accounts, mappings, confirmed_links, visible_company_ids,
                         existing_name_keys):
    """Plan only from fresh account IDs and explicit stable/manual identity evidence."""
    actions = []
    reserved_names = set(existing_name_keys)
    for item in accounts:
        account = item["account"]
        mapped_id = mappings.get(account)
        linked_ids = set(confirmed_links.get(account, ()))
        if mapped_id:
            if (
                len(linked_ids) > 1
                or (linked_ids and mapped_id not in linked_ids)
                or mapped_id not in visible_company_ids
            ):
                actions.append({"account": account, "action": "manual_review"})
            else:
                actions.append({
                    "account": account, "action": "reuse", "company_id": mapped_id
                })
            continue
        if len(linked_ids) > 1:
            actions.append({"account": account, "action": "manual_review"})
            continue
        if len(linked_ids) == 1:
            company_id = next(iter(linked_ids))
            if company_id in visible_company_ids:
                actions.append({
                    "account": account, "action": "bind", "company_id": company_id
                })
            else:
                actions.append({"account": account, "action": "manual_review"})
            continue
        name_key = account.lower()
        if name_key in reserved_names:
            # A name collision is only a reason to ask for manual binding;
            # it is never evidence that two records represent the same company.
            actions.append({"account": account, "action": "manual_review"})
            continue
        actions.append({"account": account, "action": "create"})
        reserved_names.add(name_key)
    return actions


def _put_mapping(cur, tenant, account, company_id, actor, credentials):
    cur.execute(
        """INSERT INTO saas_beinsystems_operator_company_imports
          (tenant_id,external_account,company_id,created_by,updated_by,
           last_seen_connection_instance_id,last_seen_generation,last_seen_at,updated_at)
          VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s,now(),now())
          ON CONFLICT(tenant_id,external_account) DO UPDATE SET
            updated_by=EXCLUDED.updated_by,
            last_seen_connection_instance_id=EXCLUDED.last_seen_connection_instance_id,
            last_seen_generation=EXCLUDED.last_seen_generation,
            last_seen_at=now(),updated_at=now()
          WHERE saas_beinsystems_operator_company_imports.company_id=EXCLUDED.company_id
          RETURNING company_id""",
        (tenant, account, company_id, actor, actor,
         credentials["connection_instance_id"], credentials["generation"]),
    )
    return cur.fetchone() is not None


def create_companies(profile):
    """Create only new local CRM companies present in the active connection's fresh list."""
    tenant = operator._tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise operator.OperatorError("Нет доступа.") from None

    accounts, credentials = _fresh_active_accounts(profile, tenant, actor)
    try:
        instance_id = str(uuid.UUID(credentials["connection_instance_id"]))
    except (TypeError, ValueError, KeyError):
        raise operator.OperatorError("Подключение Operator нужно повторно проверить.", 409) from None
    credentials = {**credentials, "connection_instance_id": instance_id}

    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            integration = operator._operator_row(cur, tenant, lock=True)
            if not _connection_is_current(integration, credentials, locked=True):
                raise operator.OperatorError(
                    "Подключение Operator изменилось во время импорта. Повторите попытку.", 409
                )
            # Check the encrypted active token again while the integration row is locked.
            try:
                import json
                from cryptography.fernet import InvalidToken
                secret = json.loads(
                    operator._fernet().decrypt(bytes(integration["secrets"])).decode("utf-8")
                )
            except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError,
                    TypeError, ValueError, KeyError):
                raise operator.OperatorError(
                    "Действующее подключение Operator не удалось проверить.", 409
                ) from None
            if (not isinstance(secret, dict)
                    or secret.get("token") != credentials["token"]
                    or secret.get("connection_instance_id")
                    != credentials["connection_instance_id"]):
                raise operator.OperatorError(
                    "Подключение Operator изменилось во время импорта. Повторите попытку.", 409
                )

            create_profile = current_access(cur, tenant, actor, "companies", "create")
            view_profile = current_access(cur, tenant, actor, "companies", "view")
            if (TenantPolicy(create_profile).has_scoped_access("companies")
                    or TenantPolicy(view_profile).has_scoped_access("companies")):
                raise operator.OperatorError(
                    "Для безопасного импорта нужен полный доступ к CRM-компаниям.", 403
                )
            authorize_create(cur, create_profile, "companies", tenant, actor)

            # Serialize retries for this tenant. The stable account key and unique
            # mapping constraint make repeated imports idempotent.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(tenant) + ":operator-company-import",),
            )

            account_ids = [item["account"] for item in accounts]
            mappings = {}
            confirmed_links = {}
            visible_company_ids = set()
            existing_name_keys = set()
            if account_ids:
                cur.execute(
                    """SELECT external_account,company_id::text AS company_id
                      FROM saas_beinsystems_operator_company_imports
                      WHERE tenant_id=%s::uuid AND external_account=ANY(%s::text[])
                      FOR UPDATE""",
                    (tenant, account_ids),
                )
                mappings = {
                    str(row["external_account"]): str(row["company_id"])
                    for row in cur.fetchall()
                }
                cur.execute(
                    """SELECT external_id,local_entity_id::text AS company_id
                      FROM saas_beinsystems_operator_links
                      WHERE tenant_id=%s::uuid AND local_entity_type='company'
                        AND external_entity_type='account' AND external_scope_id=''
                        AND connection_generation=%s AND connection_instance_id=%s::uuid
                        AND revoked_at IS NULL AND external_id=ANY(%s::text[])
                      FOR UPDATE""",
                    (tenant, credentials["generation"], instance_id, account_ids),
                )
                for row in cur.fetchall():
                    confirmed_links.setdefault(str(row["external_id"]), []).append(
                        str(row["company_id"])
                    )
                cur.execute(
                    """SELECT lower(name) AS name_key
                      FROM saas_crm_native_companies
                      WHERE tenant_id=%s::uuid AND lower(name)=ANY(%s::text[])
                      FOR UPDATE""",
                    (tenant, [account.lower() for account in account_ids]),
                )
                existing_name_keys = {
                    str(row["name_key"]) for row in cur.fetchall()
                }
                candidates = set(mappings.values())
                candidates.update(
                    company_id
                    for company_ids in confirmed_links.values()
                    for company_id in company_ids
                )
                for company_id in candidates:
                    resource = locked_resource(
                        cur, view_profile, "companies", "view",
                        "saas_crm_native_companies", tenant, company_id,
                    )
                    if resource:
                        visible_company_ids.add(str(resource["id"]))

            plan = _plan_company_import(
                accounts, mappings, confirmed_links, visible_company_ids, existing_name_keys
            )
            created = reused = manual_review = 0
            for action in plan:
                account = action["account"]
                kind = action["action"]
                if kind == "manual_review":
                    manual_review += 1
                    continue
                if kind == "create":
                    company_id = str(uuid.uuid4())
                    cur.execute(
                        """INSERT INTO saas_crm_native_companies
                          (id,tenant_id,name,phone,email,description,responsible_id,
                           access_responsible_id)
                          VALUES(%s::uuid,%s::uuid,%s,'','','',NULL,%s)
                          RETURNING id""",
                        (company_id, tenant, account, actor),
                    )
                    row = cur.fetchone()
                    if not row or not _put_mapping(
                        cur, tenant, account, company_id, actor, credentials
                    ):
                        raise operator.OperatorError(
                            "Связь аккаунта с CRM-компанией изменилась. Импорт отменён.", 409
                        )
                    created += 1
                    continue
                company_id = action["company_id"]
                if not _put_mapping(cur, tenant, account, company_id, actor, credentials):
                    manual_review += 1
                else:
                    reused += 1

            cur.execute(
                """INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'operator_companies_imported',
                    'operator_company_import','fresh_active_accounts',%s::jsonb)""",
                (str(uuid.uuid4()), tenant, actor, operator._json({
                    "checked": len(accounts), "created": created,
                    "reused": reused, "manual_review": manual_review,
                    "connection_generation": credentials["generation"],
                })),
            )
        conn.commit()
    return {
        "checked": len(accounts), "created": created,
        "reused": reused, "manual_review": manual_review,
    }
