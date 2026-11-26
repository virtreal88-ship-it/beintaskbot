import os
import uuid
from contextlib import nullcontext

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

import tenant_beinsystems_operator as op
import tenant_operator_widget_sender as sender
import tenant_operator_widget_transport as wire
from tenant_beinsystems_operator_schema import migrate_operator_identity_links


def test_shared_widget_binding_is_scoped_consumed_and_revocable(monkeypatch):
    dsn = os.getenv("BEINTASKBOT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set BEINTASKBOT_TEST_POSTGRES_DSN to disposable local PostgreSQL")
    assert psycopg.conninfo.conninfo_to_dict(dsn)["host"] in {"localhost", "127.0.0.1", "::1"}
    tenant, instance, link = [str(uuid.uuid4()) for _ in range(3)]
    schema = "widget_" + uuid.uuid4().hex
    creds = {"connection_instance_id": instance, "generation": "one", "token": "main-read"}
    profile = {"tenant_id": tenant, "role": "owner", "telegram_id": 11}
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", tenant)
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.execute(f'CREATE SCHEMA "{schema}"')
        db.execute(f'SET search_path TO "{schema}", public')
        try:
            db.execute("CREATE TABLE saas_tenants(id uuid PRIMARY KEY)")
            db.execute("CREATE TABLE saas_tenant_members(tenant_id uuid, telegram_id bigint, active boolean, PRIMARY KEY(tenant_id,telegram_id))")
            db.execute("CREATE TABLE saas_tenant_audit_events(id uuid,tenant_id uuid,actor_telegram_id bigint,action text,entity_type text,entity_id text,payload jsonb)")
            migrate_operator_identity_links(db.cursor())
            db.execute("INSERT INTO saas_tenants VALUES(%s)", (tenant,))
            db.execute("INSERT INTO saas_tenant_members VALUES (%s,11,true),(%s,22,true)", (tenant, tenant))
            db.execute("""INSERT INTO saas_beinsystems_operator_employee_links
                (id,tenant_id,connection_instance_id,connection_generation,member_telegram_id,
                 operator_account,operator_login,verification_basis,evidence_summary,verified_by)
                VALUES(%s,%s,%s,'one',22,'company','staff','manual','verified in test',11)""",
                (link, tenant, instance))
            at = db.execute("""INSERT INTO saas_beinsystems_operator_pending_auth_v2
                 (tenant_id,owner_telegram_id,token_ciphertext) VALUES(%s,11,%s) RETURNING received_at""",
                 (tenant,b"synthetic")).fetchone()["received_at"]
            monkeypatch.setattr(op, "_connect", lambda: nullcontext(db))
            key = Fernet(Fernet.generate_key())
            monkeypatch.setattr(op, "_fernet", lambda: key)
            monkeypatch.setattr(op, "_current_profile", lambda *args: profile)
            monkeypatch.setattr(op, "_operator_row", lambda *a, **k: {"status":"connected","metadata":creds})
            monkeypatch.setattr(op, "_pending_auth_candidate", lambda p: {
                "token":"staff-secret", "base_url":"synthetic", "received_at":at})
            identity = {"identifier":"staff@company","role":"night_bot"}
            monkeypatch.setattr(op, "_request_operator", lambda *a, **k: {"status":"success","data":identity})
            monkeypatch.setattr(wire, "probe_realtime_origin", lambda c: "connected")
            with pytest.raises(op.OperatorError):
                sender.bind_pending(profile, "staff@company")
            identity["role"] = "operator"
            sender.bind_pending(profile, "staff@company")
            assert db.execute("SELECT activated_at FROM saas_beinsystems_operator_pending_auth_v2").fetchone()["activated_at"]
            assert creds["token"] == "main-read"
            cur = db.cursor()
            assert sender.widget_sender(cur,tenant,creds)["token"] == "staff-secret"
            for t,scope in [(str(uuid.uuid4()),creds),(tenant,{**creds,"generation":"two"}),
                            (tenant,{**creds,"connection_instance_id":str(uuid.uuid4())})]:
                assert sender.widget_sender(cur,t,scope) is None
            cur.execute("UPDATE saas_beinsystems_operator_employee_links SET member_telegram_id=11")
            with pytest.raises(op.OperatorError):
                sender.widget_sender(cur,tenant,creds)
            cur.execute("UPDATE saas_beinsystems_operator_employee_links SET revoked_at=now()")
            assert sender.widget_sender(cur,tenant,creds) is None
        finally:
            db.rollback()
            db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
