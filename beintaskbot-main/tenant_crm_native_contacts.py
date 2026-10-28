"""Tenant-scoped native contact CRUD. No Kommo requests or external side effects."""
from __future__ import annotations
import uuid
import re

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
            cur.execute("""SELECT c.id, c.display_name, c.phone, c.email, c.company_id, co.name AS company_name, c.created_at, c.updated_at
                FROM saas_crm_native_contacts c
                LEFT JOIN saas_crm_native_companies co ON co.tenant_id=c.tenant_id AND co.id=c.company_id
                WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid AND c.archived_at IS NULL""", (tenant_id, _valid_id(contact_id)))
            return cur.fetchone()


def list_contacts(tenant_id: str, search: str = '', limit: int = 50):
    query = '%' + str(search or '').strip()[:120].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT c.id, c.display_name, c.phone, c.email, c.company_id, co.name AS company_name, c.created_at, c.updated_at
                FROM saas_crm_native_contacts c
                LEFT JOIN saas_crm_native_companies co ON co.tenant_id=c.tenant_id AND co.id=c.company_id
                WHERE c.tenant_id=%s::uuid AND c.archived_at IS NULL
                AND (c.display_name ILIKE %s ESCAPE '\\' OR c.phone ILIKE %s ESCAPE '\\' OR c.email ILIKE %s ESCAPE '\\')
                ORDER BY c.updated_at DESC, c.id LIMIT %s""",
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


def upsert_whatsapp_contact(cur, tenant_id: str, channel_id: str, conversation_id: str,
                            phone: str, display_name: str = '', message_id: str = ''):
    """Resolve a direct WhatsApp conversation to a tenant-owned native contact.

    Caller must pass a provider-verified personal JID number, never a name or
    group identifier.  The caller's transaction makes contact and conversation
    linkage atomic with the durable message receipt.
    """
    number = re.sub(r'\D', '', str(phone or ''))
    if not re.fullmatch(r'[1-9][0-9]{5,18}', number):
        return None
    try:
        channel = str(uuid.UUID(str(channel_id)))
    except (ValueError, TypeError, AttributeError):
        return None
    conversation = str(conversation_id or '').strip()
    # Require the number to be encoded in an explicit personal JID.  A caller
    # cannot turn an arbitrary chat ID or group participant name into identity.
    suffix = next((jid for jid in ('@s.whatsapp.net', '@c.us') if conversation.endswith(jid)), '')
    if not suffix or conversation.split('@', 1)[0] != number:
        # A late provider pair keeps the original LID route and all its
        # references. Accept its phone only through the durable scoped alias,
        # including the alias's uniqueness guarantee, never from a label.
        if not re.fullmatch(r'[1-9][0-9]{5,22}@lid', conversation):
            return None
        cur.execute('''SELECT 1 FROM saas_connect_direct_identities
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
            AND identity_key=%s AND canonical_conversation_id=%s''',
          (tenant_id, channel, number + '@s.whatsapp.net', conversation))
        if not cur.fetchone():
            return None
    # Serializes first-contact races, including the same person arriving via
    # two channels.  Hashing is database-local and exposes no identity.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(tenant_id) + ':wa:' + number,))
    cur.execute("""SELECT c.id,c.display_name,c.phone,c.email
        FROM saas_crm_native_contact_identities i
        JOIN saas_crm_native_contacts c ON c.tenant_id=i.tenant_id AND c.id=i.contact_id
        WHERE i.tenant_id=%s::uuid AND i.identity_type='whatsapp_phone' AND i.identity_value=%s
          AND c.archived_at IS NULL FOR UPDATE OF c""", (tenant_id, number))
    row = cur.fetchone()
    if row is None:
        # Adopt a pre-existing contact with the same verified number before
        # creating a new one. Names are deliberately excluded from matching.
        cur.execute("""SELECT id,display_name,phone,email FROM saas_crm_native_contacts
            WHERE tenant_id=%s::uuid AND archived_at IS NULL
              AND regexp_replace(phone,'[^0-9]','','g')=%s
            ORDER BY created_at,id LIMIT 1 FOR UPDATE""", (tenant_id, number))
        row = cur.fetchone()
    if row is None:
        name = str(display_name or '').strip()[:180] or number
        ident = str(uuid.uuid4())
        cur.execute("""INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone)
            VALUES(%s::uuid,%s::uuid,%s,%s) RETURNING id,display_name,phone,email""",
            (ident, tenant_id, name, number))
        row = cur.fetchone()
    ident = str(row['id'])
    cur.execute("""INSERT INTO saas_crm_native_contact_identities
        (tenant_id,identity_type,identity_value,contact_id)
        VALUES(%s::uuid,'whatsapp_phone',%s,%s::uuid)
        ON CONFLICT(tenant_id,identity_type,identity_value) DO NOTHING""", (tenant_id, number, ident))
    # If an older transaction won the identity race, honor its mapping.
    cur.execute("""SELECT contact_id FROM saas_crm_native_contact_identities
        WHERE tenant_id=%s::uuid AND identity_type='whatsapp_phone' AND identity_value=%s""",
        (tenant_id, number))
    identity = cur.fetchone()
    ident = str(identity['contact_id'])
    cur.execute("""INSERT INTO saas_crm_native_contact_conversations
        (tenant_id,provider,channel_id,conversation_id,contact_id,first_message_id)
        VALUES(%s::uuid,'connect',%s::uuid,%s,%s::uuid,%s)
        ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE
        SET contact_id=EXCLUDED.contact_id""",
        (tenant_id, channel, conversation, ident, str(message_id or '')[:240]))
    return ident
