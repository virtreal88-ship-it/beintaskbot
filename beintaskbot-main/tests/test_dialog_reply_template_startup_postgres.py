"""Reply-template startup hook on disposable PostgreSQL; no external services."""
import uuid

import psycopg
import pytest

import tenant_ai_agent_startup as startup
import tenant_dialog_reply_templates as templates
from test_startup_transaction_budget_postgres import database, install_bootstrap, assert_session_released


PROVISION = "DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP"
ENABLE = "DIALOG_REPLY_TEMPLATES_ENABLED"
PILOT = "BEINSYSTEMS_OPERATOR_TENANT_ID"


@pytest.fixture
def boot(database, monkeypatch):
    tenant, department = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv(PILOT, tenant)
    monkeypatch.delenv(PROVISION, raising=False)
    monkeypatch.delenv(ENABLE, raising=False)

    # Model existing base migrations, using the real startup transaction and
    # advisory lock. The optional hook must see these committed tables/rows.
    def ensure_base(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS saas_tenant_members(
            tenant_id uuid, telegram_id bigint, PRIMARY KEY(tenant_id,telegram_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS saas_tenant_departments(
            tenant_id uuid, id uuid, PRIMARY KEY(tenant_id,id))""")
        conn.execute("""INSERT INTO saas_tenant_members VALUES (%s, 11)
                        ON CONFLICT DO NOTHING""", (tenant,))
        conn.execute("""INSERT INTO saas_tenant_departments VALUES (%s,%s)
                        ON CONFLICT DO NOTHING""", (tenant, department))
        conn.execute('INSERT INTO budget_webhook VALUES(41) ON CONFLICT DO NOTHING')

    install_bootstrap(monkeypatch, database, ensure_base)
    return database, tenant


def objects(conn):
    return conn.execute("""SELECT to_regclass('saas_dialog_reply_templates') IS NOT NULL,
                            to_regclass('saas_dialog_reply_templates_scope_idx') IS NOT NULL""").fetchone()


def test_stock_boot_never_touches_template_schema_even_if_feature_enabled(boot, monkeypatch):
    dsn, _ = boot
    monkeypatch.setenv(ENABLE, 'true')

    def unexpected(*args):
        pytest.fail('stock boot must not execute template DDL or probes')

    for name in ('migrate', 'verify_provisioned_schema', '_schema_ready'):
        monkeypatch.setattr(templates, name, unexpected)
    startup.prepare_database()
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (False, False)
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert_session_released(dsn)


def test_opt_in_provisions_after_commit_under_lock_and_keeps_pilot_off(boot, monkeypatch):
    dsn, tenant = boot
    monkeypatch.setenv(PROVISION, 'true')
    seen = []
    migrate = templates.migrate

    def assert_boundary(cur):
        with psycopg.connect(dsn) as observer:
            assert observer.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
            assert not observer.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',
                (startup._STARTUP_SCHEMA_LOCK,)).fetchone()[0]
        cur.execute("SELECT current_setting('transaction_timeout') AS budget,current_setting('lock_timeout') AS lock")
        assert cur.fetchone() == {'budget': '5s', 'lock': '2s'}
        seen.append(True)
        migrate(cur)

    monkeypatch.setattr(templates, 'migrate', assert_boundary)
    startup.prepare_database()
    request_id, template_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (True, True)
        assert conn.execute('SELECT count(*) FROM saas_dialog_reply_templates').fetchone()[0] == 0
        conn.execute("""INSERT INTO saas_dialog_reply_templates
          (tenant_id,id,request_id,name,body,visibility,created_by,updated_by)
          VALUES(%s,%s,%s,'Synthetic','Existing text','personal',11,11)""",
          (tenant, template_id, request_id))
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT id,name,body,version FROM saas_dialog_reply_templates').fetchall() == [
            (template_id, 'Synthetic', 'Existing text', 1)]
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert seen == [True, True]
    assert not templates.feature_ready({'tenant_id': tenant})
    monkeypatch.setenv(ENABLE, 'true')
    assert not templates.feature_ready({'tenant_id': str(uuid.uuid4())})
    assert_session_released(dsn)


def test_opt_in_requires_explicit_valid_workspace_before_ddl(boot, monkeypatch):
    dsn, _ = boot
    monkeypatch.setenv(PROVISION, 'true')
    monkeypatch.setenv(PILOT, 'invalid-uuid')

    def forbidden(*args):
        pytest.fail('must not run DDL without an explicitly configured workspace')

    monkeypatch.setattr(templates, 'migrate', forbidden)
    with pytest.raises(RuntimeError, match='configured BeinSystems workspace UUID'):
        startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (False, False)
    assert_session_released(dsn)


def test_wrong_existing_table_rolls_back_added_index_preserves_rows(boot, monkeypatch):
    dsn, tenant = boot
    with psycopg.connect(dsn) as conn:
        conn.execute("""CREATE TABLE saas_dialog_reply_templates(
          tenant_id uuid, visibility text, department_id uuid,
          updated_at timestamptz, deleted_at timestamptz)""")
        conn.execute("INSERT INTO saas_dialog_reply_templates(tenant_id,visibility) VALUES(%s,'synthetic')",
                     (tenant,))
    monkeypatch.setenv(PROVISION, 'true')
    with pytest.raises(RuntimeError, match='Unexpected reply-template columns'):
        startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (True, False)
        assert conn.execute('SELECT visibility FROM saas_dialog_reply_templates').fetchall() == [('synthetic',)]
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert_session_released(dsn)


def test_wrong_named_index_fails_closed_and_is_not_rebuilt(boot, monkeypatch):
    dsn, _ = boot
    monkeypatch.setenv(PROVISION, 'true')
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        conn.execute('DROP INDEX saas_dialog_reply_templates_scope_idx')
        conn.execute('CREATE INDEX saas_dialog_reply_templates_scope_idx ON saas_dialog_reply_templates (body)')
    with pytest.raises(RuntimeError, match='Unexpected reply-template index'):
        startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT pg_get_indexdef('saas_dialog_reply_templates_scope_idx'::regclass)").fetchone()[0].endswith('(body)')
    assert_session_released(dsn)


def test_error_after_ddl_rolls_back_both_new_objects_not_base_commit(boot, monkeypatch):
    dsn, _ = boot
    monkeypatch.setenv(PROVISION, 'true')
    failure = RuntimeError('Synthetic verification failure')

    def fail_after_ddl(cur):
        cur.execute("SELECT to_regclass('saas_dialog_reply_templates') IS NOT NULL AS visible")
        assert cur.fetchone()['visible']
        raise failure

    monkeypatch.setattr(templates, 'verify_provisioned_schema', fail_after_ddl)
    with pytest.raises(RuntimeError) as exc:
        startup.prepare_database()
    assert exc.value is failure
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (False, False)
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert_session_released(dsn)
