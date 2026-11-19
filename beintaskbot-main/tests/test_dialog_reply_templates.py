"""Tenant isolation and permission checks for local reply templates."""
import asyncio
import json
import uuid
from unittest.mock import Mock

import pytest
from aiohttp.test_utils import make_mocked_request

import tenant_dialog_reply_templates as templates
from tenant_linear_policy import TenantLinearError

TENANT = "00000000-0000-4000-8000-000000000101"
OTHER = "00000000-0000-4000-8000-000000000102"
DEPT = "00000000-0000-4000-8000-000000000201"
ACTOR = 33


class Policy:
    def __init__(self, scope="self", department=DEPT, role="worker"):
        self.profile = {"role": role, "department_id": department}

    def action_scope(self, permission, action):
        return self._scope

    def legacy_allows_action(self, permission, action):
        return True


def policy(scope="self", department=DEPT, role="worker"):
    value = Policy(scope, department, role)
    value._scope = scope
    return value


def item(visibility, created_by=ACTOR, department=DEPT):
    return {"visibility": visibility, "created_by": created_by, "department_id": department}


def test_personal_team_shared_visibility_and_edit_rights():
    assert templates.can_view(item("personal"), ACTOR, DEPT, "self")
    assert not templates.can_view(item("personal", 11), ACTOR, DEPT, "all")
    assert templates.can_manage(item("personal"), ACTOR, DEPT, "self")
    assert not templates.can_manage(item("personal", 11), ACTOR, DEPT, "all")
    assert templates.can_view(item("team", department=DEPT), 44, DEPT, "department")
    assert templates.can_manage(item("team", department=DEPT), 44, DEPT, "department")
    other_dept = "00000000-0000-4000-8000-000000000202"
    assert not templates.can_view(item("team", department=other_dept), 44, DEPT, "department")
    assert templates.can_view(item("team", department=other_dept), 44, DEPT, "all")
    assert templates.can_manage(item("team", department=other_dept), 44, DEPT, "all")
    assert templates.can_view(item("shared"), 44, DEPT, "self")
    assert not templates.can_manage(item("shared"), 44, DEPT, "department")
    assert templates.can_manage(item("shared"), 44, DEPT, "all")


def test_text_validation_and_visibility_choices_are_closed():
    assert templates._clean_text("  Greeting  ", name=True) == "Greeting"
    with pytest.raises(TenantLinearError):
        templates._clean_text("  ", name=True)
    with pytest.raises(TenantLinearError):
        templates._clean_text("x" * (templates.MAX_BODY + 1))
    with pytest.raises(TenantLinearError):
        templates._clean_text("x\u0001")
    assert templates.allowed_visibility(policy("self")) == ["personal"]
    assert templates.allowed_visibility(policy("department")) == ["personal", "team"]
    assert templates.allowed_visibility(policy("all", department=None, role="owner")) == ["personal", "shared"]


def test_explicit_schema_migration_is_additive_and_has_no_runtime_ddl():
    class Cursor:
        def __init__(self):
            self.sql = []
        def execute(self, query):
            self.sql.append(query)
    cur = Cursor()
    templates.migrate(cur)
    joined = "\n".join(cur.sql)
    assert "saas_dialog_reply_templates" in joined
    assert "CREATE TABLE" in joined
    assert "UNIQUE(tenant_id,created_by,request_id)" in joined
    assert "deleted_at" in joined
    assert "CREATE INDEX" in joined
    assert "DROP " not in joined


class Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.current = None
        self.statements = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        self.current = self.results.pop(0) if self.results else None

    def fetchone(self):
        return self.current

    def fetchall(self):
        value = self.current
        return value if isinstance(value, list) else ([] if value is None else [value])


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def install_db(monkeypatch, cursor):
    import tenant_platform
    connection = Connection(cursor)
    monkeypatch.setattr(tenant_platform, "_connect", lambda: connection)
    return connection, cursor


def authorize(monkeypatch, scope="self", department=DEPT):
    monkeypatch.setattr(templates, "_authorize",
                        lambda cur, profile, action: (TENANT, ACTOR, policy(scope, department)))
    monkeypatch.setattr(templates, "_schema_ready", lambda cur: True)


def test_list_is_tenant_bound_and_returns_only_scoped_rows(monkeypatch):
    row = {"id": str(uuid.uuid4()), "name": "Greeting", "body": "Welcome",
           "visibility": "team", "department_id": DEPT, "created_by": 11, "version": 3}
    connection, cur = install_db(monkeypatch, Cursor([[row]]))
    authorize(monkeypatch, "department")
    result = templates.list_templates({"tenant_id": TENANT, "telegram_id": ACTOR})
    assert result["templates"][0]["name"] == "Greeting"
    assert result["templates"][0]["can_edit"] is True  # current department can manage team templates
    query, params = cur.statements[0]
    assert "tenant_id=%s::uuid" in query
    assert params == (TENANT, ACTOR, "department", DEPT)


def test_create_retry_returns_existing_without_duplicate_audit(monkeypatch):
    existing = {"id": str(uuid.uuid4()), "name": "Greeting", "body": "Welcome",
                "visibility": "personal", "department_id": None, "created_by": ACTOR, "version": 1}
    connection, cur = install_db(monkeypatch, Cursor([None, existing]))
    authorize(monkeypatch, "self")
    result = templates.mutate_template({"tenant_id": TENANT, "telegram_id": ACTOR},
        {"action": "create", "request_id": str(uuid.uuid4()), "name": "Greeting",
         "body": "Welcome", "visibility": "personal"})
    assert result["id"] == existing["id"]
    assert not any("INSERT INTO saas_tenant_audit_events" in sql for sql, _ in cur.statements)
    assert connection.commits == 1


def test_stale_template_version_is_rejected_before_update(monkeypatch):
    existing = {"id": str(uuid.uuid4()), "name": "Old", "body": "Text",
                "visibility": "personal", "department_id": None, "created_by": ACTOR, "version": 2}
    connection, cur = install_db(monkeypatch, Cursor([existing]))
    authorize(monkeypatch, "self")
    with pytest.raises(TenantLinearError) as stale:
        templates.mutate_template({"tenant_id": TENANT, "telegram_id": ACTOR},
            {"action": "update", "template_id": existing["id"], "version": 1,
             "name": "New", "body": "New text"})
    assert stale.value.status == 409
    assert not any(sql.lstrip().startswith("UPDATE saas_dialog_reply_templates") for sql, _ in cur.statements)


def test_cross_tenant_payload_identity_is_rejected_before_database(monkeypatch):
    import tenant_dialog_reply_templates_api as api
    # _expected is in the route; a mismatched body must fail before the store is called.
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request
    profile={"tenant_id":TENANT,"telegram_id":ACTOR}
    called=[]
    monkeypatch.setattr(api,"mutate_template",lambda *_: called.append(True))
    handler=api.handler(lambda _request:profile,"https://crm.test",Mock())
    request=make_mocked_request("POST","/api/platform/ai/dialogs/reply-templates",
        headers={"Origin":"https://crm.test","Content-Type":"application/json"},
        payload=json.dumps({"action":"create","expected_tenant_id":OTHER,
            "expected_user_id":ACTOR,"request_id":str(uuid.uuid4()),"name":"x","body":"y","visibility":"personal"}).encode())
    response=asyncio.run(handler(request))
    assert response.status==409
    assert not called
    assert response.headers["Cache-Control"]=="no-store"
