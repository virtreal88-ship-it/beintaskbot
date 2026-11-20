"""Real-schema regression against an explicitly disposable PostgreSQL database."""
import os
import uuid

import psycopg

from tenant_dialog_reply_templates import migrate


def test_migration_is_idempotent_and_crud_is_tenant_department_scoped():
    dsn = os.environ.get("TEST_DATABASE_URL")
    assert dsn, "TEST_DATABASE_URL must point to disposable PostgreSQL"
    schema = "dialog_templates_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute("""CREATE TABLE saas_tenant_members (
                tenant_id UUID NOT NULL, telegram_id BIGINT NOT NULL,
                PRIMARY KEY(tenant_id,telegram_id))""")
            conn.execute("""CREATE TABLE saas_tenant_departments (
                tenant_id UUID NOT NULL, id UUID NOT NULL,
                PRIMARY KEY(tenant_id,id))""")
            with conn.transaction():
                with conn.cursor() as cur:
                    migrate(cur)
                    migrate(cur)
            assert conn.execute("SELECT to_regclass('saas_dialog_reply_templates')").fetchone()[0]
            assert conn.execute("SELECT to_regclass('saas_dialog_reply_templates_scope_idx')").fetchone()[0]

            tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
            dept_a, dept_b = uuid.uuid4(), uuid.uuid4()
            actor_a, actor_b = 501, 502
            conn.execute("INSERT INTO saas_tenant_members VALUES (%s,%s),(%s,%s)",
                         (tenant_a, actor_a, tenant_b, actor_b))
            conn.execute("INSERT INTO saas_tenant_departments VALUES (%s,%s),(%s,%s)",
                         (tenant_a, dept_a, tenant_b, dept_b))
            template_a, request_a = uuid.uuid4(), uuid.uuid4()
            values = (tenant_a, template_a, request_a, "A team", "synthetic text",
                      "team", dept_a, actor_a, actor_a)
            insert = """INSERT INTO saas_dialog_reply_templates
                (tenant_id,id,request_id,name,body,visibility,department_id,created_by,updated_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
            conn.execute(insert, values)
            # Tenant and department filters reveal the row only in its intended scope.
            assert conn.execute("SELECT id FROM saas_dialog_reply_templates WHERE tenant_id=%s AND department_id=%s",
                                (tenant_a, dept_a)).fetchall() == [(template_a,)]
            assert conn.execute("SELECT id FROM saas_dialog_reply_templates WHERE tenant_id=%s AND department_id=%s",
                                (tenant_b, dept_a)).fetchall() == []
            assert conn.execute("SELECT id FROM saas_dialog_reply_templates WHERE tenant_id=%s AND department_id=%s",
                                (tenant_a, dept_b)).fetchall() == []
            conn.execute("UPDATE saas_dialog_reply_templates SET body=%s,updated_by=%s,version=version+1 WHERE tenant_id=%s AND id=%s",
                         ("updated synthetic text", actor_a, tenant_a, template_a))
            assert conn.execute("SELECT body,version FROM saas_dialog_reply_templates WHERE tenant_id=%s AND id=%s",
                                (tenant_a, template_a)).fetchone() == ("updated synthetic text", 2)
            try:
                with conn.transaction():
                    conn.execute(insert, (tenant_b, uuid.uuid4(), uuid.uuid4(), "rollback", "synthetic",
                                          "personal", None, actor_b, actor_b))
                    raise RuntimeError("exercise rollback")
            except RuntimeError as exc:
                assert str(exc) == "exercise rollback"
            assert conn.execute("SELECT count(*) FROM saas_dialog_reply_templates WHERE tenant_id=%s AND name='rollback'",
                                (tenant_b,)).fetchone()[0] == 0
            # FK and CHECK constraints reject cross-tenant department assignment.
            try:
                conn.execute(insert, (tenant_a, uuid.uuid4(), uuid.uuid4(), "invalid", "synthetic",
                                      "team", dept_b, actor_a, actor_a))
            except psycopg.errors.ForeignKeyViolation:
                pass
            else:
                raise AssertionError("cross-tenant department reference was accepted")
        finally:
            conn.execute("RESET search_path")
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
