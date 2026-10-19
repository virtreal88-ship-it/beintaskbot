"""Tenant-scoped native contact CRUD. No Kommo requests or external side effects."""
from __future__ import annotations
import uuid

from tenant_platform_core import _connect, _ensure_schema


def _valid_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid contact id') from None


def _fields(name, phone='', email=''):
    name = str(name or '').strip()
    phone = str(phone or '').strip()
    email = str(email or '').strip()
    if not name or len(name) > 180 or len(phone) > 40 or len(email) > 254:
        raise ValueError('invalid contact fields')
    return name, phone, email


def create_contact(tenant_id: str, name: str, phone: str = '', email: str = '', *, contact_id=None):
    name, phone, email = _fields(name, phone, email)
    ident = _valid_id(contact_id) if contact_id is not None else str(uuid.uuid4())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO saas_crm_native_contacts
                (id, tenant_id, display_name, phone, email)
                VALUES (%s::uuid,%s::uuid,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING RETURNING id, display_name, phone, email""",
                (ident, tenant_id, name, phone, email))
            row = cur.fetchone()
            if row is None:
                cur.execute("""SELECT id, display_name, phone, email
                    FROM saas_crm_native_contacts WHERE id=%s::uuid AND tenant_id=%s::uuid
                    AND archived_at IS NULL""", (ident, tenant_id))
                row = cur.fetchone()
                if row is None or (row['display_name'], row['phone'], row['email']) != (name, phone, email):
                    raise ValueError('contact id conflict')
        conn.commit()
    return row


def get_contact(tenant_id: str, contact_id: str):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT id, display_name, phone, email, created_at, updated_at
                FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND id=%s::uuid
                AND archived_at IS NULL""", (tenant_id, _valid_id(contact_id)))
            return cur.fetchone()


def list_contacts(tenant_id: str, search: str = '', limit: int = 50):
    query = '%' + str(search or '').strip()[:120].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT id, display_name, phone, email, created_at, updated_at
                FROM saas_crm_native_contacts
                WHERE tenant_id=%s::uuid AND archived_at IS NULL
                AND (display_name ILIKE %s ESCAPE '\\' OR phone ILIKE %s ESCAPE '\\' OR email ILIKE %s ESCAPE '\\')
                ORDER BY updated_at DESC, id LIMIT %s""",
                (tenant_id, query, query, query, max(1, min(int(limit), 100))))
            return cur.fetchall()


def update_contact(tenant_id: str, contact_id: str, name: str, phone: str = '', email: str = ''):
    name, phone, email = _fields(name, phone, email)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_crm_native_contacts
                SET display_name=%s, phone=%s, email=%s, updated_at=now()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL
                RETURNING id, display_name, phone, email""",
                (name, phone, email, tenant_id, _valid_id(contact_id)))
            row = cur.fetchone()
        conn.commit()
    return row


def archive_contact(tenant_id: str, contact_id: str):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_crm_native_contacts
                SET archived_at=now(), updated_at=now()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL
                RETURNING id""", (tenant_id, _valid_id(contact_id)))
            row = cur.fetchone()
        conn.commit()
    return row is not None
