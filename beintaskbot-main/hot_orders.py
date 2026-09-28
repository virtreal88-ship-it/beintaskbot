"""PostgreSQL-backed hot-order queue with an atomic first-claim operation."""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from decimal import Decimal
from threading import Lock

import psycopg
from psycopg.rows import dict_row


class HotOrderError(RuntimeError):
    pass


_schema_ready = False
_schema_lock = Lock()


def _database_url() -> str:
    url = str(os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise HotOrderError("PostgreSQL qoşulmayıb. Railway-də Postgres xidməti əlavə edin.")
    return url


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _ensure_schema(conn) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hot_orders (
                    id UUID PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('open', 'claimed', 'submitted', 'settled', 'cancelled')),
                    client_name TEXT NOT NULL,
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL,
                    skill TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    deadline_at TIMESTAMPTZ NULL,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    claimed_by BIGINT NULL,
                    claimed_at TIMESTAMPTZ NULL,
                    released_at TIMESTAMPTZ NULL,
                    report TEXT NOT NULL DEFAULT '',
                    completed_at TIMESTAMPTZ NULL,
                    payout_amount NUMERIC(12,2) NULL,
                    payout_set_by BIGINT NULL,
                    settled_at TIMESTAMPTZ NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS hot_orders_open_idx ON hot_orders(status, skill, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS hot_orders_worker_idx ON hot_orders(claimed_by, status, created_at DESC)")
        conn.commit()
        _schema_ready = True


def _row(row: dict, *, reveal_payout: bool = False) -> dict:
    result = dict(row)
    result["id"] = str(result["id"])
    for key in ("created_at", "claimed_at", "released_at", "completed_at", "settled_at", "deadline_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if result.get("payout_amount") is not None:
        result["payout_amount"] = float(result["payout_amount"])
    if not reveal_payout:
        result.pop("payout_amount", None)
        result.pop("payout_set_by", None)
    return result


def create_hot_order(*, client_name: str, phone: str, address: str, description: str,
                     skill: str, priority: str, deadline_at: datetime | None, created_by: int) -> dict:
    order_id = uuid.uuid4()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO hot_orders (id, status, client_name, phone, address, description, skill, priority, deadline_at, created_by)
                VALUES (%s, 'open', %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (order_id, client_name, phone, address, description, skill, priority, deadline_at, created_by))
            row = cur.fetchone()
        conn.commit()
    return _row(row, reveal_payout=True)


def update_hot_order(*, order_id: str, editor_id: int, is_admin: bool, client_name: str,
                     phone: str, address: str, description: str, skill: str,
                     priority: str, deadline_at: datetime | None) -> dict | None:
    """Update an open order. Its creator keeps this right after creation."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE hot_orders
                SET client_name = %s, phone = %s, address = %s, description = %s,
                    skill = %s, priority = %s, deadline_at = %s
                WHERE id = %s::uuid
                  AND status = 'open'
                  AND (%s OR created_by = %s)
                RETURNING *
            """, (client_name, phone, address, description, skill, priority, deadline_at,
                  order_id, is_admin, editor_id))
            row = cur.fetchone()
        conn.commit()
    return _row(row, reveal_payout=is_admin) if row else None


def cancel_hot_order(*, order_id: str, editor_id: int, is_admin: bool) -> dict | None:
    """Cancel only an order that has not yet been claimed by a worker."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE hot_orders
                SET status = 'cancelled'
                WHERE id = %s::uuid
                  AND status = 'open'
                  AND (%s OR created_by = %s)
                RETURNING *
            """, (order_id, is_admin, editor_id))
            row = cur.fetchone()
        conn.commit()
    return _row(row, reveal_payout=is_admin) if row else None


def list_hot_orders(*, worker_id: int, skills: list[str], is_admin: bool) -> list[dict]:
    allowed_skills = [str(item).strip().casefold() for item in skills if str(item).strip()]
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if is_admin:
                cur.execute("SELECT * FROM hot_orders WHERE status <> 'cancelled' ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'claimed' THEN 1 WHEN 'submitted' THEN 2 ELSE 3 END, created_at DESC")
            else:
                cur.execute("""
                    SELECT * FROM hot_orders
                    WHERE claimed_by = %s
                       OR (status = 'open' AND (skill = '' OR skill = 'all' OR skill = ANY(%s) OR 'all' = ANY(%s)))
                    ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, created_at DESC
                """, (worker_id, allowed_skills, allowed_skills))
            rows = cur.fetchall()
    return [_row(row, reveal_payout=is_admin) for row in rows]


def claim_hot_order(*, order_id: str, worker_id: int, skills: list[str]) -> dict | None:
    allowed_skills = [str(item).strip().casefold() for item in skills if str(item).strip()]
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE hot_orders
                SET status = 'claimed', claimed_by = %s, claimed_at = now()
                WHERE id = %s::uuid
                  AND status = 'open'
                  AND (skill = '' OR skill = 'all' OR skill = ANY(%s) OR 'all' = ANY(%s))
                RETURNING *
            """, (worker_id, order_id, allowed_skills, allowed_skills))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def release_hot_order(*, order_id: str, worker_id: int, is_admin: bool = False) -> dict | None:
    """Reopen a claimed order for its worker, creator, or an administrator."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE hot_orders
                SET status = 'open', claimed_by = NULL, claimed_at = NULL, released_at = now()
                WHERE id = %s::uuid
                  AND status = 'claimed'
                  AND (claimed_by = %s OR created_by = %s OR %s)
                RETURNING *
            """, (order_id, worker_id, worker_id, is_admin))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def submit_hot_order(*, order_id: str, worker_id: int, report: str) -> dict | None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE hot_orders
                SET status = 'submitted', report = %s, completed_at = now()
                WHERE id = %s::uuid AND status = 'claimed' AND claimed_by = %s
                RETURNING *
            """, (report, order_id, worker_id))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def settle_hot_order(*, order_id: str, admin_id: int, payout_amount: Decimal | float) -> dict | None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE hot_orders
                SET status = CASE WHEN status = 'submitted' THEN 'settled' ELSE status END,
                    payout_amount = CASE WHEN status = 'submitted' THEN %s ELSE payout_amount END,
                    payout_set_by = CASE WHEN status = 'submitted' THEN %s ELSE payout_set_by END,
                    settled_at = CASE WHEN status = 'submitted' THEN now() ELSE settled_at END
                WHERE id = %s::uuid
                  AND (status = 'submitted' OR (status = 'settled' AND payout_set_by = %s))
                RETURNING *
            """, (payout_amount, admin_id, order_id, admin_id))
            row = cur.fetchone()
        conn.commit()
    return _row(row, reveal_payout=True) if row else None
