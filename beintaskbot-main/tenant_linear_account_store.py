"""Shared Linear account dictionary storage."""
import re
import uuid

from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema

def list_linear_accounts() -> list[dict]:
    """Return the shared account dictionary used by Linear task creation."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, created_at, updated_at
                FROM crm_linear_accounts
                WHERE active = TRUE
                ORDER BY name_key ASC
            """)
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        for key in ("created_at", "updated_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        result.append(item)
    return result


def create_linear_account(*, name: str, created_by: int) -> dict:
    """Add an account to the shared dictionary, returning an existing match."""
    clean_name = re.sub(r"\s+", " ", str(name or "").strip())[:160]
    if not clean_name:
        raise TenantPlatformError("Hesab adını yazın.")
    name_key = clean_name.casefold()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_accounts (id, name, name_key, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name_key) DO UPDATE SET active = TRUE, updated_at = now()
                RETURNING id, name, created_at, updated_at
            """, (uuid.uuid4(), clean_name, name_key, int(created_by)))
            row = cur.fetchone()
        conn.commit()
    item = dict(row)
    item["id"] = str(item["id"])
    for key in ("created_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item
