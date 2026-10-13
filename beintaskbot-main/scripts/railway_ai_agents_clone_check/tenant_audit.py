#!/usr/bin/env python3
"""One-shot, read-only tenant inventory for the Railway check runner."""
from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg import sql


EXPECTED_DATABASE = "railway"
EXPECTED_ROLE = "ai_agents_backup_ro"
CURRENT_USER_ID = 1628569350  # legacy_runtime_config.ADMIN_CHAT_ID
CURRENT_STAGE = "startup"


def mark_stage(name: str) -> None:
    global CURRENT_STAGE
    CURRENT_STAGE = name


def safe_error_category(exc: Exception) -> str:
    """Classify connection errors without exposing driver text or connection data."""
    message = " ".join(str(part) for part in getattr(exc, "args", ())).casefold()
    categories = (
        (("password authentication failed", "authentication failed", "invalid password"), "authentication_failed"),
        (("could not translate host", "name or service not known", "temporary failure in name resolution"), "dns_resolution_failed"),
        (("timeout", "timed out"), "connection_timeout"),
        (("connection refused",), "connection_refused"),
        (("no pg_hba.conf entry", "not allowed to connect"), "connection_policy_rejected"),
        (("does not exist", "unknown database"), "database_unavailable"),
        (("ssl", "certificate verify failed"), "tls_error"),
    )
    return next((category for phrases, category in categories
                 if any(phrase in message for phrase in phrases)), "connection_error_unspecified")


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run() -> None:
    mark_stage("configuration")
    dsn = os.environ.get("SOURCE_READONLY_DATABASE_URL", "").strip()
    require(bool(dsn), "FAIL: read-only connection is not configured")

    # The URL itself is never printed. Refuse admin logins before any query.
    parsed = psycopg.conninfo.conninfo_to_dict(dsn)
    require(parsed.get("user") == EXPECTED_ROLE,
            "FAIL: configured source login is not the dedicated backup role")
    require(parsed.get("dbname") == EXPECTED_DATABASE,
            "FAIL: configured source database is not railway")

    mark_stage("connect_source")
    with psycopg.connect(
        dsn,
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
        application_name="ai-agents-tenant-audit-readonly",
    ) as conn:
        mark_stage("read_only_transaction_guard")
        conn.execute("SET TRANSACTION READ ONLY")
        with conn.cursor() as cur:
            mark_stage("verify_connection_identity")
            cur.execute("""SELECT current_user, current_database(),
                current_setting('transaction_read_only'),
                current_setting('default_transaction_read_only'),
                current_setting('server_version_num')::integer""")
            role, database, tx_readonly, default_readonly, version_num = cur.fetchone()
            require((role, database, tx_readonly, default_readonly) ==
                    (EXPECTED_ROLE, EXPECTED_DATABASE, "on", "on"),
                    "FAIL: source identity or read-only transaction guard did not match")

            mark_stage("verify_role_attributes")
            cur.execute("""SELECT rolsuper, rolcreatedb, rolcreaterole,
                rolreplication, rolbypassrls, rolcanlogin
                FROM pg_roles WHERE rolname=current_user""")
            attrs = cur.fetchone()
            require(attrs is not None and not any(attrs[:5]) and attrs[5],
                    "FAIL: backup login has elevated attributes")
            mark_stage("verify_role_memberships")
            cur.execute("""SELECT count(*) FROM pg_auth_members
                WHERE member=current_user::regrole::oid""")
            require(cur.fetchone()[0] == 0, "FAIL: backup login has role memberships")
            mark_stage("verify_database_create_privilege")
            cur.execute("""SELECT has_database_privilege(current_user,
                current_database(), 'CREATE')""")
            require(not cur.fetchone()[0], "FAIL: backup login can create database objects")

            mark_stage("read_tenants")
            cur.execute("""SELECT id::text, name, industry, status, created_at,
                owner_telegram_id, modules, onboarding
                FROM public.saas_tenants ORDER BY id""")
            tenant_rows = cur.fetchall()
            require(len(tenant_rows) == 4,
                    f"FAIL: expected the four previously identified tenants; found {len(tenant_rows)}")

            mark_stage("read_memberships")
            cur.execute("""SELECT tenant_id::text, role, active, count(*)
                FROM public.saas_tenant_members GROUP BY tenant_id, role, active
                ORDER BY tenant_id, role, active""")
            member_groups = cur.fetchall()
            mark_stage("read_integrations")
            cur.execute("""SELECT tenant_id::text, provider, status,
                    account_domain, metadata->>'credential_source',
                    metadata->>'account_id'
                FROM public.saas_tenant_integrations
                ORDER BY tenant_id, provider""")
            integrations = cur.fetchall()

            # Count all tenant-scoped records without selecting their content.
            mark_stage("enumerate_tenant_tables")
            cur.execute("""SELECT DISTINCT table_name FROM information_schema.columns
                WHERE table_schema='public' AND column_name='tenant_id'
                  AND table_name NOT IN ('saas_tenants') ORDER BY table_name""")
            tables = [row[0] for row in cur.fetchall()]
            counts: dict[str, dict[str, int]] = {row[0]: {} for row in tenant_rows}
            for table in tables:
                mark_stage("count_tenant_table")
                cur.execute(sql.SQL("SELECT tenant_id::text, count(*) FROM public.{} "
                                    "WHERE tenant_id IS NOT NULL GROUP BY tenant_id").format(
                                        sql.Identifier(table)))
                for tenant_id, total in cur.fetchall():
                    if tenant_id in counts and total:
                        counts[tenant_id][table] = total

            # The legacy /app tenant is carried by its signed browser cookie,
            # not stored as a user preference in this database. Report the
            # current user's confirmed active owner memberships without
            # exposing their Telegram ID or any other member identifiers.
            current_owner_tenants = sorted(
                row[0] for row in tenant_rows if row[5] == CURRENT_USER_ID
            )
            active_owner_memberships = {
                tenant_id: any(mid == tenant_id and role == "owner" and active and total == 1
                               for mid, role, active, total in member_groups)
                for tenant_id in counts
            }

            tenant_output = []
            for row in tenant_rows:
                tenant_id, name, industry, status, created_at, owner_id, modules, onboarding = row
                module_flags = modules if isinstance(modules, dict) else {}
                onboarding = onboarding if isinstance(onboarding, dict) else {}
                tenant_output.append({
                    "tenant_id": tenant_id,
                    "name": name,
                    "industry": industry,
                    "status": status,
                    "created_at": created_at.isoformat(),
                    "owner_is_current_user": owner_id == CURRENT_USER_ID,
                    "active_owner_membership": active_owner_memberships[tenant_id],
                    "module_flags": {str(k): bool(v) for k, v in module_flags.items()
                                     if isinstance(v, (bool, int))},
                    "onboarding_company_name_matches":
                        str(onboarding.get("company_name") or "").strip().casefold()
                        == str(name or "").strip().casefold(),
                    "completed_steps": [str(x) for x in onboarding.get("completed_steps", [])
                                        if isinstance(x, str)],
                    "active_members_by_role": {
                        role: total for mid, role, active, total in member_groups
                        if mid == tenant_id and active
                    },
                    "integrations": [
                        {"provider": provider, "status": status, "account_domain": domain,
                         "credential_source": source, "account_id": account_id}
                        for iid, provider, status, domain, source, account_id in integrations
                        if iid == tenant_id
                    ],
                    "nonzero_tenant_table_counts": counts[tenant_id],
                    "tenant_data_rows": sum(counts[tenant_id].values()),
                })

            # Verify the role has no write grant on any user object and no
            # readable rows in other databases. Never inspect row contents.
            mark_stage("verify_no_write_privileges")
            cur.execute("""SELECT count(*) FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                  AND c.relkind IN ('r','p','v','m','f') AND (
                    has_table_privilege(current_user,c.oid,'INSERT') OR
                    has_table_privilege(current_user,c.oid,'UPDATE') OR
                    has_table_privilege(current_user,c.oid,'DELETE') OR
                    has_table_privilege(current_user,c.oid,'TRUNCATE') OR
                    has_table_privilege(current_user,c.oid,'REFERENCES') OR
                    has_table_privilege(current_user,c.oid,'TRIGGER') OR
                    has_any_column_privilege(current_user,c.oid,'INSERT') OR
                    has_any_column_privilege(current_user,c.oid,'UPDATE') OR
                    has_any_column_privilege(current_user,c.oid,'REFERENCES'))""")
            writeable_objects = cur.fetchone()[0]
            require(writeable_objects == 0, "FAIL: backup role has a write privilege")

            mark_stage("enumerate_other_databases")
            cur.execute("""SELECT datname, has_database_privilege(current_user,oid,'CONNECT')
                FROM pg_database WHERE datallowconn AND NOT datistemplate
                  AND datname <> current_database() ORDER BY datname""")
            other_databases = cur.fetchall()
            cross_database_access = {}
            parsed_dsn = psycopg.conninfo.conninfo_to_dict(dsn)
            for other_database, can_connect in other_databases:
                accessible_objects = 0
                if can_connect:
                    try:
                        with psycopg.connect(
                            host=parsed_dsn.get("host"),
                            port=parsed_dsn.get("port") or 5432,
                            dbname=other_database,
                            user=role,
                            password=parsed_dsn.get("password"),
                            sslmode=parsed_dsn.get("sslmode") or "prefer",
                            connect_timeout=8,
                            options="-c default_transaction_read_only=on",
                            application_name="ai-agents-tenant-audit-readonly",
                        ) as other_conn:
                            mark_stage("verify_other_database_read_isolation")
                            other_conn.execute("SET TRANSACTION READ ONLY")
                            with other_conn.cursor() as other_cur:
                                other_cur.execute("""SELECT count(*) FROM pg_class c
                                    JOIN pg_namespace n ON n.oid=c.relnamespace
                                    WHERE n.nspname NOT LIKE 'pg_%'
                                      AND n.nspname <> 'information_schema'
                                      AND c.relkind IN ('r','p','v','m','f')
                                      AND has_schema_privilege(current_user,n.oid,'USAGE')
                                      AND (has_table_privilege(current_user,c.oid,'SELECT') OR
                                           has_any_column_privilege(current_user,c.oid,'SELECT'))""")
                                accessible_objects = other_cur.fetchone()[0]
                            other_conn.commit()
                    except Exception as exc:
                        # Emit only safe diagnostics: no DSN, SQL text, or server message.
                        state = getattr(exc, "sqlstate", None) or "none"
                        raise RuntimeError(
                            f"FAIL: cross-database read isolation could not be verified "
                            f"(stage={CURRENT_STAGE}, error={type(exc).__name__}, sqlstate={state})"
                        ) from None
                cross_database_access[other_database] = accessible_objects
                require(accessible_objects == 0,
                        "FAIL: backup login can read user objects in another database")
            mark_stage("read_database_size")
            cur.execute("SELECT pg_database_size(current_database())")
            db_bytes = cur.fetchone()[0]

        print(safe_json({
            "result": "PASS_READ_ONLY_TENANT_AUDIT",
            "database": database,
            "role": role,
            "postgres_major": version_num // 10000,
            "read_only_transaction": True,
            "writeable_objects": writeable_objects,
            "cross_database_readable_objects": cross_database_access,
            "database_size_bytes": db_bytes,
            "current_user_owner_tenant_ids": current_owner_tenants,
            "app_tenant_selection": "signed /app cookie; not persisted in PostgreSQL",
            "tenants": tenant_output,
        }))
        conn.commit()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Suppress driver errors, DSNs, and values that could contain secrets.
        if isinstance(exc, RuntimeError):
            message = str(exc)
        else:
            state = getattr(exc, "sqlstate", None) or "none"
            message = f"read-only query failed (stage={CURRENT_STAGE}, " \
                      f"error={type(exc).__name__}, sqlstate={state}, " \
                      f"category={safe_error_category(exc)}); details suppressed"
        print(f"FAIL: {message}", file=sys.stderr, flush=True)
        raise SystemExit(1)
