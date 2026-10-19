"""Tenant-scoped native customer company service; no external CRM calls."""
from __future__ import annotations
import uuid
from tenant_platform_core import _connect, _ensure_schema


def _id(value):
    try: return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError): raise ValueError('invalid company id') from None


def _fields(name, phone='', email='', description='', responsible_id=None):
    name=str(name or '').strip(); phone=str(phone or '').strip(); email=str(email or '').strip(); description=str(description or '').strip()
    if not name or len(name)>240 or len(phone)>40 or len(email)>254 or len(description)>12000: raise ValueError('invalid company fields')
    responsible=int(responsible_id) if responsible_id not in ('',None) else None
    if responsible is not None and responsible<=0: raise ValueError('invalid responsible')
    return name,phone,email,description,responsible


def create_company(tenant_id,name,phone='',email='',description='',responsible_id=None,*,company_id=None):
    values=_fields(name,phone,email,description,responsible_id); ident=_id(company_id) if company_id else str(uuid.uuid4())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if values[4]:
                cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant_id,values[4]))
                if not cur.fetchone(): raise ValueError('responsible unavailable')
            identity=('email:'+values[2].casefold()) if values[2] else (('phone:'+values[1]) if values[1] else '')
            if identity:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(str(tenant_id)+':company:'+identity,))
                cur.execute("""SELECT id,name,phone,email,description,responsible_id FROM saas_crm_native_companies
                    WHERE tenant_id=%s::uuid AND archived_at IS NULL AND
                    ((%s<>'' AND lower(email)=lower(%s)) OR (%s='' AND %s<>'' AND phone=%s))
                    ORDER BY created_at,id LIMIT 1 FOR UPDATE""",(tenant_id,values[2],values[2],values[2],values[1],values[1]))
                existing=cur.fetchone()
                if existing:
                    conn.commit()
                    return existing
            cur.execute("""INSERT INTO saas_crm_native_companies(id,tenant_id,name,phone,email,description,responsible_id)
                VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id,name,phone,email,description,responsible_id""",(ident,tenant_id,*values))
            row=cur.fetchone()
            if not row:
                cur.execute("SELECT id,name,phone,email,description,responsible_id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(tenant_id,ident)); row=cur.fetchone()
                if not row or (row['name'],row['phone'],row['email'],row['description'],row['responsible_id']) != values: raise ValueError('company id conflict')
        conn.commit()
    return row


def find_or_create_company(tenant_id,name,phone='',email='',description='',responsible_id=None):
    """Exact-name dedupe for verified AI-trigger facts, serialized per tenant."""
    values=_fields(name,phone,email,description,responsible_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(str(tenant_id)+':ai-company:'+values[0].casefold(),))
            cur.execute("SELECT id,name,phone,email,description,responsible_id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND archived_at IS NULL AND lower(name)=lower(%s) ORDER BY created_at,id LIMIT 2 FOR UPDATE",(tenant_id,values[0]))
            rows=cur.fetchall()
            if len(rows)>1:raise ValueError('company name is ambiguous')
            if rows:row=rows[0]
            else:
                if values[4]:
                    cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant_id,values[4]))
                    if not cur.fetchone():raise ValueError('responsible unavailable')
                cur.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name,phone,email,description,responsible_id) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s) RETURNING id,name,phone,email,description,responsible_id",(str(uuid.uuid4()),tenant_id,*values))
                row=cur.fetchone()
        conn.commit()
    return row


def list_companies(tenant_id,search='',limit=50):
    q='%'+str(search or '').strip()[:120].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT c.id,c.name,c.phone,c.email,c.description,c.responsible_id,m.display_name AS responsible_name,
                (SELECT count(*) FROM saas_crm_native_contacts x WHERE x.tenant_id=c.tenant_id AND x.company_id=c.id AND x.archived_at IS NULL) contact_count,
                (SELECT count(*) FROM saas_crm_native_deals d WHERE d.tenant_id=c.tenant_id AND d.company_id=c.id AND d.archived_at IS NULL) deal_count
                FROM saas_crm_native_companies c LEFT JOIN saas_tenant_members m ON m.tenant_id=c.tenant_id AND m.telegram_id=c.responsible_id
                WHERE c.tenant_id=%s::uuid AND c.archived_at IS NULL AND (c.name ILIKE %s ESCAPE '\\' OR c.phone ILIKE %s ESCAPE '\\' OR c.email ILIKE %s ESCAPE '\\')
                ORDER BY c.updated_at DESC,c.id LIMIT %s""",(tenant_id,q,q,q,max(1,min(int(limit),100))))
            return cur.fetchall()


def get_company(tenant_id,company_id):
    ident=_id(company_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,phone,email,description,responsible_id,created_at,updated_at FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(tenant_id,ident)); row=cur.fetchone()
            if not row:return None
            cur.execute("SELECT id,display_name,phone,email FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND company_id=%s::uuid AND archived_at IS NULL ORDER BY display_name",(tenant_id,ident)); row['contacts']=cur.fetchall()
            cur.execute("SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND company_id=%s::uuid AND archived_at IS NULL ORDER BY updated_at DESC",(tenant_id,ident)); row['deals']=cur.fetchall()
            return row


def update_company(tenant_id,company_id,name,phone='',email='',description='',responsible_id=None):
    ident=_id(company_id); values=_fields(name,phone,email,description,responsible_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if values[4]:
                cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant_id,values[4]))
                if not cur.fetchone():raise ValueError('responsible unavailable')
            cur.execute("UPDATE saas_crm_native_companies SET name=%s,phone=%s,email=%s,description=%s,responsible_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id,name,phone,email,description,responsible_id",(*values,tenant_id,ident)); row=cur.fetchone()
        conn.commit()
    return row


def archive_company(tenant_id,company_id):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_crm_native_companies SET archived_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,_id(company_id)))
            row=cur.fetchone()
        conn.commit()
    return row is not None


def link_entity(tenant_id,company_id,entity_type,entity_id):
    ident=_id(company_id); entity=_id(entity_id)
    table={'contact':'saas_crm_native_contacts','deal':'saas_crm_native_deals'}.get(entity_type)
    if not table: raise ValueError('invalid entity type')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(tenant_id,ident))
            if not cur.fetchone():return None
            cur.execute(f"UPDATE {table} SET company_id=%s::uuid,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(ident,tenant_id,entity))
            row=cur.fetchone()
        conn.commit()
    return row


def unlink_entity(tenant_id,company_id,entity_type,entity_id):
    ident=_id(company_id); entity=_id(entity_id)
    table={'contact':'saas_crm_native_contacts','deal':'saas_crm_native_deals'}.get(entity_type)
    if not table: raise ValueError('invalid entity type')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET company_id=NULL,updated_at=now() WHERE tenant_id=%s::uuid AND company_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,ident,entity))
            row=cur.fetchone()
        conn.commit()
    return row
