"""Tenant-local employee presence used only for local dialog admission/transfer."""
import json
import os
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_beinsystems_operator_access import configured_workspace_id, is_operator_workspace

ROUTING_ENV = "TENANT_DIALOG_PRESENCE_ROUTING_ENABLED"
STATUS_PROVISION_ENV = "DIALOG_PRESENCE_STATUS_PROVISION_WORKSPACE_ID"
STATUSES = frozenset({"online", "lunch", "away"})


def _scope(profile):
    tenant = str(uuid.UUID(str(profile.get("tenant_id"))))
    employee = int(profile.get("telegram_id"))
    return tenant, employee


def _available(cur):
    cur.execute("SELECT to_regclass('saas_dialog_employee_presence') AS table_name")
    row = cur.fetchone()
    return bool(row and row.get("table_name"))


def _routing_requested(tenant):
    return (os.environ.get(ROUTING_ENV, "").strip().lower() == "true"
            and is_operator_workspace(tenant))


def routing_enabled(cur, tenant):
    """A staged, independent opt-in. Do not query for the table when off."""
    return _routing_requested(tenant) and _available(cur)


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


def provision_status_pilot(cur, requested_workspace):
    """Install only the empty presence table, under the bounded startup lock."""
    try:
        tenant = str(uuid.UUID(requested_workspace))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Presence provisioning requires an exact BeinSystems workspace UUID.") from None
    if tenant != requested_workspace or tenant != configured_workspace_id():
        raise RuntimeError("Presence provisioning requires an exact BeinSystems workspace UUID.")
    if _routing_requested(tenant):
        raise RuntimeError("Status-only presence provisioning requires dialogue presence routing to be disabled.")
    cur.execute("""SELECT 1 FROM saas_tenants
      WHERE id=%s::uuid AND status='active' FOR SHARE""", (tenant,))
    if not cur.fetchone():
        raise RuntimeError("Presence provisioning requires the configured BeinSystems workspace to be active.")
    from tenant_schema_preflight import ready
    if not ready(cur, {"saas_dialog_rollout": (
        "tenant_id", "mode", "activated_at", "admission_enabled", "generation",
    )}, ()):
        raise RuntimeError("Dialogue authorization schema must already exist before presence provisioning.")
    created = not _available(cur)
    if created:
        migrate(cur)
    if not ready(cur, {"saas_dialog_employee_presence": (
        "tenant_id", "employee_id", "status", "updated_at", "updated_by",
    )}, ("saas_dialog_presence_status_idx",)):
        raise RuntimeError("Unexpected presence schema; status provisioning rolled back.")
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
        raise RuntimeError("Unexpected presence schema; status provisioning rolled back.")
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
        raise RuntimeError("Unexpected presence columns; status provisioning rolled back.")
    cur.execute("""SELECT pg_get_constraintdef(oid) AS definition, convalidated
      FROM pg_catalog.pg_constraint WHERE conrelid=%s::oid AND contype IN ('c','f','p','u','x')""",
      (relation["oid"],))
    constraints = cur.fetchall()
    expected_constraints = {
        "PRIMARY KEY (tenant_id, employee_id)",
        "FOREIGN KEY (tenant_id, employee_id) REFERENCES saas_tenant_members(tenant_id, telegram_id)",
        "FOREIGN KEY (tenant_id, updated_by) REFERENCES saas_tenant_members(tenant_id, telegram_id)",
        "CHECK ((status = ANY (ARRAY['online'::text, 'lunch'::text, 'away'::text])))",
    }
    if (len(constraints) != len(expected_constraints)
            or {row["definition"] for row in constraints} != expected_constraints
            or not all(row["convalidated"] for row in constraints)):
        raise RuntimeError("Unexpected presence constraints; status provisioning rolled back.")
    cur.execute("""SELECT 1 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class t ON t.oid=i.indrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
      WHERE i.indexrelid=to_regclass('saas_dialog_presence_status_idx') AND i.indrelid=%s::oid
        AND i.indisvalid AND i.indisready AND i.indislive AND pg_get_indexdef(i.indexrelid)=format(
          'CREATE INDEX saas_dialog_presence_status_idx ON %%I.saas_dialog_employee_presence USING btree (tenant_id, status, employee_id)',
          n.nspname)""", (relation["oid"],))
    if not cur.fetchone():
        raise RuntimeError("Unexpected presence index; status provisioning rolled back.")
    return created


def is_online(cur, tenant, employee, *, lock=False, _routing=None):
    """Admission predicate: legacy eligibility when routing is not enforced."""
    if not (routing_enabled(cur, tenant) if _routing is None else _routing):
        return True
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


def get_status(profile):
    tenant, employee = _scope(profile)
    unavailable = {"available": False, "status": None, "configured": False,
                   "can_update": False, "routing_enforced": False}
    if not is_operator_workspace(tenant):
        return unavailable
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            if not _available(cur):
                return unavailable
            enforced = _routing_enforced(cur, tenant)
            from tenant_operator_access import access
            tenant, employee, policy = access(cur, profile, "dialogs")
            cur.execute("""SELECT p.status FROM saas_dialog_employee_presence p
              WHERE p.tenant_id=%s::uuid AND p.employee_id=%s""",
              (tenant, employee))
            row = cur.fetchone()
            update_scope = policy.action_scope("dialogs", "update")
            can_update = (update_scope != "none" if update_scope is not None
                          else policy.legacy_allows("dialogs"))
    return {"available": True, "status": row["status"] if row else None,
            "configured": bool(row), "can_update": can_update,
            "routing_enforced": enforced}


def set_status(profile, payload):
    if not isinstance(payload, dict) or set(payload) != {"status"}:
        raise TenantLinearError("Некорректный запрос.")
    status = payload.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise TenantLinearError("Выберите допустимый статус сотрудника.")
    tenant, _ = _scope(profile)
    if not is_operator_workspace(tenant):
        raise TenantLinearError("Статусы сотрудников недоступны в этом workspace.", 403)
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            if not _available(cur):
                raise TenantLinearError("Статусы сотрудников ещё недоступны.", 409)
            enforced = _routing_enforced(cur, tenant)
            from tenant_operator_access import access
            tenant, employee, _policy = access(cur, profile, "dialogs", "update")
            # Serialize status changes with recipient checks, which lock this
            # membership row first. Inactive members cannot update stale sessions.
            cur.execute("""SELECT 1 FROM saas_tenant_members
              WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE
              FOR UPDATE""", (tenant, employee))
            if not cur.fetchone():
                raise TenantLinearError("Нет доступа.", 403)
            cur.execute("""SELECT status,updated_at FROM saas_dialog_employee_presence
              WHERE tenant_id=%s::uuid AND employee_id=%s FOR UPDATE""",
              (tenant, employee))
            previous = cur.fetchone()
            if previous and previous.get("status") == status:
                return {"available": True, "status": status, "configured": True,
                        "routing_enforced": enforced}
            cur.execute("""INSERT INTO saas_dialog_employee_presence
              (tenant_id,employee_id,status,updated_by)
              VALUES(%s::uuid,%s,%s,%s)
              ON CONFLICT(tenant_id,employee_id) DO UPDATE SET
                status=EXCLUDED.status,updated_by=EXCLUDED.updated_by,
                updated_at=clock_timestamp()
              RETURNING status,updated_at""", (tenant, employee, status, employee))
            saved = cur.fetchone()
            cur.execute("""INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s::uuid,%s::uuid,%s,'dialog_presence_changed',
                'employee_presence',%s,%s::jsonb)""",
              (str(uuid.uuid4()), tenant, employee, str(employee),
               json.dumps({"previous_status": previous.get("status") if previous else None,
                           "status": status})))
    return {"available": True, "status": saved["status"], "configured": True,
            "routing_enforced": enforced}
