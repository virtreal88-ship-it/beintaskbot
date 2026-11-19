"""Tenant-local reply templates with membership-scoped visibility and no provider IO."""
import json
import uuid

from tenant_linear_policy import TenantLinearError

TABLE = "saas_dialog_reply_templates"
VISIBILITIES = frozenset({"personal", "team", "shared"})
MAX_NAME = 120
MAX_BODY = 4000


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
            if visibility not in choices:
                raise TenantLinearError("Недостаточно прав для этого типа шаблона.", 403)
            target_department = department_id if visibility == "team" else None
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
