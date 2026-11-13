"""PostgreSQL regression for upgrading the Operator login rate-limit constraint."""
import os
import uuid

import pytest
import psycopg

from tenant_beinsystems_operator_schema import migrate_operator_identity_links


@pytest.mark.skipif(
    not os.environ.get("BEINTASKBOT_TEST_POSTGRES_DSN"),
    reason="Set BEINTASKBOT_TEST_POSTGRES_DSN to an isolated PostgreSQL database",
)
def test_existing_rate_limit_table_accepts_auth_login_after_migration():
    dsn = os.environ["BEINTASKBOT_TEST_POSTGRES_DSN"]
    schema = "operator_migration_" + uuid.uuid4().hex
    tenant_id = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            conn.execute(f'SET search_path TO "{schema}", public')
            conn.execute("CREATE TABLE saas_tenants (id UUID PRIMARY KEY)")
            conn.execute("""CREATE TABLE saas_tenant_members (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
              telegram_id BIGINT NOT NULL,
              PRIMARY KEY(tenant_id,telegram_id))""")
            conn.execute("""CREATE TABLE saas_beinsystems_operator_rate_limits (
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
              actor_telegram_id BIGINT NOT NULL,
              endpoint TEXT NOT NULL CHECK(endpoint IN
                ('me','accounts','account_info','profile_users','task_list')),
              bucket_start TIMESTAMPTZ NOT NULL,
              request_count INTEGER NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,actor_telegram_id,endpoint,bucket_start))""")
            conn.execute("INSERT INTO saas_tenants(id) VALUES(%s::uuid)", (tenant_id,))

            for _ in range(2):
                with conn.cursor() as cur:
                    migrate_operator_identity_links(cur)

            conn.execute("""INSERT INTO saas_beinsystems_operator_rate_limits
              (tenant_id,actor_telegram_id,endpoint,bucket_start,request_count)
              VALUES(%s::uuid,7,'auth_login',date_trunc('minute',now()),1)""", (tenant_id,))
            assert conn.execute("""SELECT endpoint FROM saas_beinsystems_operator_rate_limits
              WHERE tenant_id=%s::uuid""", (tenant_id,)).fetchone() == ("auth_login",)
        finally:
            conn.execute("SET search_path TO public")
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
