"""Disposable PostgreSQL checks for the provider-neutral CRM data plane.

The script intentionally refuses every database other than a loopback database
whose name starts with ``crm_provider_test_``.  It never reads the production
``DATABASE_URL`` directly.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from urllib.parse import urlsplit
import uuid

WORKERS = 8
ROWS_PER_WORKER = 40


def test_url() -> str:
    value = os.environ.get("CRM_PROVIDER_TEST_DATABASE_URL", "").strip()
    parsed = urlsplit(value)
    database = parsed.path.lstrip("/")
    if (parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or not database.startswith("crm_provider_test_")):
        raise SystemExit(
            "Set CRM_PROVIDER_TEST_DATABASE_URL to a loopback disposable "
            "database named crm_provider_test_*; refusing to run."
        )
    return value


def connection_factory(database_url: str):
    import psycopg
    from psycopg.rows import dict_row
    return lambda: psycopg.connect(database_url, row_factory=dict_row)


def count_entities(database_url: str, tenant_id: uuid.UUID) -> int:
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS total FROM saas_crm_provider_entities WHERE tenant_id=%s", (tenant_id,))
            return int(cur.fetchone()["total"])


def main() -> None:
    database_url = test_url()
    os.environ["DATABASE_URL"] = database_url
    from cryptography.fernet import Fernet
    os.environ.setdefault("TENANT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from tenant_platform_schema import _ensure_schema
    import tenant_platform_schema
    from tenant_crm_provider_store import ProviderSnapshotStore

    tenant_platform_schema._schema_ready = False
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    connect = connection_factory(database_url)
    store = ProviderSnapshotStore(connect, _ensure_schema)
    try:
        # Replaying the migration is part of every real deployment recovery.
        with connect() as conn:
            _ensure_schema(conn)
        tenant_platform_schema._schema_ready = False
        with connect() as conn:
            _ensure_schema(conn)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO saas_tenants(id,slug,name,owner_telegram_id,status) VALUES
                    (%s,%s,'Provider Test A',101,'active'),(%s,%s,'Provider Test B',202,'active')""",
                            (tenant_a, "crm-provider-a-" + tenant_a.hex[:8],
                             tenant_b, "crm-provider-b-" + tenant_b.hex[:8]))
            conn.commit()
        print("PASS: migration replay on disposable PostgreSQL")

        # Same external UUID must be isolated by tenant, not treated as shared.
        shared = "550e8400-e29b-41d4-a716-446655440000"
        for tenant, name in ((tenant_a, "A"), (tenant_b, "B")):
            assert store.upsert(tenant_id=str(tenant), provider="beinsystems", entity_type="deals",
                                entities=[{"id": shared, "data": {"name": name}}]) == 1
        assert store.get(tenant_id=str(tenant_a), provider="beinsystems", entity_type="deals",
                         external_id=shared)["data"]["name"] == "A"
        assert store.get(tenant_id=str(tenant_b), provider="beinsystems", entity_type="deals",
                         external_id=shared)["data"]["name"] == "B"
        print("PASS: two-company identity isolation")

        # A connection closed before commit represents a stopped worker. Its
        # uncommitted row must disappear, and the next worker must recover.
        interrupted_id = "interrupted-write"
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO saas_crm_provider_entities
                (tenant_id,provider,entity_type,external_id,data)
                VALUES (%s,'beinsystems','deals',%s,'{}'::jsonb)""", (tenant_a, interrupted_id))
        conn.close()
        assert store.get(tenant_id=str(tenant_a), provider="beinsystems", entity_type="deals",
                         external_id=interrupted_id) is None
        store.upsert(tenant_id=str(tenant_a), provider="beinsystems", entity_type="deals",
                     entities=[{"id": interrupted_id, "data": {"recovered": True}}])
        assert store.get(tenant_id=str(tenant_a), provider="beinsystems", entity_type="deals",
                         external_id=interrupted_id)["data"]["recovered"] is True
        print("PASS: interrupted transaction recovery")

        # Each worker has its own real database connection.  Batches are
        # disjoint to measure concurrent writes without relying on ordering.
        def write_batch(worker: int) -> tuple[uuid.UUID, int]:
            tenant = tenant_a if worker % 2 == 0 else tenant_b
            rows = [{"id": f"load-{worker}-{index}", "data": {"worker": worker, "index": index}}
                    for index in range(ROWS_PER_WORKER)]
            saved = ProviderSnapshotStore(connect, _ensure_schema).upsert(
                tenant_id=str(tenant), provider="beinsystems", entity_type="tasks", entities=rows)
            return tenant, saved

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(write_batch, range(WORKERS)))
        assert all(saved == ROWS_PER_WORKER for _, saved in results)
        expected_a = 2 + (WORKERS // 2) * ROWS_PER_WORKER
        expected_b = 1 + (WORKERS // 2) * ROWS_PER_WORKER
        assert count_entities(database_url, tenant_a) == expected_a
        assert count_entities(database_url, tenant_b) == expected_b
        print(f"PASS: concurrent load ({WORKERS * ROWS_PER_WORKER} provider snapshots)")
    finally:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM saas_tenants WHERE id IN (%s,%s)", (tenant_a, tenant_b))
            conn.commit()


if __name__ == "__main__":
    main()
