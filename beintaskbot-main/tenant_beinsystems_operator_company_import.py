"""One-way creation of native CRM companies from a fresh active Operator account list.

This path never consumes the pending-token slot or the merged account catalogue,
and it never writes to the Operator API.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import dialogue_chooser_enabled
from cryptography.fernet import InvalidToken
from tenant_crm_native_access import (
    CRMAccessError,
    authorize_create,
    current_access,
    locked_resource,
)
from tenant_operator_company_name_guard import (
    lock_name_writes, require_unreserved_account_pair, require_unreserved_name,
)
from tenant_policy import TenantPolicy


MANUAL_REVIEW_ACCOUNT_LIMIT = 20
ACCOUNTS_CAPABILITY_TTL_HOURS = 24
FIRST_BATCH_LIMIT = 100
FIRST_BATCH_PREVIEW_TTL = 300
_FIRST_BATCH_PURPOSE = "operator_first_company_batch_v2"


def _first_batch_payload(response):
    if (not isinstance(response, dict) or set(response) != {"status", "data"}
            or response.get("status") != "success"
            or not isinstance(response.get("data"), list)
            or len(response["data"]) > FIRST_BATCH_LIMIT):
        raise operator.OperatorError("Operator не вернул допустимую первую выборку аккаунтов.", 409)
    result, seen = [], set()
    for row in response["data"]:
        account = row.get("account") if isinstance(row, dict) else None
        if (not isinstance(account, str) or not account or account != account.strip()
                or len(account) > 240 or any(ord(c) < 32 or ord(c) == 127 for c in account)
                or account.casefold() in seen):
            raise operator.OperatorError("Первая выборка содержит недопустимый account ID.", 409)
        seen.add(account.casefold())
        result.append({"account": account})
    return sorted(result, key=lambda item: item["account"])


def _first_batch_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def _first_batch_scope(tenant, actor, credentials):
    # No credential or account identifiers are shipped in the opaque receipt.
    return _first_batch_digest([tenant, actor, _revision(credentials["received_at"]),
        credentials["generation"], credentials["connection_instance_id"],
        credentials["identity_fingerprint"], credentials["base_url"],
        hashlib.sha256(credentials["token"].encode("utf-8")).hexdigest()])


def _decode_first_batch_receipt(receipt):
    invalid = "Подтверждение первой выборки недействительно. Откройте новый предпросмотр."
    if not isinstance(receipt, str) or not receipt or len(receipt) > 2048:
        raise operator.OperatorError(invalid, 409)
    try:
        claims = json.loads(operator._fernet().decrypt(
            receipt.encode("ascii"), ttl=FIRST_BATCH_PREVIEW_TTL))
        if (not isinstance(claims, dict)
                or set(claims) != {"purpose", "nonce", "scope", "digest"}
                or claims["purpose"] != _FIRST_BATCH_PURPOSE
                or str(uuid.UUID(claims["nonce"])) != claims["nonce"]):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, InvalidToken):
        raise operator.OperatorError(invalid, 409) from None
    return claims


def _require_first_batch_plan_enabled(tenant):
    if not operator.first_company_batch_plan_enabled(tenant):
        raise operator.OperatorError("Создание компаний из первой выборки отключено.", 403)


def first_company_batch(profile, *, receipt=None, plan=False):
    """Keep the source list read-only; only a separate guarded plan can mint a receipt."""
    if not operator.first_company_batch_preview_enabled():
        raise operator.OperatorError("Первая выборка аккаунтов отключена.", 404)
    planning = plan or receipt is not None
    if planning:
        _require_first_batch_plan_enabled((profile or {}).get("tenant_id"))
    claims = None if receipt is None else _decode_first_batch_receipt(receipt)
    tenant, actor, source, credentials = _selected_source_credentials(profile)
    if source != "accounts_only":
        raise operator.OperatorError("Первая выборка доступна только для режима accounts-only.", 409)
    scope = _first_batch_scope(tenant, actor, credentials)
    if claims is not None and claims["scope"] != scope:
        raise operator.OperatorError("Доступ Operator изменился. Откройте новый предпросмотр.", 409)

    operator._record_operator_read(profile, "me", {})
    _selected_source_fence(profile, tenant, actor, source, credentials)
    identity = operator.operator_me_payload(operator._request_operator(
        "me", {}, token=credentials["token"], base_url=credentials["base_url"], requests_me=True))
    _selected_source_fence(profile, tenant, actor, source, credentials)
    if _identifier_fingerprint(identity["identifier"]) != credentials["identity_fingerprint"]:
        raise operator.OperatorError("Подтверждённый профиль Operator изменился.", 409)
    operator._record_operator_read(profile, "accounts", {})
    _selected_source_fence(profile, tenant, actor, source, credentials)
    response = operator._request_operator("accounts", {"search": "", "limit": FIRST_BATCH_LIMIT},
        token=credentials["token"], base_url=credentials["base_url"], first_batch_accounts=True)
    accounts = _first_batch_payload(response)
    _selected_source_fence(profile, tenant, actor, source, credentials)
    if not planning:
        return _preview_operator_accounts_only(profile, tenant, actor, accounts, credentials)
    return _apply_company_import(profile, tenant, actor, accounts, credentials,
        first_batch=True, preview_receipt=receipt, preview_scope=scope)



def _revision(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _identifier_fingerprint(identifier):
    normalized = str(identifier or "").strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _pending_owner_candidate(profile, expected_revision=None):
    tenant = operator._tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise operator.OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise operator.OperatorError("Только владелец может подтвердить доступ к аккаунтам.", 403)
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            integration = operator._operator_row(cur, tenant)
    if (integration and integration.get("status") in {"connected", "configured"}
            and integration.get("secrets")):
        raise operator.OperatorError(
            "Действующее подключение уже доступно. Используйте его для поиска аккаунтов.", 409)
    candidate = operator._pending_auth_candidate(profile, required=False)
    if not candidate:
        raise operator.OperatorError("Сначала получите JWT из карточки интеграции.", 409)
    if expected_revision is not None and _revision(candidate["received_at"]) != str(expected_revision):
        raise operator.OperatorError("JWT Operator был заменён. Начните подтверждение заново.", 409)
    return tenant, actor, candidate


def _pending_candidate_fence(profile, tenant, actor, candidate):
    operator._assert_current_operator_owner(
        tenant, actor, pending_received_at=candidate["received_at"])
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            row = operator._operator_row(cur, tenant)
            if row and row.get("status") in {"connected", "configured"} and row.get("secrets"):
                raise operator.OperatorError("Подключение Operator изменилось. Повторите попытку.", 409)


def _verified_pending_identity(profile, expected_revision=None):
    tenant, actor, candidate = _pending_owner_candidate(profile, expected_revision)
    operator._record_operator_read(profile, "me", {})
    _pending_candidate_fence(profile, tenant, actor, candidate)
    identity = operator.operator_me_payload(operator._request_operator(
        "me", {}, token=candidate["token"], base_url=candidate["base_url"],
        requests_me=True))
    _pending_candidate_fence(profile, tenant, actor, candidate)
    return tenant, actor, candidate, identity


def prepare_accounts_capability(profile, candidate_received_at=None):
    """Owner-initiated identity preview; it persists no capability or token copy."""
    _, _, candidate, identity = _verified_pending_identity(profile, candidate_received_at)
    return {"identifier": identity["identifier"],
            "candidate_received_at": _revision(candidate["received_at"])}


def confirm_accounts_capability(profile, *, acknowledged_identifier, candidate_received_at):
    """Persist an expiring accounts-only grant bound to the owner and pending revision."""
    if not isinstance(acknowledged_identifier, str):
        raise operator.OperatorError("Укажите подтверждённую учётную запись Operator.")
    acknowledged = acknowledged_identifier.strip()
    if not acknowledged or len(acknowledged) > 240 or any(ord(c) < 32 for c in acknowledged):
        raise operator.OperatorError("Укажите подтверждённую учётную запись Operator.")
    tenant, actor, candidate, identity = _verified_pending_identity(
        profile, candidate_received_at)
    if acknowledged.casefold() != identity["identifier"].casefold():
        raise operator.OperatorError(
            "Указанная учётная запись не совпадает с проверенной Operator.", 409)
    fingerprint = _identifier_fingerprint(identity["identifier"])
    generation, instance = str(uuid.uuid4()), str(uuid.uuid4())
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            operator._lock_accounts_capability_scope(cur, tenant)
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            integration = operator._operator_row(cur, tenant, lock=True)
            if (integration and integration.get("status") in {"connected", "configured"}
                    and integration.get("secrets")):
                raise operator.OperatorError("Подключение Operator изменилось. Повторите попытку.", 409)
            cur.execute("""SELECT received_at FROM saas_beinsystems_operator_pending_auth_v2
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND activated_at IS NULL
              FOR UPDATE""", (tenant, actor))
            pending = cur.fetchone()
            if not pending or pending.get("received_at") != candidate["received_at"]:
                raise operator.OperatorError("JWT Operator был заменён. Начните подтверждение заново.", 409)
            cur.execute("""INSERT INTO saas_beinsystems_operator_accounts_capabilities
              (tenant_id,owner_telegram_id,candidate_received_at,operator_identifier_fingerprint,
               connection_generation,connection_instance_id,expires_at)
              VALUES(%s::uuid,%s,%s,%s,%s,%s::uuid,now()+(%s * interval '1 hour'))
              ON CONFLICT(tenant_id) DO UPDATE SET owner_telegram_id=EXCLUDED.owner_telegram_id,
                candidate_received_at=EXCLUDED.candidate_received_at,
                operator_identifier_fingerprint=EXCLUDED.operator_identifier_fingerprint,
                connection_generation=EXCLUDED.connection_generation,
                connection_instance_id=EXCLUDED.connection_instance_id,
                expires_at=EXCLUDED.expires_at,created_at=now()""",
              (tenant, actor, candidate["received_at"], fingerprint, generation, instance,
               ACCOUNTS_CAPABILITY_TTL_HOURS))
        conn.commit()
    return {"ready": True, "label": "accounts_only",
            "expires_in_hours": ACCOUNTS_CAPABILITY_TTL_HOURS}


def _accounts_capability_credentials(profile):
    tenant = operator._tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise operator.OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise operator.OperatorError("Добавлять аккаунт может только владелец workspace.", 403)
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            integration = operator._operator_row(cur, tenant)
            if integration and integration.get("status") in {"connected", "configured"} \
                    and integration.get("secrets"):
                raise operator.OperatorError("Подключение Operator изменилось. Повторите попытку.", 409)
            cur.execute("""SELECT owner_telegram_id,candidate_received_at,
                operator_identifier_fingerprint,connection_generation,connection_instance_id::text,
                expires_at FROM saas_beinsystems_operator_accounts_capabilities
              WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND expires_at>now()""",
              (tenant, actor))
            capability = cur.fetchone()
    if not capability:
        raise operator.OperatorError(
            "Сначала подтвердите аккаунтный доступ для сохранённого JWT Operator.", 403)
    candidate = operator._pending_auth_candidate(profile, required=False)
    if (not candidate or candidate["received_at"] != capability["candidate_received_at"]):
        raise operator.OperatorError("Подтверждение устарело. Подтвердите текущий JWT заново.", 409)
    return {**candidate, "generation": capability["connection_generation"],
            "connection_instance_id": str(capability["connection_instance_id"]),
            "identity_fingerprint": capability["operator_identifier_fingerprint"],
            "status": "accounts_only"}


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
            actions.append({"account": account, "action": "manual_review",
                            "reason": "name_collision"})
            continue
        actions.append({"account": account, "action": "create"})
        reserved_names.add(name_key)
    return actions


def _name_collision_summary(plan):
    accounts = [action["account"] for action in plan
                if action.get("action") == "manual_review"
                and action.get("reason") == "name_collision"]
    visible = accounts[:MANUAL_REVIEW_ACCOUNT_LIMIT]
    return {
        "name_collision_count": len(accounts),
        "name_collision_accounts": visible,
        "name_collision_truncated": len(accounts) > len(visible),
    }


def _require_import_name(cur, tenant, account, company_id):
    # All import writers already hold the shared tenant namespace advisory lock.
    try:
        require_unreserved_name(cur,tenant,account,company_id)
    except CRMAccessError:
        raise operator.OperatorError('Название занято. Проверьте существующую компанию CRM.',409) from None


def _put_mapping(cur, tenant, account, company_id, actor, credentials):
    if dialogue_chooser_enabled(tenant):
        # All mapping callers acquired the common namespace first. Also fence
        # non-create paths that turn an existing explicit link into a mapping.
        try:
            require_unreserved_account_pair(cur,tenant,account,company_id)
        except CRMAccessError:
            return False
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
    return _apply_company_import(profile, tenant, actor, accounts, credentials)


def _first_batch_prior_identity(cur, tenant, account_ids):
    """Read reserved identity evidence from any generation; it is never a binding grant."""
    targets = {}
    cur.execute("""SELECT a.account AS requested, m.external_account,
                    m.company_id::text AS company_id
                  FROM unnest(%s::text[]) AS a(account)
                  JOIN saas_beinsystems_operator_company_imports m
                    ON m.tenant_id=%s::uuid AND lower(m.external_account)=lower(a.account)
                  FOR UPDATE OF m""", (account_ids, tenant))
    exact_mappings = {}
    for row in cur.fetchall():
        account, company = row["requested"], str(row["company_id"])
        targets.setdefault(account, set()).add(company)
        if account == row["external_account"]:
            exact_mappings[account] = company
    cur.execute("""SELECT a.account AS requested, l.local_entity_id::text AS company_id
                  FROM unnest(%s::text[]) AS a(account)
                  JOIN saas_beinsystems_operator_links l
                    ON l.tenant_id=%s::uuid AND lower(l.external_id)=lower(a.account)
                  WHERE l.local_entity_type='company' AND l.external_entity_type='account'
                    AND l.external_scope_id='' AND l.revoked_at IS NULL
                  FOR UPDATE OF l""", (account_ids, tenant))
    for row in cur.fetchall():
        targets.setdefault(row["requested"], set()).add(str(row["company_id"]))
    return exact_mappings, targets


def _assert_first_batch_not_expired(cur, tenant, actor, credentials):
    # The capability/candidate rows are already locked, but wall time may have
    # advanced while waiting for the import or CRM rows. now() is transaction-start.
    cur.execute("""SELECT 1 FROM saas_beinsystems_operator_accounts_capabilities
        WHERE tenant_id=%s::uuid AND owner_telegram_id=%s
          AND connection_generation=%s AND expires_at>clock_timestamp()""",
        (tenant, actor, credentials["generation"]))
    if not cur.fetchone():
        raise operator.OperatorError("Аккаунтный доступ Operator истёк. Откройте новый предпросмотр.", 409)


def _preview_operator_accounts_only(profile, tenant, actor, accounts, credentials):
    # Explicitly source-only. No CRM policy, planner, company/association lookup
    # or import lock is necessary to display the bounded provider response.
    with operator._connect() as conn:
        with conn.cursor() as cur:
            operator._lock_accounts_capability_scope(cur, tenant)
            _owner(profile, operator._current_profile(cur, tenant, actor))
            _lock_and_check_selected_source(cur, tenant, actor, "accounts_only", credentials)
            _assert_first_batch_not_expired(cur, tenant, actor, credentials)
            if not operator.first_company_batch_preview_enabled():
                raise operator.OperatorError("Первая выборка аккаунтов отключена.", 404)
    return {"checked": len(accounts), "partial": True, "limit": FIRST_BATCH_LIMIT,
            "mode": "accounts_only", "rows": [{"account": row["account"]} for row in accounts]}


def _apply_company_import(profile, tenant, actor, accounts, credentials, *,
                          first_batch=False, preview_receipt=None, preview_scope=None):
    try:
        instance_id = str(uuid.UUID(credentials["connection_instance_id"]))
    except (TypeError, ValueError, KeyError):
        raise operator.OperatorError("Подключение Operator нужно повторно проверить.", 409) from None
    credentials = {**credentials, "connection_instance_id": instance_id}

    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            namespace_locked=lock_name_writes(cur,tenant,wait=True)
            if first_batch:
                _require_first_batch_plan_enabled(tenant)
                if not namespace_locked:
                    raise operator.OperatorError("Защита имён компаний Operator отключена.", 403)
                operator._lock_accounts_capability_scope(cur, tenant)
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            if first_batch:
                _lock_and_check_selected_source(cur, tenant, actor, "accounts_only", credentials)
            else:
                integration = operator._operator_row(cur, tenant, lock=True)
                if not _connection_is_current(integration, credentials, locked=True):
                    raise operator.OperatorError(
                        "Подключение Operator изменилось во время импорта. Повторите попытку.", 409
                    )
                # Check the encrypted active token again while the integration row is locked.
                try:
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

            if first_batch:
                try:
                    create_profile = current_access(cur, tenant, actor, "companies", "create")
                    view_profile = current_access(cur, tenant, actor, "companies", "view")
                    for p, action in ((create_profile, "create"), (view_profile, "view")):
                        policy = TenantPolicy(p)
                        # For an active owner, allowed legacy (None) is tenant-wide;
                        # configured scopes already normalize to their effective value.
                        if (p.get("role") != "owner" or not policy.allows_action("companies", action)
                                or policy.action_scope("companies", action) not in {None, "all"}):
                            raise CRMAccessError()
                    authorize_create(cur, create_profile, "companies", tenant, actor)
                except CRMAccessError:
                    raise operator.OperatorError(
                        "Для безопасного импорта нужен полный доступ к CRM-компаниям.", 403
                    ) from None
            else:
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
            if not namespace_locked:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(tenant) + ":operator-company-import",),
                )

            account_ids = [item["account"] for item in accounts]
            mappings = {}
            confirmed_links = {}
            visible_company_ids = set()
            existing_name_keys = set()
            prior_targets = {}
            if account_ids:
                if first_batch:
                    mappings, prior_targets = _first_batch_prior_identity(cur, tenant, account_ids)
                else:
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
                if first_batch:
                    cur.execute("""SELECT a.account FROM unnest(%s::text[]) AS a(account)
                        JOIN saas_crm_native_companies c
                          ON c.tenant_id=%s::uuid AND lower(c.name)=lower(a.account)
                        FOR UPDATE OF c""", (account_ids, tenant))
                    existing_name_keys = {row["account"].lower() for row in cur.fetchall()}
                else:
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
            if first_batch:
                # Historical links and case variants can prevent a duplicate but
                # cannot authorize reusing or rebinding a CRM company. A stable exact
                # mapping can be reused without touching it only if all prior targets agree.
                plan = [
                    action if not prior_targets.get(action["account"]) or (
                        action["action"] == "reuse"
                        and prior_targets[action["account"]] == {action["company_id"]})
                    else {"account": action["account"], "action": "manual_review",
                          "reason": "prior_identity_conflict"}
                    for action in plan
                ]
                # A same-company case alias or conflicting reverse identity is
                # manual too; matching company IDs cannot override exact pair evidence.
                for index, action in enumerate(plan):
                    account = action["account"]
                    try:
                        if action["action"] == "reuse":
                            require_unreserved_account_pair(cur, tenant, account, action["company_id"])
                        elif action["action"] == "create":
                            require_unreserved_name(cur, tenant, account, str(uuid.uuid4()))
                    except CRMAccessError:
                        plan[index] = {"account": account, "action": "manual_review",
                                       "reason": "prior_identity_conflict"}
                _assert_first_batch_not_expired(cur, tenant, actor, credentials)
                _require_first_batch_plan_enabled(tenant)
                digest = _first_batch_digest(plan)
                if preview_receipt is None:
                    preview = {"checked": len(accounts), "partial": True, "limit": FIRST_BATCH_LIMIT,
                               "mode": "company_plan",
                               "rows": [{"account": a["account"], "action": a["action"]} for a in plan]}
                    if any(action["action"] == "create" for action in plan):
                        payload = {"purpose": _FIRST_BATCH_PURPOSE, "nonce": str(uuid.uuid4()),
                                   "scope": preview_scope, "digest": digest}
                        receipt = operator._fernet().encrypt(json.dumps(payload).encode("utf-8"))
                        preview["receipt"] = receipt.decode("ascii")
                    return preview
                claims = _decode_first_batch_receipt(preview_receipt)
                if claims["scope"] != preview_scope or claims["digest"] != digest:
                    raise operator.OperatorError("Выборка или план изменились. Откройте новый предпросмотр.", 409)
                # Audit ID has a primary key. Insert in the write transaction:
                # rollback permits retry, while concurrent/repeated confirmation cannot write twice.
                cur.execute("""INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s::uuid,%s::uuid,%s,'operator_first_company_batch',
                    'operator_company_import','first_batch',%s::jsonb)
                  ON CONFLICT(id) DO NOTHING RETURNING id""",
                  (claims["nonce"], tenant, actor, operator._json({"checked": len(accounts)})))
                if not cur.fetchone():
                    raise operator.OperatorError("Эта выборка уже подтверждена. Откройте новый предпросмотр.", 409)
            collision_summary = _name_collision_summary(plan)
            created = reused = manual_review = 0
            for action in plan:
                account = action["account"]
                kind = action["action"]
                if kind == "manual_review":
                    manual_review += 1
                    continue
                if kind == "create":
                    company_id = str(uuid.uuid4())
                    _require_import_name(cur,tenant,account,company_id)
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
                if first_batch:
                    reused += 1
                    continue
                if not _put_mapping(cur, tenant, account, company_id, actor, credentials):
                    manual_review += 1
                else:
                    reused += 1

            cur.execute(
                """INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'operator_companies_imported',
                    'operator_company_import',%s,%s::jsonb)""",
                (str(uuid.uuid4()), tenant, actor,
                 "first_bounded_accounts" if first_batch else "fresh_active_accounts", operator._json({
                    "checked": len(accounts), "created": created,
                    "reused": reused, "manual_review": manual_review,
                    "connection_generation": credentials["generation"],
                })),
            )
        conn.commit()
    return {
        "checked": len(accounts), "created": created,
        "reused": reused, "manual_review": manual_review,
        **collision_summary,
        **({"partial": True, "limit": FIRST_BATCH_LIMIT} if first_batch else {}),
    }


def _selected_source_credentials(profile):
    tenant = operator._tenant(profile)
    try:
        actor = int(profile.get("telegram_id"))
    except (TypeError, ValueError, AttributeError):
        raise operator.OperatorError("Нет доступа.") from None
    if profile.get("role") != "owner" or profile.get("active") is False:
        raise operator.OperatorError("Добавлять аккаунт может только владелец workspace.", 403)
    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            row = operator._operator_row(cur, tenant)
    active = bool(row and row.get("status") in {"connected", "configured"} and row.get("secrets"))
    if active:
        return tenant, actor, "active", operator._credentials(tenant, allow_configured=True)
    return tenant, actor, "accounts_only", _accounts_capability_credentials(profile)


def _selected_source_fence(profile, tenant, actor, source, credentials):
    operator._assert_current_operator_owner(tenant, actor)
    if source == "active":
        fresh = operator._credentials(tenant, allow_configured=True)
        if not _same_credentials(credentials, fresh):
            raise operator.OperatorError("Подключение Operator изменилось. Повторите попытку.", 409)
        return
    fresh = _accounts_capability_credentials(profile)
    if not _same_credentials(credentials, fresh) or (
            credentials.get("identity_fingerprint") != fresh.get("identity_fingerprint")):
        raise operator.OperatorError("Доступ только к аккаунтам изменился. Повторите попытку.", 409)


def _verify_selected_identity(profile, tenant, actor, source, credentials):
    operator._record_operator_read(profile, "me", {})
    _selected_source_fence(profile, tenant, actor, source, credentials)
    identity = operator.operator_me_payload(operator._request_operator(
        "me", {}, token=credentials["token"], base_url=credentials["base_url"],
        requests_me=True))
    _selected_source_fence(profile, tenant, actor, source, credentials)
    if (source == "accounts_only"
            and _identifier_fingerprint(identity["identifier"])
            != credentials["identity_fingerprint"]):
        raise operator.OperatorError("Подтверждённая учётная запись Operator изменилась.", 409)


def _verify_selected_account(profile, tenant, actor, source, credentials, account):
    _verify_selected_identity(profile,tenant,actor,source,credentials)
    operator._record_operator_read(profile, "accounts", {"account": account})
    _selected_source_fence(profile, tenant, actor, source, credentials)
    response = operator._request_operator(
        "accounts", {"search": account, "limit": 1}, token=credentials["token"],
        base_url=credentials["base_url"])
    found = operator._accounts_access_probe_payload(response)
    if len(found) != 1 or found[0] != account:
        raise operator.OperatorError(
            "Operator не подтвердил точное совпадение выбранного account ID.", 409)
    _selected_source_fence(profile, tenant, actor, source, credentials)


def _lock_and_check_selected_source(cur, tenant, actor, source, credentials):
    """Fence the credential under the same transaction as the local CRM write."""
    integration = operator._operator_row(cur, tenant, lock=True)
    if source == "active":
        if not _connection_is_current(integration, credentials, locked=True):
            raise operator.OperatorError("Подключение Operator изменилось до добавления.", 409)
        try:
            secret = json.loads(operator._fernet().decrypt(
                bytes(integration["secrets"])).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise operator.OperatorError("Действующее подключение Operator недоступно.", 409) from None
        if (not isinstance(secret, dict) or secret.get("token") != credentials["token"]
                or secret.get("connection_instance_id") != credentials["connection_instance_id"]):
            raise operator.OperatorError("Подключение Operator изменилось до добавления.", 409)
        return

    if integration and integration.get("status") in {"connected", "configured"} \
            and integration.get("secrets"):
        raise operator.OperatorError("Подключение Operator изменилось до добавления.", 409)
    cur.execute("""SELECT owner_telegram_id,candidate_received_at,operator_identifier_fingerprint,
        connection_generation,connection_instance_id::text,expires_at
      FROM saas_beinsystems_operator_accounts_capabilities
      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND expires_at>now() FOR UPDATE""",
      (tenant, actor))
    capability = cur.fetchone()
    if (not capability
            or capability["candidate_received_at"] != credentials["received_at"]
            or capability["connection_generation"] != credentials["generation"]
            or str(capability["connection_instance_id"]) != credentials["connection_instance_id"]
            or capability["operator_identifier_fingerprint"] != credentials["identity_fingerprint"]):
        raise operator.OperatorError("Доступ только к аккаунтам изменился до добавления.", 409)
    cur.execute("""SELECT token_ciphertext,received_at,activated_at
      FROM saas_beinsystems_operator_pending_auth_v2
      WHERE tenant_id=%s::uuid AND owner_telegram_id=%s FOR UPDATE""", (tenant, actor))
    pending = cur.fetchone()
    if (not pending or pending["received_at"] != credentials["received_at"]
            or pending.get("activated_at") is not None):
        raise operator.OperatorError("Сохранённый JWT Operator изменился до добавления.", 409)
    try:
        envelope = json.loads(operator._fernet().decrypt(
            bytes(pending["token_ciphertext"])).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise operator.OperatorError("Сохранённый JWT Operator недоступен.", 409) from None
    if (not isinstance(envelope, dict) or envelope.get("tenant_id") != tenant
            or envelope.get("owner_telegram_id") != actor
            or envelope.get("provider") != operator.PROVIDER
            or envelope.get("purpose") != "pending_auth_verification"
            or envelope.get("token") != credentials["token"]):
        raise operator.OperatorError("Сохранённый JWT Operator изменился до добавления.", 409)


def add_selected_account_company(profile, account):
    """Reverify one exact provider account, then create/reuse only one local company."""
    if not isinstance(account, str) or not account or account != account.strip() \
            or len(account) > 240 or any(ord(char) < 32 or ord(char) == 127 for char in account):
        raise operator.OperatorError("Идентификатор выбранного account ID некорректен.")
    tenant, actor, source, credentials = _selected_source_credentials(profile)
    _verify_selected_account(profile, tenant, actor, source, credentials, account)

    with operator._connect() as conn:
        operator._ensure_schema(conn)
        with conn.cursor() as cur:
            namespace_locked=lock_name_writes(cur,tenant,wait=True)
            operator._lock_accounts_capability_scope(cur, tenant)
            current = operator._current_profile(cur, tenant, actor)
            _owner(profile, current)
            _lock_and_check_selected_source(cur, tenant, actor, source, credentials)

            create_profile = current_access(cur, tenant, actor, "companies", "create")
            view_profile = current_access(cur, tenant, actor, "companies", "view")
            if (TenantPolicy(create_profile).has_scoped_access("companies")
                    or TenantPolicy(view_profile).has_scoped_access("companies")):
                raise operator.OperatorError(
                    "Для безопасного добавления нужен полный доступ к CRM-компаниям.", 403)
            authorize_create(cur, create_profile, "companies", tenant, actor)
            if not namespace_locked:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            (str(tenant) + ":operator-company-import",))

            cur.execute("""SELECT external_account,company_id::text AS company_id,
                last_seen_connection_instance_id::text AS last_instance,
                last_seen_generation FROM saas_beinsystems_operator_company_imports
              WHERE tenant_id=%s::uuid AND external_account=%s FOR UPDATE""",
              (tenant, account))
            existing_mapping = cur.fetchone()
            mappings = ({account: str(existing_mapping["company_id"])} if existing_mapping else {})

            cur.execute("""SELECT external_id,local_entity_id::text AS company_id
              FROM saas_beinsystems_operator_links
              WHERE tenant_id=%s::uuid AND local_entity_type='company'
                AND external_entity_type='account' AND external_scope_id=''
                AND connection_generation=%s AND connection_instance_id=%s::uuid
                AND revoked_at IS NULL AND external_id=%s FOR UPDATE""",
              (tenant, credentials["generation"], credentials["connection_instance_id"], account))
            confirmed_links = {}
            for row in cur.fetchall():
                confirmed_links.setdefault(account, []).append(str(row["company_id"]))

            existing_name_keys = set()
            cur.execute("""SELECT lower(name) AS name_key FROM saas_crm_native_companies
              WHERE tenant_id=%s::uuid AND lower(name)=lower(%s) FOR UPDATE""", (tenant, account))
            existing_name_keys = {str(row["name_key"]) for row in cur.fetchall()}

            targets = set(mappings.values())
            targets.update(company for values in confirmed_links.values() for company in values)
            visible_company_ids = set()
            for company_id in targets:
                resource = locked_resource(cur, view_profile, "companies", "view",
                    "saas_crm_native_companies", tenant, company_id)
                if resource:
                    visible_company_ids.add(str(resource["id"]))

            plan = _plan_company_import([{"account": account}], mappings, confirmed_links,
                                        visible_company_ids, existing_name_keys)
            action = plan[0]
            outcome = action["action"]
            company_id = action.get("company_id")
            if outcome == "manual_review":
                reason = action.get("reason") or "existing_identity_conflict"
            elif outcome == "create":
                company_id = str(uuid.uuid4())
                _require_import_name(cur,tenant,account,company_id)
                cur.execute("""INSERT INTO saas_crm_native_companies
                  (id,tenant_id,name,phone,email,description,responsible_id,access_responsible_id)
                  VALUES(%s::uuid,%s::uuid,%s,'','','',NULL,%s) RETURNING id""",
                  (company_id, tenant, account, actor))
                if not cur.fetchone() or not _put_mapping(
                        cur, tenant, account, company_id, actor, credentials):
                    raise operator.OperatorError(
                        "Аккаунт изменился во время добавления. Операция отменена.", 409)
                reason = None
            elif outcome in {"reuse", "bind"}:
                # Preserve the active connection's mapping generation when an
                # accounts-only candidate reuses an already mapped company.
                preserve_old_mapping = bool(
                    source == "accounts_only" and existing_mapping
                    and str(existing_mapping["company_id"]) == str(company_id))
                if not preserve_old_mapping and not _put_mapping(
                        cur, tenant, account, company_id, actor, credentials):
                    raise operator.OperatorError(
                        "Связь аккаунта с CRM-компанией изменилась. Операция отменена.", 409)
                reason = None
            else:
                raise operator.OperatorError("План добавления аккаунта некорректен.", 409)

            account_hash = hashlib.sha256(account.encode("utf-8")).hexdigest()
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'operator_company_selected','operator_company_import',%s,%s::jsonb)""",
              (str(uuid.uuid4()), tenant, actor, account_hash, operator._json({
                  "account_hash": account_hash, "result": outcome,
                  "reason": reason, "connection_generation": credentials["generation"],
                  "source": source,
              })))
        conn.commit()

    result = {"status": outcome, "reason": reason,
              "company": ({"id": str(company_id), "name": account} if company_id else None),
              "account_id": account, "source": source}
    return result
