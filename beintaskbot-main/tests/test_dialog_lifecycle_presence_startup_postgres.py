"""Presence-only startup provisioning on disposable PostgreSQL 17+."""
import os
import uuid

import psycopg
import pytest

import tenant_ai_agent_startup as startup
import tenant_dialog_presence as presence
from test_startup_transaction_budget_postgres import database, install_bootstrap, assert_session_released


@pytest.fixture
def boot(database, monkeypatch):
    tenant = str(uuid.uuid4())
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", tenant)
    for name in (presence.STATUS_PROVISION_ENV, presence.STATUS_ENABLED_ENV, presence.ROUTING_ENV,
                 "DIALOG_REPLY_TEMPLATES_PROVISION_ON_STARTUP"):
        monkeypatch.delenv(name, raising=False)

    def ensure_base(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS saas_tenants(id uuid PRIMARY KEY,status text NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS saas_tenant_members(
            tenant_id uuid, telegram_id bigint, PRIMARY KEY(tenant_id,telegram_id))""")
        conn.execute("""INSERT INTO saas_tenants VALUES (%s,'active') ON CONFLICT DO NOTHING""", (tenant,))
        conn.execute("""INSERT INTO saas_tenant_members VALUES (%s,11) ON CONFLICT DO NOTHING""", (tenant,))
        from tenant_dialog_rollout import migrate
        with conn.cursor() as cur:
            migrate(cur)
    install_bootstrap(monkeypatch, database, ensure_base)
    return database, tenant


def present(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT to_regclass('saas_dialog_employee_presence') IS NOT NULL").fetchone()[0]


def test_default_boot_does_not_probe_or_provision_presence(boot, monkeypatch):
    dsn, _ = boot

    def miss(*_):
        pytest.fail("default startup must not probe or migrate presence")

    monkeypatch.setattr(presence, "_available", miss)
    monkeypatch.setattr(presence, "migrate", miss)
    startup.prepare_database()
    assert not present(dsn)
    assert_session_released(dsn)


def test_status_flag_alone_never_runs_startup_ddl(boot, monkeypatch):
    dsn, _ = boot
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, "true")
    startup.prepare_database()
    assert not present(dsn)
    assert_session_released(dsn)


def test_provisioning_must_not_activate_preexisting_routing(boot, monkeypatch, caplog):
    dsn, _ = boot
    monkeypatch.setenv(presence.STATUS_PROVISION_ENV, "true")
    monkeypatch.setenv(presence.ROUTING_ENV, "true")
    startup.prepare_database()
    assert "Employee status schema preflight: unverified" in caplog.text
    assert not present(dsn)
    assert_session_released(dsn)


def test_opted_in_uses_existing_budget_creates_empty_once_and_preserves_rows(boot, monkeypatch, caplog):
    dsn, tenant = boot
    monkeypatch.setenv(presence.STATUS_PROVISION_ENV, "true")
    monkeypatch.delenv("BEINSYSTEMS_OPERATOR_TENANT_ID", raising=False)
    assert presence.STATUS_ENABLED_ENV not in os.environ
    caplog.set_level("INFO", logger=startup.__name__)
    calls, original = [], presence.migrate

    def migrate(cur):
        assert not present(dsn)
        with psycopg.connect(dsn) as observer:
            assert observer.execute("SELECT 1 FROM budget_markers WHERE name='ready'").fetchone()
            assert not observer.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                                        (startup._STARTUP_SCHEMA_LOCK,)).fetchone()[0]
        cur.execute("SELECT current_setting('transaction_timeout') AS budget,current_setting('lock_timeout') AS lock")
        assert cur.fetchone() == {"budget": "5s", "lock": "2s"}
        calls.append(True)
        original(cur)

    monkeypatch.setattr(presence, "migrate", migrate)
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM saas_dialog_employee_presence").fetchone()[0] == 0
        conn.execute("""INSERT INTO saas_dialog_employee_presence(tenant_id,employee_id,status,updated_by)
            VALUES(%s,11,'away',11)""", (tenant,))
    startup.prepare_database()
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT status FROM saas_dialog_employee_presence").fetchall() == [("away",)]
    assert "Employee status schema preflight: created" in caplog.text
    assert "Employee status schema preflight: ready" in caplog.text
    assert calls == [True]
    assert_session_released(dsn)


def test_bad_shape_rolls_back_whole_optional_provision_without_blocking_boot(boot, monkeypatch, caplog):
    dsn, tenant = boot
    monkeypatch.setenv(presence.STATUS_PROVISION_ENV, "true")
    original = presence.migrate

    def incompatible(cur):
        original(cur)
        cur.execute("ALTER TABLE saas_dialog_employee_presence ADD COLUMN unexpected text")

    monkeypatch.setattr(presence, "migrate", incompatible)
    startup.prepare_database()
    assert "Employee status schema preflight: unverified" in caplog.text
    assert "Unexpected presence columns" not in caplog.text
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT 1 FROM budget_markers WHERE name='ready'").fetchone()
    assert not present(dsn)
    assert_session_released(dsn)


def test_provision_does_not_install_unrelated_missing_dialogue_authorization_schema(boot, monkeypatch, caplog):
    dsn, tenant = boot
    monkeypatch.setenv(presence.STATUS_PROVISION_ENV, "true")
    import tenant_dialog_rollout
    monkeypatch.setattr(tenant_dialog_rollout, "migrate", lambda cur: None)
    startup.prepare_database()
    assert "Employee status schema preflight: unverified" in caplog.text
    assert not present(dsn)
    assert_session_released(dsn)

def test_optional_budget_cancels_and_reverts_ddl_without_blocking_boot(boot, monkeypatch, caplog):
    dsn, _ = boot
    monkeypatch.setenv(presence.STATUS_PROVISION_ENV, "true")
    original = presence.migrate

    def slow_after_create(cur):
        original(cur)
        cur.execute("SET LOCAL statement_timeout='40ms'")
        cur.execute("SELECT pg_sleep(1)")

    monkeypatch.setattr(presence, "migrate", slow_after_create)
    startup.prepare_database()
    assert "Employee status schema preflight: unverified" in caplog.text
    assert not present(dsn)
    assert_session_released(dsn)
