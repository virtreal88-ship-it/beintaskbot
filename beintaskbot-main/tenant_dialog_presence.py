"""Employee-owned status and the separate legacy admission/transfer policy."""
import json
import os
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_beinsystems_operator_access import is_operator_workspace

ROUTING_ENV = "TENANT_DIALOG_PRESENCE_ROUTING_ENABLED"
STATUS_ENABLED_ENV = "TENANT_DIALOG_EMPLOYEE_STATUS_ENABLED"
STATUS_PROVISION_ENV = "DIALOG_EMPLOYEE_STATUS_PROVISION_ON_STARTUP"
DEFAULT_STATUS = "online"
LEGACY_STATUSES = frozenset({DEFAULT_STATUS, "lunch", "away"})
STATUSES = LEGACY_STATUSES | {"offline"}
_LEGACY_CHECK = "CHECK ((status = ANY (ARRAY['online'::text, 'lunch'::text, 'away'::text])))"
_WORK_CHECK = "CHECK ((status = ANY (ARRAY['online'::text, 'lunch'::text, 'away'::text, 'offline'::text])))"


def _scope(profile):
    tenant = str(uuid.UUID(str(profile.get("tenant_id"))))
    employee = int(profile.get("telegram_id"))
    return tenant, employee


def _available(cur):
    cur.execute("SELECT to_regclass('saas_dialog_employee_presence') AS table_name")
    row = cur.fetchone()
    return bool(row and row.get("table_name"))


def work_status_enabled():
    return os.environ.get(STATUS_ENABLED_ENV, "").strip().lower() == "true"


def _status_requested(tenant):
    # Turning status off again preserves the already-supported BeinSystems scope.
    return work_status_enabled() or is_operator_workspace(tenant)


def _routing_flag_enabled():
    return os.environ.get(ROUTING_ENV, "").strip().lower() == "true"


def _routing_requested(tenant):
    return _routing_flag_enabled() and is_operator_workspace(tenant)


def work_status_routing_requested(tenant):
    return _routing_requested(tenant) and work_status_enabled()


def routing_enabled(cur, tenant):
    """Keep the presence gate fail-closed when the four-state pilot is requested."""
    return _routing_requested(tenant) and (work_status_enabled() or _available(cur))


def _routing_enforced(cur, tenant):
    # Call from the enrollment endpoints only, with the rollout guard taken
    # before membership checks. Admission already holds that guard and is
    # only used in the enforced lifecycle.
    if not _routing_requested(tenant):
        return False
    from tenant_dialog_rollout import guard
    return guard(cur, tenant).enforced


def migrate(cur):
    """Explicit lifecycle preflight only; never run as request-time DDL."""
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_employee_presence (
      tenant_id UUID NOT NULL,
      employee_id BIGINT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('online','lunch','away')),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      updated_by BIGINT NOT NULL,
      PRIMARY KEY(tenant_id,employee_id),
      FOREIGN KEY(tenant_id,employee_id)
        REFERENCES saas_tenant_members(tenant_id,telegram_id),
      FOREIGN KEY(tenant_id,updated_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id)
    )""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_presence_status_idx
      ON saas_dialog_employee_presence(tenant_id,status,employee_id)""")


class PresenceSchemaError(RuntimeError):
    """The known status schema is absent or structurally incompatible."""


def _authorization_schema_ready(cur):
    from tenant_schema_preflight import ready
    return ready(cur, {"saas_dialog_rollout": (
        "tenant_id", "mode", "activated_at", "admission_enabled", "generation",
    )}, ())


def provision_status_schema(cur):
    """Install only the empty, additive presence table during optional startup."""
    if not _authorization_schema_ready(cur):
        raise PresenceSchemaError("Dialogue authorization schema must already exist before presence provisioning.")
    created = not _available(cur)
    if created:
        # Old binaries may regard a newly present table + active routing flag
        # as a reason to start gating assignment. Status must never cause that.
        if _routing_flag_enabled():
            raise PresenceSchemaError("A status-only rollout cannot activate legacy routing.")
        migrate(cur)
    check_name, expanded = verify_status_schema(cur)
    if not expanded:
        from psycopg import sql
        # Exact verified 3-state constraint only. The existing values are a
        # subset of the replacement; no data mutation or extra row occurs.
        cur.execute(sql.SQL("""ALTER TABLE saas_dialog_employee_presence
          DROP CONSTRAINT {previous}, ADD CONSTRAINT {previous}
          CHECK (status IN ('online','lunch','away','offline'))""").format(
            previous=sql.Identifier(check_name)))
        if not verify_status_schema(cur)[1]:
            raise PresenceSchemaError("Work status schema expansion did not verify.")
    return created


def verify_status_schema(cur):
    """Catalog evidence, never probe or change employee records."""
    from tenant_schema_preflight import ready
    if not ready(cur, {"saas_dialog_employee_presence": (
        "tenant_id", "employee_id", "status", "updated_at", "updated_by",
    )}, ("saas_dialog_presence_status_idx",)):
        raise PresenceSchemaError("Unexpected presence schema; status provisioning rolled back.")
    cur.execute("""SELECT c.oid FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.oid=to_regclass('saas_dialog_employee_presence')
        AND n.nspname=current_schema() AND c.relkind='r' AND c.relpersistence='p'
        AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
        AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid,i.inhparent))
        AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
          WHERE k.conrelid=c.oid AND k.contype='p'
            AND pg_get_constraintdef(k.oid)='PRIMARY KEY (tenant_id, employee_id)')""")
    relation = cur.fetchone()
    if not relation:
        raise PresenceSchemaError("Unexpected presence schema; status provisioning rolled back.")
    cur.execute("""SELECT a.attname, format_type(a.atttypid,a.atttypmod) AS type,
      a.attnotnull, a.attidentity, a.attgenerated, pg_get_expr(d.adbin,d.adrelid) AS default_expr
      FROM pg_catalog.pg_attribute a
      LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid=%s::oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
      (relation["oid"],))
    columns = cur.fetchall()
    expected_columns = (
        ("tenant_id", "uuid", None), ("employee_id", "bigint", None),
        ("status", "text", None), ("updated_at", "timestamp with time zone", "clock_timestamp()"),
        ("updated_by", "bigint", None),
    )
    if (tuple((row["attname"],row["type"],row["default_expr"]) for row in columns) != expected_columns
            or any(not row["attnotnull"] or row["attidentity"] or row["attgenerated"] for row in columns)):
        raise PresenceSchemaError("Unexpected presence columns; status provisioning rolled back.")
    cur.execute("""SELECT conname, pg_get_constraintdef(oid) AS definition, convalidated
      FROM pg_catalog.pg_constraint WHERE conrelid=%s::oid AND contype IN ('c','f','p','u','x')""",
      (relation["oid"],))
    constraints = cur.fetchall()
    expected_constraints = {
        "PRIMARY KEY (tenant_id, employee_id)",
        "FOREIGN KEY (tenant_id, employee_id) REFERENCES saas_tenant_members(tenant_id, telegram_id)",
        "FOREIGN KEY (tenant_id, updated_by) REFERENCES saas_tenant_members(tenant_id, telegram_id)",
    }
    actual = {row["definition"] for row in constraints}
    mode = actual - expected_constraints
    if (mode not in ({_LEGACY_CHECK}, {_WORK_CHECK})
            or len(constraints) != len(expected_constraints) + 1
            or actual != expected_constraints | mode
            or not all(row["convalidated"] for row in constraints)):
        raise PresenceSchemaError("Unexpected presence constraints; status provisioning rolled back.")
    cur.execute("""SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON t.oid=i.indrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
      WHERE i.indexrelid=to_regclass('saas_dialog_presence_status_idx') AND i.indrelid=%s::oid
        AND i.indisvalid AND i.indisready AND i.indislive AND pg_get_indexdef(i.indexrelid)=
          'CREATE INDEX saas_dialog_presence_status_idx ON ' || quote_ident(n.nspname) ||
          '.saas_dialog_employee_presence USING btree (tenant_id, status, employee_id)'""", (relation["oid"],))
    if not cur.fetchone():
        raise PresenceSchemaError("Unexpected presence index; status provisioning rolled back.")
    return next(row["conname"] for row in constraints if row["definition"] in mode), mode == {_WORK_CHECK}


def status_schema_ready(cur, *, require_offline=False, require_dialog_acl=True):
    if not _available(cur) or (require_dialog_acl and not _authorization_schema_ready(cur)):
        return False
    try:
        _, expanded = verify_status_schema(cur)
    except PresenceSchemaError:
        return False
    return expanded or not require_offline


def _active_member(cur, tenant, employee, *, writing=False):
    """Self status only; no role or module grant is consumed or changed."""
    from tenant_ai_agent_access import tenant_status_allows_management
    cur.execute("SELECT status FROM saas_tenants WHERE id=%s::uuid FOR SHARE", (tenant,))
    company = cur.fetchone()
    if not company or not tenant_status_allows_management(company["status"]):
        raise TenantLinearError("Нет доступа к активной компании.", 403)
    cur.execute("""SELECT 1 FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE""" +
        (" FOR UPDATE" if writing else " FOR SHARE"), (tenant, employee))
    if not cur.fetchone():
        raise TenantLinearError("Нет доступа к активной компании.", 403)


def _acquire_status_lock(cur, tenant, employee):
    cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
                (f"dialog-status:{tenant}:{employee}",))
    if not cur.fetchone()["acquired"]:
        raise TenantLinearError("Статус уже обновляется. Повторите запрос.", 409)


def _allows(policy, action):
    scope = policy.action_scope("dialogs", action)
    return scope != "none" if scope is not None else policy.legacy_allows("dialogs")


def _authorized_status(cur, tenant, employee, *, lock, require_update, absent, operators_only=False,
                       source_channel=None):
    required = absent is not None
    if not (work_status_enabled() if required else _status_requested(tenant)):
        return None
    if not status_schema_ready(cur, require_offline=required):
        return None
    from tenant_ai_agent_access import tenant_status_allows_management
    from tenant_dialog_rollout import guard
    from tenant_policy import TenantPolicy
    from tenant_scoped_access_store import read_access_profile

    if lock:
        guard(cur, tenant)
    profile = read_access_profile(cur, tenant, employee, lock=lock)
    if not profile or not tenant_status_allows_management(profile.get("tenant_status")):
        return None
    if operators_only and profile.get("role") != "operator":
        return None
    policy = TenantPolicy({**profile, "tenant_id": tenant})
    if not _allows(policy, "view") or (require_update and not _allows(policy, "update")):
        return None
    if source_channel is not None:
        from tenant_dialog_access import sql_filter
        view = sql_filter(policy, "'connect'", "c.connect_channel_id::text", "view")
        update = sql_filter(policy, "'connect'", "c.connect_channel_id::text", "update")
        cur.execute(f"""SELECT 1 FROM saas_connect_channels c
          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.active
          AND ({view}) AND ({update})""", (tenant, source_channel))
        if not cur.fetchone():
            return None
    cur.execute("""SELECT status FROM saas_dialog_employee_presence
      WHERE tenant_id=%s::uuid AND employee_id=%s""" + (" FOR SHARE" if lock else ""),
      (tenant, employee))
    row = cur.fetchone()
    return row["status"] if row else absent


def is_recorded_online(cur, tenant, employee, *, lock=False):
    """Require explicit Online and current edit ACL; never use admission fallback."""
    return _authorized_status(cur, tenant, employee, lock=lock,
                              require_update=True, absent=None) == DEFAULT_STATUS


def is_effectively_online(cur, tenant, employee, *, lock=False):
    """Read eligible availability, defaulting to Online only absent a status row.

    Consumers must independently enforce action/route authorization and any role
    restriction. View does not imply update or any new routing eligibility.
    """
    return _authorized_status(cur, tenant, employee, lock=lock,
                              require_update=False, absent=DEFAULT_STATUS) == DEFAULT_STATUS


def is_online(cur, tenant, employee, *, lock=False, _routing=None, source_channel=None):
    """Admission predicate: legacy eligibility when routing is not enforced."""
    if not (routing_enabled(cur, tenant) if _routing is None else _routing):
        return True
    if work_status_routing_requested(tenant):
        return _authorized_status(cur, tenant, employee, lock=lock, require_update=True,
                                  absent=DEFAULT_STATUS, operators_only=True,
                                  source_channel=source_channel) == DEFAULT_STATUS
    clause = " FOR SHARE" if lock else ""
    cur.execute("""SELECT 1 FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE""" + clause,
      (tenant, employee))
    if not cur.fetchone():
        return False
    cur.execute("""SELECT status FROM saas_dialog_employee_presence
      WHERE tenant_id=%s::uuid AND employee_id=%s""" + clause,
      (tenant, employee))
    row = cur.fetchone()
    return bool(row and row.get("status") == "online")


_UNAVAILABLE = {"available": False, "status": None, "configured": False,
                "can_update": False, "routing_enforced": False}


def _work_ready(cur, *, profile_only=False):
    return work_status_enabled() and status_schema_ready(
        cur, require_offline=True, require_dialog_acl=not profile_only)


def get_status(profile, *, profile_only=False):
    tenant, employee = _scope(profile)
    if not (work_status_enabled() if profile_only else _status_requested(tenant)):
        return dict(_UNAVAILABLE)
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            work = _work_ready(cur, profile_only=profile_only)
            if not work and (profile_only or not is_operator_workspace(tenant)
                             or not status_schema_ready(cur)):
                return dict(_UNAVAILABLE)
            if profile_only:
                _active_member(cur, tenant, employee)
                enforced, can_update = False, True
            else:
                enforced = _routing_enforced(cur, tenant)
                from tenant_operator_access import access
                tenant, employee, policy = access(cur, profile, "dialogs")
                can_update = _allows(policy, "update")
            cur.execute("""SELECT status FROM saas_dialog_employee_presence
              WHERE tenant_id=%s::uuid AND employee_id=%s""", (tenant, employee))
            row = cur.fetchone()
    status = row["status"] if row else DEFAULT_STATUS
    # Flag-off returns the old picker to its historical three states; the
    # selected Offline remains in storage and is restored on re-enable.
    if not work and status == "offline":
        status = "away"
    return {"available": True, "status": status, "configured": bool(row),
            "can_update": can_update, "routing_enforced": enforced, **({"can_set_offline": True} if work else {})}


def _persist_status(cur, tenant, employee, status, *, preserve_hidden_offline=False):
    cur.execute("""SELECT status,updated_at FROM saas_dialog_employee_presence
      WHERE tenant_id=%s::uuid AND employee_id=%s FOR UPDATE""", (tenant, employee))
    previous = cur.fetchone()
    old = previous["status"] if previous else None
    if old == status or (preserve_hidden_offline and old == "offline" and status == "away"):
        return
    cur.execute("""INSERT INTO saas_dialog_employee_presence
      (tenant_id,employee_id,status,updated_by)
      VALUES(%s::uuid,%s,%s,%s)
      ON CONFLICT(tenant_id,employee_id) DO UPDATE SET
        status=EXCLUDED.status,updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp()""",
        (tenant, employee, status, employee))
    cur.execute("""INSERT INTO saas_tenant_audit_events
      (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
      VALUES(%s::uuid,%s::uuid,%s,'dialog_presence_changed',
        'employee_presence',%s,%s::jsonb)""",
        (str(uuid.uuid4()), tenant, employee, str(employee),
         json.dumps({"previous_status": old, "status": status})))


def set_status(profile, payload, *, profile_only=False):
    if not isinstance(payload, dict) or set(payload) != {"status"}:
        raise TenantLinearError("Некорректный запрос.")
    status = payload.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise TenantLinearError("Выберите допустимый статус сотрудника.")
    tenant, employee = _scope(profile)
    if not (work_status_enabled() if profile_only else _status_requested(tenant)):
        raise TenantLinearError("Статусы сотрудников недоступны в этом workspace.", 403)
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            work = _work_ready(cur, profile_only=profile_only)
            if not work and (profile_only or not is_operator_workspace(tenant)
                             or not status_schema_ready(cur) or status not in LEGACY_STATUSES):
                raise TenantLinearError("Статусы сотрудников ещё недоступны.", 409)
            enforced = False if profile_only else _routing_enforced(cur, tenant)
            _acquire_status_lock(cur, tenant, employee)
            if not profile_only:
                from tenant_operator_access import access
                tenant, employee, policy = access(cur, profile, "dialogs", "view")
                if not _allows(policy, "update"):
                    raise TenantLinearError("Владелец должен выдать доступ к этому разделу.", 403)
            # Serializes with recipient eligibility and locks fresh membership
            # through the write. Status alone never adds broader permissions.
            _active_member(cur, tenant, employee, writing=True)
            _persist_status(cur, tenant, employee, status, preserve_hidden_offline=not work)
    return {"available": True, "status": status, "configured": True,
            "routing_enforced": enforced, **({"can_set_offline": True} if work else {})}


def for_owner_roster(cur, *, tenant, owner_id, current, rows):
    """Optional read-only projection onto already authorized active roster rows."""
    if (current.get("role") != "owner" or current.get("active") is not True
            or int(current.get("company_owner_id") or 0) != owner_id
            or str(current.get("tenant_id")) != str(tenant)
            or not _work_ready(cur, profile_only=True)):
        return
    active = [row for row in rows if row["active"] is True and str(row["tenant_id"]) == str(tenant)]
    ids = [int(row["telegram_id"]) for row in active]
    if not ids:
        return
    cur.execute("""SELECT employee_id,status FROM saas_dialog_employee_presence
      WHERE tenant_id=%s::uuid AND employee_id=ANY(%s::bigint[])""", (tenant, ids))
    statuses = {int(row["employee_id"]): row["status"] for row in cur.fetchall()}
    for row in active:
        row["work_status"] = statuses.get(int(row["telegram_id"]), DEFAULT_STATUS)
