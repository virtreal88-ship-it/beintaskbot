"""Persistence for CRM snapshots that are not tied to a single provider.

The legacy Kommo tables remain authoritative for the existing webapp and
Talk/chat history.  This additive store is the common data plane used by new
providers, where identifiers may be UUIDs rather than numeric lead IDs.
"""
from contextlib import AbstractContextManager
import json
from typing import Callable, Protocol

from tenant_crm_identity import crm_identity, external_id
from tenant_crm_queries import public_crm_row


class Cursor(Protocol):
    def execute(self, sql: str, parameters: object = ...) -> object: ...
    def executemany(self, sql: str, rows: list) -> object: ...
    def fetchone(self) -> dict | None: ...
    def fetchall(self) -> list[dict]: ...


class Connection(Protocol):
    def cursor(self) -> AbstractContextManager[Cursor]: ...
    def commit(self) -> None: ...


def _rows(tenant_id: str, provider: str, kind: str, entities: list[dict], encode: Callable[[object], str]) -> list[tuple]:
    provider, kind, _ = crm_identity(provider, kind, "identity-check")
    rows: list[tuple] = []
    for item in entities:
        if not isinstance(item, dict):
            raise ValueError("CRM entity məlumatı düzgün deyil")
        identity = external_id(item.get("external_id") or item.get("id"))
        parent_id = str(item.get("parent_external_id") or "").strip()
        if parent_id:
            parent_id = external_id(parent_id)
        parent_type = str(item.get("parent_entity_type") or "").strip()
        if bool(parent_id) != bool(parent_type):
            raise ValueError("CRM parent identity tam göstərilməlidir")
        if parent_type:
            _, parent_type, _ = crm_identity(provider, parent_type, "parent-check")
        payload = item.get("data", item.get("raw", item))
        if not isinstance(payload, dict):
            raise ValueError("CRM entity məlumatı düzgün deyil")
        rows.append((tenant_id, provider, kind, identity, parent_type, parent_id,
                     item.get("source_updated_at") or None, encode(payload)))
    return rows


class ProviderSnapshotStore:
    def __init__(self, connect: Callable[[], AbstractContextManager[Connection]],
                 ensure_schema: Callable[[Connection], None], *,
                 serialize: Callable[[object], str] = json.dumps,
                 payload: Callable[[dict], dict] = public_crm_row):
        self.connect = connect
        self.ensure_schema = ensure_schema
        self.serialize = serialize
        self.payload = payload

    def upsert(self, *, tenant_id: str, provider: str, entity_type: str, entities: list[dict]) -> int:
        rows = _rows(tenant_id, provider, entity_type, entities, self.serialize)
        if not rows:
            return 0
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO saas_crm_provider_entities
                    (tenant_id, provider, entity_type, external_id, parent_entity_type,
                     parent_external_id, source_updated_at, data, synced_at)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::timestamptz, %s::jsonb, now())
                    ON CONFLICT (tenant_id, provider, entity_type, external_id) DO UPDATE SET
                        parent_entity_type=EXCLUDED.parent_entity_type,
                        parent_external_id=EXCLUDED.parent_external_id,
                        source_updated_at=EXCLUDED.source_updated_at,
                        data=EXCLUDED.data, synced_at=now()
                """, rows)
            conn.commit()
        return len(rows)

    def get(self, *, tenant_id: str, provider: str, entity_type: str, external_id: str) -> dict | None:
        provider, entity_type, external_id = crm_identity(provider, entity_type, external_id)
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""SELECT * FROM saas_crm_provider_entities
                    WHERE tenant_id=%s::uuid AND provider=%s AND entity_type=%s AND external_id=%s""",
                            (tenant_id, provider, entity_type, external_id))
                row = cur.fetchone()
        return self.payload(row) if row else None

    def list(self, *, tenant_id: str, provider: str, entity_type: str,
             parent_entity_type: str = "", parent_external_id: str = "",
             limit: int = 100, offset: int = 0) -> list[dict]:
        provider, entity_type, _ = crm_identity(provider, entity_type, "identity-check")
        parent_type = str(parent_entity_type or "").strip()
        parent_id = str(parent_external_id or "").strip()
        if bool(parent_type) != bool(parent_id):
            raise ValueError("CRM parent identity tam göstərilməlidir")
        values: list[object] = [tenant_id, provider, entity_type]
        where = "tenant_id=%s::uuid AND provider=%s AND entity_type=%s"
        if parent_type:
            _, parent_type, parent_id = crm_identity(provider, parent_type, parent_id)
            where += " AND parent_entity_type=%s AND parent_external_id=%s"
            values.extend([parent_type, parent_id])
        values.extend([max(1, min(int(limit or 100), 200)), max(0, int(offset or 0))])
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM saas_crm_provider_entities WHERE {where} "
                            "ORDER BY source_updated_at DESC NULLS LAST, synced_at DESC LIMIT %s OFFSET %s", values)
                rows = cur.fetchall()
        return [self.payload(row) for row in rows]
