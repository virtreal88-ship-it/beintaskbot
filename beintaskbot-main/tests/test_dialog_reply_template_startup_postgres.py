"""Reply-template startup hook on disposable PostgreSQL; no external services."""
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

import tenant_ai_agent_startup as startup
import tenant_dialog_reply_templates as templates
from test_startup_transaction_budget_postgres import database, install_bootstrap, assert_session_released


PROVISION = "DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP"
ENABLE = "DIALOG_REPLY_TEMPLATES_ENABLED"
OBSERVE = "DIALOG_REPLY_TEMPLATES_OBSERVE_ON_STARTUP"
PILOT = "BEINSYSTEMS_OPERATOR_TENANT_ID"


@pytest.fixture
def boot(database, monkeypatch):
    tenant, department = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv(PILOT, tenant)
    monkeypatch.delenv(PROVISION, raising=False)
    monkeypatch.delenv(ENABLE, raising=False)
    monkeypatch.delenv(OBSERVE, raising=False)

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

    for name in ('migrate', 'provision_schema', 'verify_provisioned_schema', '_schema_ready'):
        monkeypatch.setattr(templates, name, unexpected)
    startup.prepare_database()
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (False, False)
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert_session_released(dsn)


def test_opt_in_provisions_after_commit_under_lock_and_keeps_pilot_off(boot, monkeypatch, caplog):
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
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
    assert 'Reply template schema preflight: ready' in caplog.text
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
    assert seen == [True], 'the existing exact shape must be verified without DDL'
    assert not templates.feature_ready({'tenant_id': tenant})
    monkeypatch.setenv(ENABLE, 'true')
    assert not templates.feature_ready({'tenant_id': str(uuid.uuid4())})
    assert_session_released(dsn)


def test_opt_in_requires_explicit_valid_workspace_before_ddl(boot, monkeypatch, caplog):
    dsn, _ = boot
    monkeypatch.setenv(PROVISION, 'true')
    monkeypatch.setenv(PILOT, 'invalid-uuid')

    def forbidden(*args):
        pytest.fail('must not run DDL without an explicitly configured workspace')

    monkeypatch.setattr(templates, 'provision_schema', forbidden)
    startup.prepare_database()
    assert 'Reply template schema preflight: workspace_not_configured' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (False, False)
    assert_session_released(dsn)


def test_wrong_existing_table_is_never_repaired_and_preserves_rows(boot, monkeypatch, caplog):
    dsn, tenant = boot
    with psycopg.connect(dsn) as conn:
        conn.execute("""CREATE TABLE saas_dialog_reply_templates(
          tenant_id uuid, visibility text, department_id uuid,
          updated_at timestamptz, deleted_at timestamptz)""")
        conn.execute("INSERT INTO saas_dialog_reply_templates(tenant_id,visibility) VALUES(%s,'synthetic')",
                     (tenant,))
    monkeypatch.setenv(PROVISION, 'true')
    def no_ddl(*_args):
        pytest.fail('unexpected existing schema must not be repaired')
    monkeypatch.setattr(templates, 'migrate', no_ddl)
    startup.prepare_database()
    assert 'Reply template schema preflight: unverified' in caplog.text
    assert 'synthetic' not in caplog.text
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (True, False)
        assert conn.execute('SELECT visibility FROM saas_dialog_reply_templates').fetchall() == [('synthetic',)]
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert_session_released(dsn)


def test_wrong_named_index_fails_closed_and_is_not_rebuilt(boot, monkeypatch, caplog):
    dsn, _ = boot
    monkeypatch.setenv(PROVISION, 'true')
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        conn.execute('DROP INDEX saas_dialog_reply_templates_scope_idx')
        conn.execute('CREATE INDEX saas_dialog_reply_templates_scope_idx ON saas_dialog_reply_templates (body)')
    startup.prepare_database()
    assert 'Reply template schema preflight: unverified' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT pg_get_indexdef('saas_dialog_reply_templates_scope_idx'::regclass)").fetchone()[0].endswith('(body)')
    assert_session_released(dsn)


def test_error_after_ddl_rolls_back_both_new_objects_not_base_commit(boot, monkeypatch, caplog):
    dsn, _ = boot
    monkeypatch.setenv(PROVISION, 'true')
    failure = RuntimeError('Synthetic verification failure')

    def fail_after_ddl(cur):
        cur.execute("SELECT to_regclass('saas_dialog_reply_templates') IS NOT NULL AS visible")
        assert cur.fetchone()['visible']
        raise failure

    monkeypatch.setattr(templates, 'verify_provisioned_schema', fail_after_ddl)
    startup.prepare_database()
    assert 'Reply template schema preflight: unverified' in caplog.text
    assert str(failure) not in caplog.text
    with psycopg.connect(dsn) as conn:
        assert objects(conn) == (False, False)
        assert conn.execute('SELECT * FROM budget_webhook').fetchall() == [(41,)]
    assert_session_released(dsn)


def test_read_only_observation_reports_absent_then_existing_exact_shape(boot, monkeypatch, caplog):
    dsn,_=boot
    def forbidden(*_args):
        pytest.fail('schema observation must not invoke any provisioning')
    migrate=templates.migrate
    monkeypatch.setattr(templates, 'migrate', forbidden)
    monkeypatch.setenv(OBSERVE, 'true')
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
    assert 'Reply template schema observation: absent' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert objects(conn)==(False,False)
    # Create a synthetic schema through its approved fixture path, not through
    # observation. No real content, messages, or customer data is touched.
    with psycopg.connect(dsn) as conn:
        migrate(conn)
    verify=templates.verify_provisioned_schema
    def check(cur):
        cur.execute("SELECT current_setting('transaction_read_only') AS readonly,current_setting('transaction_timeout') AS budget")
        assert cur.fetchone()=={'readonly':'on','budget':'5s'}
        verify(cur)
    monkeypatch.setattr(templates,'verify_provisioned_schema',check)
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
    assert 'Reply template schema observation: matching' in caplog.text
    assert_session_released(dsn)


def test_observation_of_unexpected_existing_schema_never_repairs(boot,monkeypatch,caplog):
    dsn,_=boot
    with psycopg.connect(dsn) as conn:
        conn.execute('CREATE TABLE saas_dialog_reply_templates(marker text)')
        conn.execute("INSERT INTO saas_dialog_reply_templates VALUES ('no customer data')")
    monkeypatch.setenv(OBSERVE,'true')
    with caplog.at_level('INFO',logger=startup.__name__):
        startup.prepare_database()
    assert 'Reply template schema observation: unverified' in caplog.text
    assert 'no customer data' not in caplog.text
    with psycopg.connect(dsn) as conn:
        assert objects(conn)==(True,False)
        assert conn.execute('SELECT marker FROM saas_dialog_reply_templates').fetchall()==[('no customer data',)]
    assert_session_released(dsn)


@pytest.mark.parametrize('parent',['saas_tenant_members','saas_tenant_departments'])
def test_busy_fk_parent_is_nowait_and_does_not_create_or_log_details(boot,monkeypatch,caplog,parent):
    dsn,_=boot
    startup.prepare_database()  # Commit synthetic base relations before simulating a live writer.
    monkeypatch.setenv(PROVISION,'true')
    def forbidden(*_args):
        pytest.fail('must obtain both FK locks before template DDL')
    monkeypatch.setattr(templates,'migrate',forbidden)
    with psycopg.connect(dsn) as writer:
        writer.execute(sql.SQL('LOCK TABLE {} IN ROW EXCLUSIVE MODE').format(sql.Identifier(parent)))
        with psycopg.connect(dsn,row_factory=dict_row) as candidate:
            with pytest.raises(psycopg.errors.LockNotAvailable) as conflict:
                with candidate.transaction(),candidate.cursor() as cur:
                    # Native NOWAIT emits a different error from a timer-based
                    # lock_timeout. No timing assertion or production writes.
                    cur.execute("SET LOCAL lock_timeout='2s'")
                    templates.provision_schema(cur)
            assert 'could not obtain lock on relation' in conflict.value.diag.message_primary
            # The Worker wrapper doesn't fail ordinary startup or expose the
            # relation name; a transaction remains usable immediately after.
            startup._provision_reply_template_schema(candidate)
            assert candidate.execute('SELECT 1 AS ok').fetchone()=={'ok':1}
        with psycopg.connect(dsn) as observer:
            assert objects(observer)==(False,False)
    assert 'Reply template schema preflight: unverified' in caplog.text
    assert parent not in caplog.text
    assert_session_released(dsn)


def test_exact_existing_schema_never_locks_fk_parents_for_writes(boot,monkeypatch,caplog):
    dsn,_=boot
    monkeypatch.setenv(PROVISION,'true')
    startup.prepare_database()
    def forbidden(*_args):
        pytest.fail('existing exact schema must not run DDL')
    monkeypatch.setattr(templates,'migrate',forbidden)
    with psycopg.connect(dsn) as writer:
        writer.execute("""LOCK TABLE saas_tenant_members,saas_tenant_departments
                          IN ROW EXCLUSIVE MODE""")
        with psycopg.connect(dsn,row_factory=dict_row) as candidate, caplog.at_level('INFO',logger=startup.__name__):
            startup._provision_reply_template_schema(candidate)
    assert 'Reply template schema preflight: ready' in caplog.text
    with psycopg.connect(dsn) as conn:
        assert objects(conn)==(True,True)
    assert_session_released(dsn)
