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
import tenant_crm_native_companies as native
import tenant_operator_company_import_jobs as jobs
from tenant_crm_native_access import CRMAccessError


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
                   "generation": "g", "received_at": received_at, "token": "mock",
                   "base_url": "https://operator-api.example/v1"}
    cipher = Fernet(Fernet.generate_key())
    envelope = cipher.encrypt(json.dumps({"tenant_id": tenant, "owner_telegram_id": actor,
        "provider": batch.operator.PROVIDER, "purpose": "pending_auth_verification",
        "token": "mock"}).encode())

    def connect():
        return psycopg.connect(dsn, row_factory=dict_row, options=f"-csearch_path={schema}")

    profile = {"role": "owner", "active": True, "tenant_id": tenant, "telegram_id": actor}
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", tenant)
    monkeypatch.setenv("BEINSYSTEMS_DIALOG_OPERATOR_CHOOSER_ENABLED", "true")
    monkeypatch.setattr(batch.operator, "first_company_batch_confirm_enabled", lambda: True)
    monkeypatch.setattr(native, "_connect", connect)
    monkeypatch.setattr(native, "_ensure_schema", lambda *_: None)
    monkeypatch.setattr(native, "current_access", lambda *_: profile)
    monkeypatch.setattr(native, "authorize_create", lambda *_: None)
    monkeypatch.setattr(batch.operator, "_connect", connect)
    monkeypatch.setattr(batch.operator, "_ensure_schema", lambda *_: None)
    monkeypatch.setattr(batch.operator, "_current_profile", lambda *_: profile)
    monkeypatch.setattr(batch.operator, "_operator_row", lambda *_, **__: None)
    monkeypatch.setattr(batch.operator, "_fernet", lambda: cipher)
    monkeypatch.setattr(batch, "current_access", lambda *_: profile)
    monkeypatch.setattr(batch, "authorize_create", lambda *_: None)

    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            with connect() as c:
                c.execute("CREATE TABLE saas_tenants (id uuid PRIMARY KEY)")
                c.execute("INSERT INTO saas_tenants (id) VALUES (%s)", (tenant,))
                with c.cursor() as cur:
                    jobs.migrate_jobs(cur)
                c.execute("""CREATE TABLE saas_tenant_audit_events (
                  id uuid PRIMARY KEY, tenant_id uuid,actor_telegram_id bigint,action text,
                  entity_type text,entity_id text,payload jsonb)""")
                c.execute("""CREATE TABLE saas_crm_native_companies (
                  id uuid PRIMARY KEY,tenant_id uuid,name text,phone text,email text,
                  description text,responsible_id bigint,access_responsible_id bigint,archived_at timestamptz,
                  created_at timestamptz DEFAULT now())""")
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

            monkeypatch.setattr(batch, "_selected_source_credentials",
                                lambda p: (tenant,actor,"accounts_only",credentials))
            scope = batch._first_batch_digest(
                ["catalogue_background","accounts_only",batch._first_batch_scope(tenant, actor, credentials)])

            def run(accounts, receipt=None, *, source_only=False, catalogue=False, background=False):
                rows = [{"account": name} for name in accounts]
                if source_only:
                    return batch._preview_operator_accounts_only(profile, tenant, actor, rows, credentials)
                return batch._apply_company_import(profile, tenant, actor, rows, credentials,
                    first_batch=True, preview_receipt=receipt, preview_scope=scope if background else "scope",
                    catalogue_source="accounts_only" if catalogue else None, background=background)

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


def test_catalogue_can_resume_after_a_full_batch_and_preserves_local_changes(state):
    connect, run, tenant = state
    names = [f"acct-{i:03}" for i in range(104)]
    initial = run(names, catalogue=True)
    assert initial["received"] == 104
    assert initial["already"] == 0
    assert len(initial["rows"]) == 100
    first = run(names, initial["receipt"], catalogue=True)
    assert first["created"] == 100 and first["remaining"] == 4
    with connect() as c:
        c.execute("UPDATE saas_crm_native_companies SET archived_at=now() WHERE tenant_id=%s AND name='acct-000'", (tenant,))
        c.execute("UPDATE saas_crm_native_companies SET name='Edited locally' WHERE tenant_id=%s AND name='acct-001'", (tenant,))
    resumed = run(names, catalogue=True)
    assert resumed["already"] == 99
    assert resumed["conflicts"] == [{"account": "acct-000", "reason": "archived_company"}]
    assert len(resumed["rows"]) == 4
    assert run(names, resumed["receipt"], catalogue=True)["created"] == 4
    done = run(names, catalogue=True)
    assert done["already"] == 103 and done["remaining"] == 0 and "receipt" not in done
    with connect() as c:
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE tenant_id=%s AND name='Edited locally'", (tenant,)).fetchone()["n"] == 1
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE tenant_id=%s AND archived_at IS NOT NULL", (tenant,)).fetchone()["n"] == 1


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
    original, conflict, stable_company = (str(uuid.uuid4()) for _ in range(3))
    with connect() as c:
        for company, name in ((original, "Existing CRM company"), (conflict, "Other CRM company"),
                              (stable_company, "Stable CRM company")):
            c.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s,%s,%s)",
                      (company, tenant, name))
        for account, company in (("stable", stable_company), ("CASE-ALT", original), ("clash", original)):
            c.execute("""INSERT INTO saas_beinsystems_operator_company_imports
              (tenant_id,external_account,company_id,last_seen_connection_instance_id,last_seen_generation)
              VALUES(%s,%s,%s,%s,'old')""", (tenant, account, company, old_instance))
        for owner, account, company, revoked in (
                (tenant, "old-link", original, False),
                (tenant, "UPPER-LINK", original, False),
                (tenant, "stable", stable_company, False),
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
            {"name": "Stable CRM company"}, {"name": "foreign"}, {"name": "revoked"}]

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
    original = batch.lock_name_writes

    def check(*args, **kwargs):
        early_check.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(batch, "lock_name_writes", check)
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
    monkeypatch.setattr(batch.operator, "first_company_batch_preview_enabled", lambda: True)
    preview = run(["new"], source_only=True)
    assert preview["rows"] == [{"account": "new"}]
    assert "receipt" not in preview
    with pytest.raises(batch.operator.OperatorError):
        run(["new"])
    with pytest.raises(batch.operator.OperatorError) as err:
        run(["new"], receipt)
    assert err.value.status == 403
    with connect() as c:
        for table in ("saas_crm_native_companies", "saas_tenant_audit_events",
                      "saas_beinsystems_operator_company_imports"):
            assert c.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_source_preview_has_no_crm_table_dependency_and_rechecks_pending_rotation(state, monkeypatch):
    connect, run, _ = state
    monkeypatch.setattr(batch.operator, "first_company_batch_preview_enabled", lambda: True)
    monkeypatch.setattr(batch.operator, "first_company_batch_confirm_enabled", lambda: False)
    with connect() as c:
        c.execute("DROP TABLE saas_crm_native_companies")
        c.execute("DROP TABLE saas_beinsystems_operator_company_imports")
        c.execute("DROP TABLE saas_beinsystems_operator_links")
    assert run(["one", "two"], source_only=True) == {
        "checked": 2, "partial": True, "limit": 100, "mode": "accounts_only",
        "rows": [{"account": "one"}, {"account": "two"}],
    }
    assert run([], source_only=True)["rows"] == []
    with connect() as c:
        assert c.execute("SELECT count(*) AS n FROM saas_tenant_audit_events").fetchone()["n"] == 0
        c.execute("UPDATE saas_beinsystems_operator_pending_auth_v2 SET received_at=received_at+interval '1 second'")
    with pytest.raises(batch.operator.OperatorError) as err:
        run(["one"], source_only=True)
    assert err.value.status == 409


@pytest.mark.parametrize("writer", ["manual", "ai"])
@pytest.mark.parametrize("first", ["batch", "ordinary"])
def test_guarded_first_batch_and_real_native_writer_serialize_both_orders(state, monkeypatch, writer, first):
    connect, run, tenant = state
    preview = run(["same-name"])
    assert preview["mode"] == "company_plan"
    entered, release, waiting = Event(), Event(), Event()

    def write():
        if writer == "manual":
            return native.create_company(tenant, "SAME-NAME", actor_id=7)
        return native.find_or_create_company(tenant, "SAME-NAME")

    def confirm():
        return run(["same-name"], preview["receipt"])

    if first == "batch":
        orig = batch._put_mapping

        def hold(*args):
            entered.set()
            assert release.wait(15)
            return orig(*args)

        monkeypatch.setattr(batch, "_put_mapping", hold)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ahead = pool.submit(confirm)
            try:
                assert entered.wait(10)
                behind = pool.submit(write)
                with pytest.raises(CRMAccessError) as busy:
                    behind.result(10)
                assert busy.value.code == "company_name_busy"
            finally:
                release.set()
            assert ahead.result(15)["created"] == 1
    else:
        orig_native = native.require_unreserved_name
        orig_batch = batch.lock_name_writes

        def hold(*args):
            orig_native(*args)
            entered.set()
            assert release.wait(15)

        def observe(*args, **kwargs):
            waiting.set()
            return orig_batch(*args, **kwargs)

        monkeypatch.setattr(native, "require_unreserved_name", hold)
        monkeypatch.setattr(batch, "lock_name_writes", observe)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ahead = pool.submit(write)
            try:
                assert entered.wait(10)
                behind = pool.submit(confirm)
                assert waiting.wait(10)
            finally:
                release.set()
            assert ahead.result(15)["name"] == "SAME-NAME"
            with pytest.raises(batch.operator.OperatorError) as changed:
                behind.result(15)
            assert changed.value.status == 409
        after = run(["same-name"])
        assert after["rows"] == [{"account": "same-name", "action": "manual_review"}]
        assert "receipt" not in after
    with connect() as c:
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies").fetchone()["n"] == 1
        expected_mappings = int(first == "batch")
        assert c.execute("SELECT count(*) AS n FROM saas_beinsystems_operator_company_imports").fetchone()["n"] == expected_mappings
        assert c.execute("""SELECT count(*) AS n FROM saas_tenant_audit_events
          WHERE action='operator_first_company_batch'""").fetchone()["n"] == expected_mappings


def test_same_target_case_alias_and_reverse_manual_link_block_exact_reuse(state):
    connect, run, tenant = state
    receipt = run(["real", "other"])["receipt"]
    assert run(["real", "other"], receipt)["created"] == 2
    with connect() as c:
        c.execute("""INSERT INTO saas_beinsystems_operator_links
          SELECT tenant_id,'company','account','','REAL',company_id,'old',
            last_seen_connection_instance_id,NULL
          FROM saas_beinsystems_operator_company_imports WHERE external_account='real'""")
        c.execute("""INSERT INTO saas_beinsystems_operator_links
          SELECT tenant_id,'company','account','','different',company_id,'old',
            last_seen_connection_instance_id,NULL
          FROM saas_beinsystems_operator_company_imports WHERE external_account='other'""")
    after = run(["real", "other"])
    assert after["rows"] == [{"account": "real", "action": "manual_review"},
                             {"account": "other", "action": "manual_review"}]
    assert "receipt" not in after


def test_background_confirmation_imports_more_than_one_batch_and_is_replay_safe(state):
    connect, run, tenant = state
    names = [f"background-{i:03}" for i in range(104)]
    plan = run(names, catalogue=True, background=True)
    assert plan["received"] == plan["remaining"] == 104
    job = run(names, plan["receipt"], catalogue=True, background=True)
    assert job["state"] == "queued" and job["total"] == 104 and job["created"] == 0
    with connect() as c:
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies").fetchone()["n"] == 0
    # Two workers racing may take one or two independent slices, but cannot
    # advance the same cursor twice. Changes survive new DB sessions per tick.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: jobs.run_once(), range(2)))
    replay = run(names, plan["receipt"], catalogue=True, background=True)
    assert replay["id"] == job["id"] and replay["processed"] in {50,100}
    for _ in range(3):
        jobs.run_once()
    with connect() as c:
        row = c.execute("SELECT * FROM saas_operator_company_import_jobs").fetchone()
        assert row["state"] == "completed"
        assert row["processed"] == row["created"] == 104
        assert row["accounts"] == []
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies").fetchone()["n"] == 104
        assert c.execute("SELECT count(*) AS n FROM saas_beinsystems_operator_company_imports").fetchone()["n"] == 104
    assert "receipt" not in run(names, catalogue=True, background=True)


def test_background_import_rechecks_access_and_preserves_committed_slices(state):
    connect, run, tenant = state
    names = [f"background-{i:03}" for i in range(51)]
    plan = run(names, catalogue=True, background=True)
    run(names, plan["receipt"], catalogue=True, background=True)
    jobs.run_once()
    with connect() as c:
        c.execute("UPDATE saas_beinsystems_operator_accounts_capabilities SET expires_at=now() WHERE tenant_id=%s", (tenant,))
    jobs.run_once()
    with connect() as c:
        row = c.execute("SELECT * FROM saas_operator_company_import_jobs").fetchone()
        assert row["state"] == "failed" and row["error_code"] == "access_or_plan_changed"
        assert row["processed"] == row["created"] == 50 and row["accounts"] == []
        assert c.execute("SELECT count(*) AS n FROM saas_crm_native_companies").fetchone()["n"] == 50


def test_background_does_not_adopt_same_name_or_create_outside_confirmed_set(state):
    connect, run, tenant = state
    names = [f"background-{i:03}" for i in range(51)]
    plan = run(names, catalogue=True, background=True)
    run(names, plan["receipt"], catalogue=True, background=True)
    with connect() as c:
        c.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s,%s,%s)",
                  (uuid.uuid4(),tenant,names[50]))
    jobs.run_once()
    jobs.run_once()
    with connect() as c:
        row = c.execute("SELECT * FROM saas_operator_company_import_jobs").fetchone()
        assert row["state"] == "completed" and row["processed"] == 51
        assert row["created"] == 50 and row["conflicts"] == 1
        assert not c.execute("SELECT 1 FROM saas_beinsystems_operator_company_imports WHERE external_account=%s",
                             (names[50],)).fetchone()
