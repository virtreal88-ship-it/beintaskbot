"""Tenant-scoped native customer company service; no external CRM calls."""
from __future__ import annotations
import uuid
from tenant_platform_core import _connect, _ensure_schema
from tenant_policy import TenantPolicy
from tenant_scoped_access import scoped_access_enabled
from tenant_crm_native_access import current_access, scoped_sql, authorize_create, locked_resource, action_columns, public_row, CRMAccessError, related_scope_sql, require_defined_relation, require_local_relation


def _id(value):
    try: return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError): raise ValueError('invalid company id') from None


def _fields(name, phone='', email='', description='', responsible_id=None):
    name=str(name or '').strip(); phone=str(phone or '').strip(); email=str(email or '').strip(); description=str(description or '').strip()
    if not name or len(name)>240 or len(phone)>40 or len(email)>254 or len(description)>12000: raise ValueError('invalid company fields')
    responsible=int(responsible_id) if responsible_id not in ('',None) else None
    if responsible is not None and responsible<=0: raise ValueError('invalid responsible')
    return name,phone,email,description,responsible


def create_company(tenant_id,name,phone='',email='',description='',responsible_id=None,*,company_id=None,actor_id=None):
    values=_fields(name,phone,email,description,responsible_id); ident=_id(company_id) if company_id else str(uuid.uuid4())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'companies', 'create')
            authorize_create(cur, profile, 'companies', tenant_id, actor_id)
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
                    if profile is not None:
                        existing = locked_resource(cur, profile, 'companies', 'create', 'saas_crm_native_companies', tenant_id, existing['id'])
                        if not existing:
                            raise ValueError('company already exists')
                        if TenantPolicy(profile).has_scoped_access('companies') and (
                                str(existing.get('access_responsible_id')) != str(actor_id)
                                or str(existing['id']) != ident
                                or (existing['name'], existing['phone'], existing['email'],
                                    existing['description'], existing['responsible_id']) != values):
                            # C without R permits a genuine retry, not a lookup
                            # of old company details by a known phone/email.
                            raise ValueError('company already exists')
                    conn.commit()
                    return public_row(existing)
            cur.execute("""INSERT INTO saas_crm_native_companies(id,tenant_id,name,phone,email,description,responsible_id,access_responsible_id)
                VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id,name,phone,email,description,responsible_id""",(ident,tenant_id,*values,actor_id))
            row=cur.fetchone()
            if not row:
                cur.execute("SELECT id,name,phone,email,description,responsible_id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(tenant_id,ident)); row=cur.fetchone()
                if profile is not None:
                    row = locked_resource(cur, profile, 'companies', 'create', 'saas_crm_native_companies', tenant_id, ident)
                    if row and TenantPolicy(profile).has_scoped_access('companies') and str(row.get('access_responsible_id')) != str(actor_id):
                        row = None
                if not row or (row['name'],row['phone'],row['email'],row['description'],row['responsible_id']) != values: raise ValueError('company id conflict')
        conn.commit()
    return public_row(row)


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


def list_companies(tenant_id,search='',limit=50,*,actor_id=None):
    q='%'+str(search or '').strip()[:120].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'companies', 'view')
            where, params = scoped_sql(profile, 'companies', 'view', 'c')
            contacts, contact_params = related_scope_sql(profile, 'customers', 'x')
            deals, deal_params = related_scope_sql(profile, 'deals', 'd')
            columns, action_params = action_columns(profile, 'companies', 'c')
            cur.execute(f"""SELECT c.id,c.name,c.phone,c.email,c.description,c.responsible_id,m.display_name AS responsible_name,
                (SELECT count(*) FROM saas_crm_native_contacts x WHERE x.tenant_id=c.tenant_id AND x.company_id=c.id AND x.archived_at IS NULL AND ({contacts})) contact_count,
                (SELECT count(*) FROM saas_crm_native_deals d WHERE d.tenant_id=c.tenant_id AND d.company_id=c.id AND d.archived_at IS NULL AND ({deals})) deal_count {columns}
                FROM saas_crm_native_companies c LEFT JOIN saas_tenant_members m ON m.tenant_id=c.tenant_id AND m.telegram_id=c.responsible_id
                WHERE c.tenant_id=%s::uuid AND c.archived_at IS NULL AND ({where})
                AND (c.name ILIKE %s ESCAPE '\\' OR c.phone ILIKE %s ESCAPE '\\' OR c.email ILIKE %s ESCAPE '\\')
                ORDER BY c.updated_at DESC,c.id LIMIT %s""",
                [*contact_params,*deal_params,*action_params,tenant_id,*params,q,q,q,max(1,min(int(limit),100))])
            return [public_row(row) for row in cur.fetchall()]


def get_company(tenant_id,company_id,*,actor_id=None):
    ident=_id(company_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'companies', 'view')
            where, params = scoped_sql(profile, 'companies', 'view', 'c')
            columns, action_params = action_columns(profile, 'companies', 'c')
            cur.execute(f"""SELECT c.id,c.name,c.phone,c.email,c.description,c.responsible_id,c.created_at,c.updated_at {columns}
              FROM saas_crm_native_companies c WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid
              AND c.archived_at IS NULL AND ({where})""", [*action_params,tenant_id,ident,*params])
            row=public_row(cur.fetchone())
            if not row:return None
            if 'actions' in row:
                row['relation_actions'] = {
                    'link_contact': row['actions']['update'],
                    'unlink_contact': row['actions']['update' if scoped_access_enabled() else 'archive'],
                    'link_deal': row['actions']['update'] and (scoped_access_enabled()
                        or TenantPolicy(profile).allows_action('deals','update')),
                    'unlink_deal': row['actions']['update' if scoped_access_enabled() else 'archive'],
                }
            where, params = related_scope_sql(profile, 'customers', 'c')
            cur.execute(f"""SELECT c.id,c.display_name,c.phone,c.email FROM saas_crm_native_contacts c
              WHERE c.tenant_id=%s::uuid AND c.company_id=%s::uuid AND c.archived_at IS NULL
              AND ({where}) ORDER BY c.display_name""",[tenant_id,ident,*params]); row['contacts']=cur.fetchall()
            where, params = related_scope_sql(profile, 'deals', 'd')
            cur.execute(f"""SELECT d.id,d.title,d.pipeline_id,d.stage_id,d.contact_id,d.responsible_id
              FROM saas_crm_native_deals d WHERE d.tenant_id=%s::uuid AND d.company_id=%s::uuid
              AND d.archived_at IS NULL AND ({where}) ORDER BY d.updated_at DESC""",
              [tenant_id,ident,*params]); row['deals']=cur.fetchall()
            return row


def update_company(tenant_id,company_id,name,phone='',email='',description='',responsible_id=None,*,actor_id=None):
    ident=_id(company_id); values=_fields(name,phone,email,description,responsible_id)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'companies', 'update')
            if actor_id is not None and not locked_resource(cur, profile, 'companies', 'update',
                    'saas_crm_native_companies', tenant_id, ident):
                return None
            if values[4]:
                cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE",(tenant_id,values[4]))
                if not cur.fetchone():raise ValueError('responsible unavailable')
            cur.execute("UPDATE saas_crm_native_companies SET name=%s,phone=%s,email=%s,description=%s,responsible_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id,name,phone,email,description,responsible_id",(*values,tenant_id,ident)); row=cur.fetchone()
        conn.commit()
    return row


def archive_company(tenant_id,company_id,*,actor_id=None):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            profile = current_access(cur, tenant_id, actor_id, 'companies', 'delete')
            if actor_id is not None and not locked_resource(cur, profile, 'companies', 'delete',
                    'saas_crm_native_companies', tenant_id, _id(company_id)):
                return False
            cur.execute("UPDATE saas_crm_native_companies SET archived_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,_id(company_id)))
            row=cur.fetchone()
        conn.commit()
    return row is not None


def _relation_access(cur, tenant, actor, company, entity_type, entity, *, unlink=False):
    # Editing a company card's contact/deal relation requires only company U.
    # Lock the contact/deal first in both card directions to avoid a lock-order
    # cycle with contact/deal->company edits. No target contents are read/returned.
    if scoped_access_enabled() and (entity_type == 'contact' or (entity_type == 'deal' and actor is not None)):
        profile = current_access(cur, tenant, actor, 'companies', 'update')
        table = {'contact': 'saas_crm_native_contacts', 'deal': 'saas_crm_native_deals'}[entity_type]
        cur.execute(f'''SELECT id FROM {table} WHERE tenant_id=%s::uuid
            AND id=%s::uuid AND archived_at IS NULL FOR UPDATE''', (tenant, entity))
        if not cur.fetchone():
            return False
        if not locked_resource(cur, profile, 'companies', 'update',
                'saas_crm_native_companies', tenant, company):
            return False
        require_local_relation(cur, tenant, entity_type)
        return True
    company_action = 'delete' if unlink else 'update'
    profile = current_access(cur, tenant, actor, 'companies', company_action)
    if profile is None:
        return True
    module = 'customers' if entity_type == 'contact' else 'deals'
    require_defined_relation(profile, 'companies', module)
    # Keep the pre-scoped dual-U/link and company-D/unlink contract gate off.
    if not unlink and entity_type == 'deal' and not TenantPolicy(profile).allows_action('deals', 'update'):
        raise CRMAccessError()
    return True


def link_entity(tenant_id,company_id,entity_type,entity_id,*,actor_id=None):
    ident=_id(company_id); entity=_id(entity_id)
    table={'contact':'saas_crm_native_contacts','deal':'saas_crm_native_deals'}.get(entity_type)
    if not table: raise ValueError('invalid entity type')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if not _relation_access(cur,tenant_id,actor_id,ident,entity_type,entity):
                return None
            cur.execute("SELECT 1 FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(tenant_id,ident))
            if not cur.fetchone():return None
            cur.execute(f"UPDATE {table} SET company_id=%s::uuid,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(ident,tenant_id,entity))
            row=cur.fetchone()
        conn.commit()
    return row


def unlink_entity(tenant_id,company_id,entity_type,entity_id,*,actor_id=None):
    ident=_id(company_id); entity=_id(entity_id)
    table={'contact':'saas_crm_native_contacts','deal':'saas_crm_native_deals'}.get(entity_type)
    if not table: raise ValueError('invalid entity type')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if not _relation_access(cur,tenant_id,actor_id,ident,entity_type,entity,unlink=True):
                return None
            cur.execute(f"UPDATE {table} SET company_id=NULL,updated_at=now() WHERE tenant_id=%s::uuid AND company_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,ident,entity))
            row=cur.fetchone()
        conn.commit()
    return row
