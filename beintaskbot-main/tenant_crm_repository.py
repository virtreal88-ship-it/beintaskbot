"""CRM snapshot persistence; dependencies injected, no platform imports.

Public platform wrappers retain their contracts. This repository does not
own schema migrations, authorization, provider requests or legacy data.
"""
from contextlib import AbstractContextManager
import json
from typing import Callable, Protocol
import uuid
from tenant_crm_queries import deal_filters, task_filters, public_crm_row
from tenant_crm_snapshots import deal_rows, task_rows
from tenant_message_snapshots import message_rows


class Cursor(Protocol):
    def execute(self, sql: str, parameters: object) -> object: ...
    def executemany(self, sql: str, rows: list) -> object: ...
    def fetchone(self) -> dict | None: ...
    def fetchall(self) -> list[dict]: ...


class Connection(Protocol):
    def cursor(self) -> AbstractContextManager[Cursor]: ...
    def commit(self) -> None: ...


class CRMRepository:
    def __init__(self, connect: Callable[[], AbstractContextManager[Connection]],
                 ensure_schema: Callable[[Connection], None], *,
                 serialize: Callable[[object], str] = json.dumps,
                 payload: Callable[[dict], dict] = public_crm_row,
                 id_factory: Callable[[], uuid.UUID] = uuid.uuid4):
        self.connect = connect
        self.ensure_schema = ensure_schema
        self.serialize = serialize
        self.payload = payload
        self.id_factory = id_factory

    def _write(self, sql: str, rows: list) -> int:
        if not rows:
            return 0
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                # psycopg pipelines executemany: one batch, one transaction.
                cur.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def upsert_deals(self, *, tenant_id: str, deals: list[dict]) -> int:
        rows = deal_rows(tenant_id, deals, self.serialize)
        return self._write("""
                    INSERT INTO saas_crm_deals
                    (tenant_id, kommo_lead_id, pipeline_id, status_id, stage_name, name, contact_name, phone,
                     channel, last_message, last_message_at, source_updated_at, raw, synced_at)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::timestamptz, %s::timestamptz, %s::jsonb, now())
                    ON CONFLICT (tenant_id, kommo_lead_id) DO UPDATE SET
                        pipeline_id = EXCLUDED.pipeline_id, status_id = EXCLUDED.status_id,
                        stage_name = EXCLUDED.stage_name, name = EXCLUDED.name,
                        contact_name = EXCLUDED.contact_name, phone = EXCLUDED.phone,
                        channel = EXCLUDED.channel, last_message = EXCLUDED.last_message,
                        last_message_at = EXCLUDED.last_message_at,
                        source_updated_at = EXCLUDED.source_updated_at, raw = EXCLUDED.raw, synced_at = now()
                """, rows)

    def list_deals(self, *, tenant_id: str, search: str = '', pipeline_ids: list[int] | None = None,
                   status_ids: list[int] | None = None, limit: int = 100, offset: int = 0,
                   scope: list[dict] | None = None) -> tuple[list[dict], int]:
        safe_limit = max(1, min(int(limit or 100), 200))
        safe_offset = max(0, int(offset or 0))
        where, values = deal_filters(tenant_id=tenant_id, search=search, pipeline_ids=pipeline_ids,
                                    status_ids=status_ids, scope=scope)
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(f'SELECT count(*) AS total FROM saas_crm_deals WHERE {where}', values)
                total = int((cur.fetchone() or {}).get('total') or 0)
                cur.execute(f'SELECT * FROM saas_crm_deals WHERE {where} '
                            'ORDER BY last_message_at DESC NULLS LAST, source_updated_at DESC NULLS LAST, synced_at DESC '
                            'LIMIT %s OFFSET %s', [*values, safe_limit, safe_offset])
                rows = cur.fetchall()
        return [self.payload(row) for row in rows], total

    def _one(self, sql: str, parameters: tuple) -> dict | None:
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(sql, parameters)
                row = cur.fetchone()
        return self.payload(row) if row else None

    def get_deal(self, *, tenant_id: str, kommo_lead_id: int) -> dict | None:
        return self._one('SELECT * FROM saas_crm_deals WHERE tenant_id = %s::uuid AND kommo_lead_id = %s AND deleted_at IS NULL',
                         (tenant_id, int(kommo_lead_id)))

    def get_task(self, *, tenant_id: str, kommo_task_id: int) -> dict | None:
        return self._one('SELECT * FROM saas_crm_tasks WHERE tenant_id=%s::uuid AND kommo_task_id=%s '
                         'AND NOT EXISTS (SELECT 1 FROM saas_crm_deals d WHERE d.tenant_id=saas_crm_tasks.tenant_id '
                         'AND d.kommo_lead_id=saas_crm_tasks.kommo_lead_id AND d.deleted_at IS NOT NULL)',
                         (tenant_id, int(kommo_task_id)))

    def upsert_tasks(self, *, tenant_id: str, tasks: list[dict]) -> int:
        rows = task_rows(tenant_id, tasks, self.serialize)
        return self._write("""
                    INSERT INTO saas_crm_tasks
                    (tenant_id, kommo_task_id, kommo_lead_id, text, due_at, responsible_id, completed, raw, synced_at)
                    VALUES (%s::uuid, %s, %s, %s, %s::timestamptz, %s, %s, %s::jsonb, now())
                    ON CONFLICT (tenant_id, kommo_task_id) DO UPDATE SET
                        kommo_lead_id = EXCLUDED.kommo_lead_id, text = EXCLUDED.text, due_at = EXCLUDED.due_at,
                        responsible_id = EXCLUDED.responsible_id, completed = EXCLUDED.completed,
                        raw = EXCLUDED.raw, synced_at = now()
                """, rows)

    def list_tasks(self, *, tenant_id: str, responsible_id: int | None = None,
                   scope: dict | None = None, limit: int = 100) -> list[dict]:
        if responsible_id == 0:
            return []
        where, values = task_filters(tenant_id=tenant_id, responsible_id=responsible_id, scope=scope)
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM saas_crm_tasks WHERE {where} '
                            'ORDER BY completed ASC, due_at ASC NULLS LAST, synced_at DESC LIMIT %s',
                            [*values, max(1, min(int(limit or 100), 200))])
                rows = cur.fetchall()
        return [self.payload(row) for row in rows]

    def upsert_messages(self, *, tenant_id: str, kommo_lead_id: int, messages: list[dict]) -> int:
        rows = message_rows(tenant_id, kommo_lead_id, messages, self.serialize, self.id_factory)
        return self._write("""
                    INSERT INTO saas_crm_messages
                    (id, tenant_id, kommo_lead_id, external_id, direction, channel, author_name,
                     body, message_type, media_url, happened_at, raw)
                    VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s::jsonb)
                    ON CONFLICT (tenant_id, external_id) DO UPDATE SET
                        direction = EXCLUDED.direction, channel = EXCLUDED.channel,
                        author_name = EXCLUDED.author_name, body = EXCLUDED.body,
                        message_type = EXCLUDED.message_type, media_url = EXCLUDED.media_url,
                        happened_at = EXCLUDED.happened_at, raw = EXCLUDED.raw
                """, rows)

    def list_messages(self, *, tenant_id: str, kommo_lead_id: int, limit: int = 120) -> list[dict]:
        with self.connect() as conn:
            self.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("""
                SELECT * FROM (
                    SELECT external_id, direction, channel, author_name, body, message_type, media_url, happened_at, created_at
                    FROM saas_crm_messages
                    WHERE tenant_id = %s::uuid AND kommo_lead_id = %s
                    ORDER BY COALESCE(happened_at,created_at) DESC, external_id DESC
                    LIMIT %s
                ) recent
                ORDER BY COALESCE(happened_at,created_at) ASC, external_id ASC
            """, (tenant_id, int(kommo_lead_id), max(1, min(int(limit or 120), 300))))
                rows = cur.fetchall()
        return [self.payload(row) for row in rows]
