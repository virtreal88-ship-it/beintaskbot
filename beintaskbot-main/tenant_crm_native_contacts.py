"""Tenant-scoped native contact CRUD with atomic scoped scalar sync receipts."""
from __future__ import annotations
import uuid
import re

from tenant_platform_core import _connect, _ensure_schema
from tenant_policy import TenantPolicy
from tenant_scoped_access import scoped_access_enabled
from tenant_crm_scalar_store import lock_admission, queue_edit, managed
from tenant_crm_native_access import current_access, scoped_sql, authorize_create, locked_resource, action_columns, public_row, CRMAccessError, require_local_relation


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


def create_contact(tenant_id: str, name: str, phone: str = '', email: str = '', *, contact_id=None, actor_id=None):
    name, phone, email = _fields(name, phone, email)
    ident = _valid_id(contact_id) if contact_id is not None else str(uuid.uuid4())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            lock_admission(cur, tenant_id, actor_id)
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'create')
            authorize_create(cur, profile, 'customers', tenant_id, actor_id)
            cur.execute("""INSERT INTO saas_crm_native_contacts
                (id, tenant_id, display_name, phone, email, access_responsible_id)
                VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING RETURNING id, display_name, phone, email""",
                (ident, tenant_id, name, phone, email, actor_id))
            row = cur.fetchone()
            if row is None:
                # An idempotent create must not read a record outside this
                # action's scope, even when the caller knows its UUID/fields.
                row = locked_resource(cur, profile, 'customers', 'create',
                    'saas_crm_native_contacts', tenant_id, ident)
                if (row is None or (profile is not None and TenantPolicy(profile).has_scoped_access('customers')
                        and str(row.get('access_responsible_id')) != str(actor_id))
                        or (row['display_name'], row['phone'], row['email']) != (name, phone, email)):
                    raise ValueError('contact id conflict')
            sync = queue_edit(cur, tenant_id, actor_id, profile, 'contact', row, None, action='create')
            if sync is not None: row = {**row, 'sync': sync}
        conn.commit()
    return public_row(row)


def get_contact(tenant_id: str, contact_id: str, *, actor_id=None):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'view')
            where, params = scoped_sql(profile, 'customers', 'view', 'c')
            columns, action_params = action_columns(profile, 'customers', 'c')
            cur.execute(f"""SELECT c.id, c.display_name, c.phone, c.email, c.company_id,
                co.name AS company_name, c.created_at, c.updated_at {columns}
                FROM saas_crm_native_contacts c
                LEFT JOIN saas_crm_native_companies co ON co.tenant_id=c.tenant_id AND co.id=c.company_id
                WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid AND c.archived_at IS NULL
                AND ({where})""", [*action_params, tenant_id, _valid_id(contact_id), *params])
            return public_row(cur.fetchone())


def list_contacts(tenant_id: str, search: str = '', limit: int = 50, *, actor_id=None):
    query = '%' + str(search or '').strip()[:120].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'view')
            where, params = scoped_sql(profile, 'customers', 'view', 'c')
            columns, action_params = action_columns(profile, 'customers', 'c')
            cur.execute(f"""SELECT c.id, c.display_name, c.phone, c.email, c.company_id,
                co.name AS company_name, c.created_at, c.updated_at {columns}
                FROM saas_crm_native_contacts c
                LEFT JOIN saas_crm_native_companies co ON co.tenant_id=c.tenant_id AND co.id=c.company_id
                WHERE c.tenant_id=%s::uuid AND c.archived_at IS NULL AND ({where})
                AND (c.display_name ILIKE %s ESCAPE '\\' OR c.phone ILIKE %s ESCAPE '\\' OR c.email ILIKE %s ESCAPE '\\')
                ORDER BY c.updated_at DESC, c.id LIMIT %s""",
                [*action_params, tenant_id, *params, query, query, query, max(1, min(int(limit), 100))])
            return [public_row(row) for row in cur.fetchall()]


def update_contact(tenant_id: str, contact_id: str, name: str, phone: str = '', email: str = '', *, actor_id=None):
    name, phone, email = _fields(name, phone, email)
    ident = _valid_id(contact_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            from tenant_crm_writer_fence import lock_card
            from tenant_scoped_access import scoped_access_enabled
            if actor_id is not None and scoped_access_enabled():
                lock_card(cur, tenant_id, 'contact', ident)
            lock_admission(cur, tenant_id, actor_id)
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'update')
            previous = None
            if actor_id is not None:
                previous = locked_resource(cur, profile, 'customers', 'update',
                    'saas_crm_native_contacts', tenant_id, ident)
                if not previous: return None
            if (previous and managed(profile, 'contact')
                    and (previous['display_name'], previous['phone'], previous['email']) == (name, phone, email)):
                row = {key:previous[key] for key in ('id','display_name','phone','email')}
                row['sync'] = queue_edit(cur, tenant_id, actor_id, profile, 'contact', row, previous)
                conn.commit()
                return row
            stamp_sql = 'clock_timestamp()' if managed(profile, 'contact') else 'now()'
            cur.execute(f"""UPDATE saas_crm_native_contacts
                SET display_name=%s, phone=%s, email=%s, updated_at={stamp_sql}
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL
                RETURNING id, display_name, phone, email""",
                (name, phone, email, tenant_id, ident))
            row = cur.fetchone()
            if row:
                sync = queue_edit(cur, tenant_id, actor_id, profile, 'contact', row, previous)
                if sync is not None: row = {**row, 'sync': sync}
        conn.commit()
    return row


def archive_contact(tenant_id: str, contact_id: str, *, actor_id=None):
    ident = _valid_id(contact_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'delete')
            if actor_id is not None and not locked_resource(cur, profile, 'customers', 'delete',
                    'saas_crm_native_contacts', tenant_id, ident):
                return False
            cur.execute("""UPDATE saas_crm_native_contacts
                SET archived_at=now(), updated_at=now()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL
                RETURNING id""", (tenant_id, ident))
            row = cur.fetchone()
        conn.commit()
    return row is not None


def set_contact_deal_link(tenant_id, contact_id, deal_id, *, unlink=False, actor_id):
    """Edit a contact card's deal relation using only that contact's U scope.

    No deal contents, fields or authority are returned through this route.
    Unlink is conditional on the exact current contact, so it cannot remove
    somebody else's link by presenting an unrelated editable contact.
    """
    if not scoped_access_enabled():
        raise CRMAccessError('scoped_relation_unavailable', 403)
    contact = _valid_id(contact_id)
    deal = _valid_id(deal_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'update')
            if not locked_resource(cur, profile, 'customers', 'update',
                    'saas_crm_native_contacts', tenant_id, contact):
                return None
            cur.execute("""SELECT id,contact_id FROM saas_crm_native_deals
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE""",
                (tenant_id, deal))
            target = cur.fetchone()
            if not target or (unlink and str(target['contact_id'] or '') != contact):
                return None
            require_local_relation(cur, tenant_id, 'deal')
            cur.execute("""UPDATE saas_crm_native_deals SET contact_id=%s::uuid,updated_at=clock_timestamp()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL""",
                (None if unlink else contact, tenant_id, deal))
        conn.commit()
    return {'id': contact, 'deal_id': deal, 'linked': not unlink}



def set_contact_company_link(tenant_id, contact_id, company_id, *, unlink=False, actor_id):
    """Change only an authorized contact's company relation, never company data."""
    if not scoped_access_enabled():
        raise CRMAccessError('scoped_relation_unavailable', 403)
    contact = _valid_id(contact_id)
    company = _valid_id(company_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'customers', 'update')
            row = locked_resource(cur, profile, 'customers', 'update',
                'saas_crm_native_contacts', tenant_id, contact)
            if not row or (unlink and str(row.get('company_id') or '') != company):
                return None
            # A target's membership and active state are required, not its R/U/D.
            # Do not fetch or expose target contents or mutate any company field.
            cur.execute("""SELECT id FROM saas_crm_native_companies
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE""",
                (tenant_id, company))
            if not cur.fetchone():
                return None
            require_local_relation(cur, tenant_id, 'contact')
            cur.execute("""UPDATE saas_crm_native_contacts SET company_id=%s::uuid,updated_at=clock_timestamp()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL""",
                (None if unlink else company, tenant_id, contact))
        conn.commit()
    return {'id': contact, 'company_id': company, 'linked': not unlink}


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
    cur.execute("""SELECT c.id,c.display_name,c.phone,c.email,c.archived_at
        FROM saas_crm_native_contact_identities i
        JOIN saas_crm_native_contacts c ON c.tenant_id=i.tenant_id AND c.id=i.contact_id
        WHERE i.tenant_id=%s::uuid AND i.identity_type='whatsapp_phone' AND i.identity_value=%s
        FOR UPDATE OF c""", (tenant_id, number))
    row = cur.fetchone()
    # A durable identity that points to an archived record must not silently
    # be reassigned to a new CRM contact. Require an explicit recovery action.
    if row is not None and row.get('archived_at') is not None:
        return None
    if row is None:
        # Adopt a pre-existing contact only when the number identifies exactly
        # one active row. A shared number is evidence for candidate discovery,
        # not proof that the first matching contact owns this conversation.
        cur.execute("""SELECT id,display_name,phone,email FROM saas_crm_native_contacts
            WHERE tenant_id=%s::uuid AND archived_at IS NULL
              AND regexp_replace(phone,'[^0-9]','','g')=%s
            ORDER BY created_at,id LIMIT 2 FOR UPDATE""", (tenant_id, number))
        matches = cur.fetchall()
        if len(matches) > 1:
            return None
        row = matches[0] if matches else None
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
    # Never rewrite an established route to another contact merely because a
    # later event supplied a matching phone. Conflicts need explicit review.
    cur.execute("""SELECT contact_id FROM saas_crm_native_contact_conversations
        WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s::uuid
          AND conversation_id=%s FOR UPDATE""", (tenant_id, channel, conversation))
    route = cur.fetchone()
    if route is not None and str(route['contact_id']) != ident:
        return None
    cur.execute("""INSERT INTO saas_crm_native_contact_conversations
        (tenant_id,provider,channel_id,conversation_id,contact_id,first_message_id)
        VALUES(%s::uuid,'connect',%s::uuid,%s,%s::uuid,%s)
        ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO NOTHING""",
        (tenant_id, channel, conversation, ident, str(message_id or '')[:240]))
    cur.execute("""SELECT contact_id FROM saas_crm_native_contact_conversations
        WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s::uuid
          AND conversation_id=%s""", (tenant_id, channel, conversation))
    route = cur.fetchone()
    if not route or str(route['contact_id']) != ident:
        return None
    return ident
