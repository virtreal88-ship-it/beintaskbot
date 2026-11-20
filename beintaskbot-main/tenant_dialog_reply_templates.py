"""Tenant-local reply templates with membership-scoped visibility and no provider IO."""
import json
import os
import uuid

from tenant_linear_policy import TenantLinearError

TABLE = "saas_dialog_reply_templates"
VISIBILITIES = frozenset({"personal", "team", "shared"})
MAX_NAME = 120
MAX_BODY = 4000


def feature_ready(profile):
    """Server gate: explicit opt-in, exact pilot tenant, and initialized schema."""
    if os.environ.get("DIALOG_REPLY_TEMPLATES_ENABLED", "").lower() != "true":
        return False
    from tenant_beinsystems_operator_access import is_operator_workspace
    if not is_operator_workspace(profile.get("tenant_id")):
        return False
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            return _schema_ready(cur)


def migrate(cur):
    """Explicit reviewed preflight migration; never call from request handlers."""
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_reply_templates (
      tenant_id UUID NOT NULL,
      id UUID NOT NULL,
      request_id UUID NOT NULL,
      name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
      body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
      visibility TEXT NOT NULL CHECK(visibility IN ('personal','team','shared')),
      department_id UUID,
      created_by BIGINT NOT NULL,
      updated_by BIGINT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
      created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      deleted_at TIMESTAMPTZ,
      PRIMARY KEY(tenant_id,id),
      UNIQUE(tenant_id,created_by,request_id),
      CHECK((visibility='team') = (department_id IS NOT NULL)),
      FOREIGN KEY(tenant_id,created_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id),
      FOREIGN KEY(tenant_id,updated_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id),
      FOREIGN KEY(tenant_id,department_id)
        REFERENCES saas_tenant_departments(tenant_id,id)
    )""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_reply_templates_scope_idx
      ON saas_dialog_reply_templates(tenant_id,visibility,department_id,updated_at DESC)
      WHERE deleted_at IS NULL""")


_EXPECTED_COLUMNS = (
    ("tenant_id", "uuid", True, None),
    ("id", "uuid", True, None),
    ("request_id", "uuid", True, None),
    ("name", "text", True, None),
    ("body", "text", True, None),
    ("visibility", "text", True, None),
    ("department_id", "uuid", False, None),
    ("created_by", "bigint", True, None),
    ("updated_by", "bigint", True, None),
    ("version", "integer", True, "1"),
    ("created_at", "timestamp with time zone", True, "clock_timestamp()"),
    ("updated_at", "timestamp with time zone", True, "clock_timestamp()"),
    ("deleted_at", "timestamp with time zone", False, None),
)
_EXPECTED_CONSTRAINTS = frozenset({
    "CHECK (((length(body) >= 1) AND (length(body) <= 4000)))",
    "CHECK (((length(name) >= 1) AND (length(name) <= 120)))",
    "CHECK (((visibility = 'team'::text) = (department_id IS NOT NULL)))",
    "CHECK ((version > 0))",
    "CHECK ((visibility = ANY (ARRAY['personal'::text, 'team'::text, 'shared'::text])))",
    "FOREIGN KEY (tenant_id, created_by) REFERENCES saas_tenant_members(tenant_id, telegram_id)",
    "FOREIGN KEY (tenant_id, department_id) REFERENCES saas_tenant_departments(tenant_id, id)",
    "FOREIGN KEY (tenant_id, updated_by) REFERENCES saas_tenant_members(tenant_id, telegram_id)",
    "PRIMARY KEY (tenant_id, id)",
    "UNIQUE (tenant_id, created_by, request_id)",
})


def verify_provisioned_schema(cur):
    """Verify IF NOT EXISTS did not silently accept an incompatible object."""
    cur.execute("""SELECT c.oid, n.nspname FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.oid=to_regclass('saas_dialog_reply_templates')
        AND n.nspname=current_schema() AND c.relkind='r' AND c.relpersistence='p'
        AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
        AND NOT EXISTS (SELECT FROM pg_catalog.pg_trigger t
                        WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
        AND NOT EXISTS (SELECT FROM pg_catalog.pg_inherits inh
                        WHERE c.oid IN (inh.inhrelid, inh.inhparent))""")
    table = cur.fetchone()
    if not table:
        raise RuntimeError("Unexpected reply-template table; provisioning rolled back.")
    oid = table["oid"]
    cur.execute("""SELECT a.attname, format_type(a.atttypid,a.atttypmod) AS data_type,
      a.attnotnull, pg_get_expr(d.adbin,d.adrelid) AS default_expression,
      a.attidentity, a.attgenerated
      FROM pg_catalog.pg_attribute a
      LEFT JOIN pg_catalog.pg_attrdef d ON (d.adrelid,d.adnum)=(a.attrelid,a.attnum)
      WHERE a.attrelid=%s::oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (oid,))
    columns = cur.fetchall()
    if (tuple((row["attname"], row["data_type"], row["attnotnull"], row["default_expression"])
              for row in columns) != _EXPECTED_COLUMNS
            or any(row["attidentity"] or row["attgenerated"] for row in columns)):
        raise RuntimeError("Unexpected reply-template columns; provisioning rolled back.")
    cur.execute("""SELECT pg_get_constraintdef(oid) AS definition, convalidated
      FROM pg_catalog.pg_constraint WHERE conrelid=%s::oid AND contype IN ('c','f','p','u','x')""",
                (oid,))
    constraints = cur.fetchall()
    # PostgreSQL 18 also stores NOT NULL constraints here. The column check
    # above covers them on both PostgreSQL 17 and 18.
    if (len(constraints) != len(_EXPECTED_CONSTRAINTS)
            or frozenset(row["definition"] for row in constraints) != _EXPECTED_CONSTRAINTS
            or not all(row["convalidated"] for row in constraints)):
        raise RuntimeError("Unexpected reply-template constraints; provisioning rolled back.")
    cur.execute("""SELECT 1 FROM pg_catalog.pg_index i
      WHERE i.indexrelid=to_regclass('saas_dialog_reply_templates_scope_idx')
        AND i.indrelid=%s::oid AND i.indisvalid AND i.indisready AND i.indislive
        AND pg_get_indexdef(i.indexrelid)=format(
           'CREATE INDEX saas_dialog_reply_templates_scope_idx ON %%I.saas_dialog_reply_templates USING btree (tenant_id, visibility, department_id, updated_at DESC) WHERE (deleted_at IS NULL)',
           %s::text)""", (oid, table["nspname"]))
    if not cur.fetchone():
        raise RuntimeError("Unexpected reply-template index; provisioning rolled back.")


def _schema_ready(cur):
    cur.execute("SELECT to_regclass('saas_dialog_reply_templates') AS table_name")
    row = cur.fetchone()
    return bool(row and row.get("table_name"))


def _identity(profile):
    try:
        tenant = str(uuid.UUID(str(profile.get("tenant_id"))))
        actor = int(profile.get("telegram_id"))
        if actor <= 0:
            raise ValueError
        return tenant, actor
    except (TypeError, ValueError, AttributeError):
        raise TenantLinearError("Нет доступа.", 403) from None


def _authorize(cur, profile, action):
    from tenant_operator_access import access
    tenant, actor, policy = access(cur, profile, "dialogs", action)
    return tenant, actor, policy


def _effective_scope(policy, action):
    scope = policy.action_scope("dialogs", action)
    if scope is not None:
        return scope
    if not policy.legacy_allows_action("dialogs", action):
        return "none"
    role = policy.profile.get("role")
    department_id = policy.profile.get("department_id")
    if role in {"owner", "admin"}:
        return "all"
    if role == "manager" and department_id:
        return "department"
    return "self"


def _department(policy):
    value = policy.profile.get("department_id")
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def allowed_visibility(policy):
    scope = _effective_scope(policy, "update")
    result = []
    if scope != "none":
        result.append("personal")
    if scope in {"department", "all"} and _department(policy):
        result.append("team")
    if scope == "all":
        result.append("shared")
    return result


def can_view(template, actor, department_id, scope):
    visibility = template.get("visibility")
    if visibility == "shared":
        return scope != "none"
    if visibility == "personal":
        return int(template.get("created_by") or 0) == int(actor)
    if visibility == "team":
        item_department = template.get("department_id")
        if scope == "all":
            return item_department is not None
        return (scope == "department" and department_id is not None
                and item_department is not None
                and str(item_department) == str(department_id))
    return False


def can_manage(template, actor, department_id, scope):
    visibility = template.get("visibility")
    if visibility == "personal":
        return scope != "none" and int(template.get("created_by") or 0) == int(actor)
    if visibility == "team":
        item_department = template.get("department_id")
        if scope == "all":
            return item_department is not None
        return (scope == "department" and department_id is not None
                and item_department is not None
                and str(item_department) == str(department_id))
    if visibility == "shared":
        return scope == "all"
    return False


def _clean_text(value, *, name=False):
    limit = MAX_NAME if name else MAX_BODY
    field = "название" if name else "текст"
    if not isinstance(value, str):
        raise TenantLinearError("Некорректный " + field + " шаблона.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value or len(value) > limit:
        raise TenantLinearError("Некорректный " + field + " шаблона.")
    if any(ord(char) < 32 and not (not name and char in "\n\t") for char in value):
        raise TenantLinearError("Некорректный " + field + " шаблона.")
    return value


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TenantLinearError("Версия шаблона устарела.", 409)
    return value


def _uuid(value, message):
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise TenantLinearError(message) from None


def _serialize(row, actor, department_id, update_scope):
    item = {
        "id": str(row["id"]),
        "name": row["name"],
        "body": row["body"],
        "visibility": row["visibility"],
        "version": int(row["version"]),
    }
    item["can_edit"] = can_manage(row, actor, department_id, update_scope)
    return item


def list_templates(profile):
    tenant, actor = _identity(profile)
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            view_tenant, view_actor, view_policy = _authorize(cur, profile, "view")
            if (view_tenant, view_actor) != (tenant, actor):
                raise TenantLinearError("Нет доступа.", 403)
            if not _schema_ready(cur):
                raise TenantLinearError("Шаблоны ещё не активированы.", 409)
            update_scope = _effective_scope(view_policy, "update")
            view_scope = _effective_scope(view_policy, "view")
            department_id = _department(view_policy)
            cur.execute("""SELECT id::text AS id,request_id::text AS request_id,name,body,
                visibility,department_id::text AS department_id,created_by,version
              FROM saas_dialog_reply_templates
              WHERE tenant_id=%s::uuid AND deleted_at IS NULL
                AND (visibility='shared' OR (visibility='personal' AND created_by=%s)
                  OR (visibility='team' AND (%s='all' OR department_id=%s::uuid)))
              ORDER BY updated_at DESC,id DESC""",
              (tenant, actor, view_scope, department_id))
            rows = cur.fetchall()
            visible = [row for row in rows if can_view(row, actor, department_id, view_scope)]
            return {
                "available": True,
                "templates": [_serialize(row, actor, department_id, update_scope) for row in visible],
                "allowed_visibility": allowed_visibility(view_policy),
            }


def _audit(cur, tenant, actor, template_id, action, visibility):
    cur.execute("""INSERT INTO saas_tenant_audit_events
      (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
      VALUES(%s::uuid,%s::uuid,%s,%s,'dialog_reply_template',%s,%s::jsonb)""",
      (str(uuid.uuid4()), tenant, actor, action, template_id,
       json.dumps({"visibility": visibility}, ensure_ascii=False)))


def mutate_template(profile, payload):
    if not isinstance(payload, dict):
        raise TenantLinearError("Некорректный запрос.")
    action = payload.get("action")
    if action not in {"create", "update", "delete"}:
        raise TenantLinearError("Действие не поддерживается.")
    tenant, actor = _identity(profile)
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            current_tenant, current_actor, policy = _authorize(cur, profile, "update")
            if (current_tenant, current_actor) != (tenant, actor):
                raise TenantLinearError("Нет доступа.", 403)
            if not _schema_ready(cur):
                raise TenantLinearError("Шаблоны ещё не активированы.", 409)
            update_scope = _effective_scope(policy, "update")
            department_id = _department(policy)
            if action == "create":
                if set(payload) != {"action", "request_id", "name", "body", "visibility"}:
                    raise TenantLinearError("Некорректный запрос.")
                request_id = _uuid(payload.get("request_id"), "Идентификатор запроса некорректен.")
                name = _clean_text(payload.get("name"), name=True)
                body = _clean_text(payload.get("body"))
                visibility = payload.get("visibility")
                choices = allowed_visibility(policy)
                if visibility not in choices:
                    raise TenantLinearError("Недостаточно прав для этого типа шаблона.", 403)
                target_department = department_id if visibility == "team" else None
                ident = str(uuid.uuid4())
                cur.execute("""INSERT INTO saas_dialog_reply_templates
                  (tenant_id,id,request_id,name,body,visibility,department_id,created_by,updated_by)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::uuid,%s,%s)
                  ON CONFLICT(tenant_id,created_by,request_id) DO NOTHING
                  RETURNING id::text AS id,name,body,visibility,department_id::text AS department_id,created_by,version""",
                  (tenant, ident, request_id, name, body, visibility, target_department,
                   actor, actor))
                row = cur.fetchone()
                created = row is not None
                if not row:
                    cur.execute("""SELECT id::text AS id,name,body,visibility,
                        department_id::text AS department_id,created_by,version
                      FROM saas_dialog_reply_templates
                      WHERE tenant_id=%s::uuid AND created_by=%s AND request_id=%s::uuid
                        AND deleted_at IS NULL""", (tenant, actor, request_id))
                    row = cur.fetchone()
                if not row:
                    raise TenantLinearError("Не удалось сохранить шаблон.", 409)
                if not created:
                    view_tenant, view_actor, view_policy = _authorize(cur, profile, "view")
                    view_scope = _effective_scope(view_policy, "view")
                    if ((view_tenant, view_actor) != (tenant, actor)
                            or not can_view(row, actor, _department(view_policy), view_scope)):
                        raise TenantLinearError("Шаблон не найден.", 404)
                if created:
                    _audit(cur, tenant, actor, str(row["id"]), "dialog_reply_template_created", row["visibility"])
                conn.commit()
                return _serialize(row, actor, department_id, update_scope)
            if set(payload) not in ({"action", "template_id", "version"},
                                    {"action", "template_id", "version", "name", "body"},
                                    {"action", "template_id", "version", "name", "body", "visibility"}):
                raise TenantLinearError("Некорректный запрос.")
            ident = _uuid(payload.get("template_id"), "Идентификатор шаблона некорректен.")
            version = _version(payload.get("version"))
            cur.execute("""SELECT id::text AS id,name,body,visibility,
                department_id::text AS department_id,created_by,version
              FROM saas_dialog_reply_templates
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND deleted_at IS NULL FOR UPDATE""",
              (tenant, ident))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError("Шаблон не найден.", 404)
            if not can_manage(row, actor, department_id, update_scope):
                raise TenantLinearError("Недостаточно прав для изменения шаблона.", 403)
            if int(row["version"]) != version:
                raise TenantLinearError("Шаблон изменился. Обновите список.", 409)
            if action == "delete":
                if set(payload) != {"action", "template_id", "version"}:
                    raise TenantLinearError("Некорректный запрос.")
                cur.execute("""UPDATE saas_dialog_reply_templates SET deleted_at=clock_timestamp(),
                    updated_by=%s,updated_at=clock_timestamp(),version=version+1
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s
                  RETURNING id::text AS id,visibility""", (actor, tenant, ident, version))
                deleted = cur.fetchone()
                if not deleted:
                    raise TenantLinearError("Шаблон изменился. Обновите список.", 409)
                _audit(cur, tenant, actor, ident, "dialog_reply_template_deleted", deleted["visibility"])
                conn.commit()
                return {"id": ident, "deleted": True}
            name = _clean_text(payload.get("name"), name=True)
            body = _clean_text(payload.get("body"))
            visibility = payload.get("visibility", row["visibility"])
            if visibility != row["visibility"] and int(row["created_by"]) != actor:
                raise TenantLinearError("Изменить область чужого шаблона может только его автор.", 403)
            choices = allowed_visibility(policy)
            preserve_team_scope = visibility == row["visibility"] == "team"
            if visibility not in choices and not preserve_team_scope:
                raise TenantLinearError("Недостаточно прав для этого типа шаблона.", 403)
            target_department = (
                row["department_id"] if preserve_team_scope
                else department_id if visibility == "team" else None
            )
            cur.execute("""UPDATE saas_dialog_reply_templates SET name=%s,body=%s,
                visibility=%s,department_id=%s::uuid,updated_by=%s,updated_at=clock_timestamp(),
                version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s
              RETURNING id::text AS id,name,body,visibility,department_id::text AS department_id,created_by,version""",
              (name, body, visibility, target_department, actor, tenant, ident, version))
            saved = cur.fetchone()
            if not saved:
                raise TenantLinearError("Шаблон изменился. Обновите список.", 409)
            _audit(cur, tenant, actor, ident, "dialog_reply_template_updated", visibility)
            conn.commit()
            return _serialize(saved, actor, department_id, update_scope)
