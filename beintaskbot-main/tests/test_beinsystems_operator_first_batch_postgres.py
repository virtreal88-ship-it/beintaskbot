"""Disposable PostgreSQL transaction checks. Never connects outside loopback."""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from time import sleep

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

import tenant_beinsystems_operator_company_import as batch


@pytest.fixture
def state(monkeypatch):
    dsn = os.environ.get("BEINTASKBOT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Disposable PostgreSQL DSN is required")
    parts = conninfo_to_dict(dsn)
    if parts.get("host") not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Only disposable loopback PostgreSQL is supported")
    schema = "op_batch_" + uuid.uuid4().hex
    tenant, instance = str(uuid.uuid4()), str(uuid.uuid4())
    actor, received_at = 7, datetime.now(timezone.utc)
    fingerprint = batch._identifier_fingerprint("owner@example")
    credentials = {"connection_instance_id": instance, "identity_fingerprint": fingerprint,
                   "generation": "g", "received_at": received_at, "token": "mock"}
    cipher = Fernet(Fernet.generate_key())
    envelope = cipher.encrypt(json.dumps({"tenant_id": tenant, "owner_telegram_id": actor,
        "provider": batch.operator.PROVIDER, "purpose": "pending_auth_verification",
        "token": "mock"}).encode())

    def connect():
        return psycopg.connect(dsn, row_factory=dict_row, options=f"-csearch_path={schema}")

    profile = {"role": "owner", "active": True, "tenant_id": tenant, "telegram_id": actor}
    monkeypatch.setattr(batch.operator, "first_company_batch_confirm_enabled", lambda: True)
    monkeypatch.setattr(batch.operator, "_connect", connect)
    monkeypatch.setattr(batch.operator, "_ensure_schema", lambda *_: None)
    monkeypatch.setattr(batch.operator, "_current_profile", lambda *_: profile)
    monkeypatch.setattr(batch.operator, "_operator_row", lambda *_, **__: None)
    monkeypatch.setattr(batch.operator, "_fernet", lambda: cipher)
    monkeypatch.setattr(batch, "current_access", lambda *_: profile)
    monkeypatch.setattr(batch, "authorize_create", lambda *_: None)

    class Full:
        def __init__(self, _): pass
        def has_scoped_access(self, _): return False

    monkeypatch.setattr(batch, "TenantPolicy", Full)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            with connect() as c:
                c.execute("""CREATE TABLE saas_tenant_audit_events (
                  id uuid PRIMARY KEY, tenant_id uuid,actor_telegram_id bigint,action text,
                  entity_type text,entity_id text,payload jsonb)""")
                c.execute("""CREATE TABLE saas_crm_native_companies (
                  id uuid PRIMARY KEY,tenant_id uuid,name text,phone text,email text,
                  description text,responsible_id bigint,access_responsible_id bigint,archived_at timestamptz)""")
                c.execute("""CREATE TABLE saas_beinsystems_operator_company_imports (
                  tenant_id uuid,external_account text,company_id uuid,created_by bigint,
                  updated_by bigint,last_seen_connection_instance_id uuid,last_seen_generation text,
                  last_seen_at timestamptz,updated_at timestamptz,
                  PRIMARY KEY(tenant_id,external_account))""")
                c.execute("""CREATE TABLE saas_beinsystems_operator_links (
                  tenant_id uuid,local_entity_type text,external_entity_type text,external_scope_id text,
                  external_id text,local_entity_id uuid,connection_generation text,
                  connection_instance_id uuid,revoked_at timestamptz)""")
                c.execute("""CREATE TABLE saas_beinsystems_operator_accounts_capabilities (
                  tenant_id uuid,owner_telegram_id bigint,candidate_received_at timestamptz,
                  operator_identifier_fingerprint text,connection_generation text,
                  connection_instance_id uuid,expires_at timestamptz,PRIMARY KEY(tenant_id))""")
                c.execute("""CREATE TABLE saas_beinsystems_operator_pending_auth_v2 (
                  tenant_id uuid, owner_telegram_id bigint,received_at timestamptz,
                  token_ciphertext bytea,activated_at timestamptz,PRIMARY KEY(tenant_id,owner_telegram_id))""")
                c.execute("""INSERT INTO saas_beinsystems_operator_accounts_capabilities
                  VALUES(%s,%s,%s,%s,'g',%s,now()+interval '1 hour')""",
                  (tenant, actor, received_at, fingerprint, instance))
                c.execute("""INSERT INTO saas_beinsystems_operator_pending_auth_v2
                  VALUES(%s,%s,%s,%s,NULL)""", (tenant, actor, received_at, envelope))

            def run(accounts, receipt=None):
                return batch._apply_company_import(profile, tenant, actor,
                    [{"account": name} for name in accounts], credentials,
                    first_batch=True, preview_receipt=receipt, preview_scope="scope")

            yield connect, run, tenant
        finally:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_concurrent_confirmation_replay_and_archived_collision(state):
    connect, run, tenant = state
    with connect() as c:
        c.execute("""INSERT INTO saas_crm_native_companies(id,tenant_id,name,archived_at)
          VALUES(%s,%s,'Archived',now())""", (uuid.uuid4(), tenant))
    r = run(["archived", "unique"])
    assert r["rows"] == [{"account": "archived", "action": "manual_review"},
                         {"account": "unique", "action": "create"}]

    def confirm(_):
        try:
            return run(["archived", "unique"], r["receipt"])["created"]
        except batch.operator.OperatorError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, range(2))) == [1, 409]
    with connect() as c:
        assert c.execute("""SELECT name,phone,email,description,responsible_id
          FROM saas_crm_native_companies WHERE archived_at IS NULL""").fetchall() == [
            {"name": "unique", "phone": "", "email": "", "description": "", "responsible_id": None}]
        assert c.execute("""SELECT count(*) AS n FROM saas_tenant_audit_events
          WHERE action='operator_first_company_batch'""").fetchone()["n"] == 1


def test_mapping_rollback_clears_nonce_and_rotation_rejects_old_receipt(state, monkeypatch):
    connect, run, _ = state
    r = run(["a", "b"])
    real_put = batch._put_mapping
    with monkeypatch.context() as m:
        m.setattr(batch, "_put_mapping", lambda *args: False if args[2] == "b" else real_put(*args))
        with pytest.raises(batch.operator.OperatorError):
            run(["a", "b"], r["receipt"])
    with connect() as c:
        for table in ("saas_crm_native_companies", "saas_tenant_audit_events",
                      "saas_beinsystems_operator_company_imports"):
            assert c.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
    assert run(["a", "b"], r["receipt"])["created"] == 2
    next_receipt = run(["c"])["receipt"]
    with connect() as c:
        c.execute("""UPDATE saas_beinsystems_operator_pending_auth_v2
          SET received_at=received_at+interval '1 second'""")
    with pytest.raises(batch.operator.OperatorError):
        run(["c"], next_receipt)
    with connect() as c:
        assert c.execute('SELECT name FROM saas_crm_native_companies ORDER BY name COLLATE "C"').fetchall() == [
            {"name": "a"}, {"name": "b"}]


def test_historical_company_identity_blocks_duplicates_without_rebinding(state):
    connect, run, tenant = state
    old_instance, foreign_tenant = str(uuid.uuid4()), str(uuid.uuid4())
    original, conflict = str(uuid.uuid4()), str(uuid.uuid4())
    with connect() as c:
        for company, name in ((original, "Existing CRM company"), (conflict, "Other CRM company")):
            c.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s,%s,%s)",
                      (company, tenant, name))
        for account, company in (("stable", original), ("CASE-ALT", original), ("clash", original)):
            c.execute("""INSERT INTO saas_beinsystems_operator_company_imports
              (tenant_id,external_account,company_id,last_seen_connection_instance_id,last_seen_generation)
              VALUES(%s,%s,%s,%s,'old')""", (tenant, account, company, old_instance))
        for owner, account, company, revoked in (
                (tenant, "old-link", original, False),
                (tenant, "UPPER-LINK", original, False),
                (tenant, "stable", original, False),
                (tenant, "CLASH", conflict, False),
                (tenant, "revoked", original, True),
                (foreign_tenant, "foreign", original, False)):
            c.execute("""INSERT INTO saas_beinsystems_operator_links VALUES(
              %s,'company','account','',%s,%s,'old',%s,CASE WHEN %s THEN now() END)""",
              (owner, account, company, old_instance, revoked))

    ids = ["case-alt", "clash", "foreign", "old-link", "revoked", "stable", "upper-link"]
    preview = run(ids)
    assert {row["account"]: row["action"] for row in preview["rows"]} == {
        "case-alt": "manual_review", "clash": "manual_review", "foreign": "create",
        "old-link": "manual_review", "revoked": "create", "stable": "reuse",
        "upper-link": "manual_review"}
    assert run(ids, preview["receipt"])["created"] == 2
    with connect() as c:
        assert c.execute("""SELECT last_seen_generation,last_seen_connection_instance_id::text AS instance
          FROM saas_beinsystems_operator_company_imports WHERE external_account='stable'""").fetchone() == {
            "last_seen_generation": "old", "instance": old_instance}
        assert c.execute("SELECT count(*) AS n FROM saas_beinsystems_operator_links").fetchone()["n"] == 6
        assert c.execute('SELECT name FROM saas_crm_native_companies ORDER BY name COLLATE "C"').fetchall() == [
            {"name": "Existing CRM company"}, {"name": "Other CRM company"},
            {"name": "foreign"}, {"name": "revoked"}]

    preview_late = run(["late"])
    assert preview_late["rows"][0]["action"] == "create"
    with connect() as c:
        c.execute("""INSERT INTO saas_beinsystems_operator_links VALUES(
          %s,'company','account','','LATE',%s,'old',%s,NULL)""", (tenant, original, old_instance))
    with pytest.raises(batch.operator.OperatorError) as err:
        run(["late"], preview_late["receipt"])
    assert err.value.status == 409
    with connect() as c:
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE name='late'").fetchone()["n"] == 0


def test_capability_expires_while_waiting_for_import_lock(state, monkeypatch):
    connect, run, tenant = state
    r = run(["new"])
    early_check = Event()
    original = batch._lock_and_check_selected_source

    def check(*args):
        original(*args)
        early_check.set()

    monkeypatch.setattr(batch, "_lock_and_check_selected_source", check)
    with connect() as c:
        c.execute("""UPDATE saas_beinsystems_operator_accounts_capabilities
          SET expires_at=clock_timestamp()+interval '3 seconds'""")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connect() as blocking:
            blocking.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             (tenant + ":operator-company-import",))
            future = pool.submit(run, ["new"], r["receipt"])
            assert early_check.wait(timeout=2)
            sleep(3.1)
            assert not future.done()
        with pytest.raises(batch.operator.OperatorError, match="истёк") as err:
            future.result(timeout=10)
    assert err.value.status == 409
    with connect() as c:
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies").fetchone()["n"] == 0
        assert c.execute("SELECT count(*) AS n FROM saas_tenant_audit_events").fetchone()["n"] == 0


def test_preview_only_cannot_mint_or_commit_a_previous_receipt(state, monkeypatch):
    connect, run, _ = state
    receipt = run(["new"])["receipt"]
    monkeypatch.setattr(batch.operator, "first_company_batch_confirm_enabled", lambda: False)
    preview = run(["new"])
    assert preview["rows"] == [{"account": "new", "action": "create"}]
    assert "receipt" not in preview
    with pytest.raises(batch.operator.OperatorError) as err:
        run(["new"], receipt)
    assert err.value.status == 403
    with connect() as c:
        for table in ("saas_crm_native_companies", "saas_tenant_audit_events",
                      "saas_beinsystems_operator_company_imports"):
            assert c.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
