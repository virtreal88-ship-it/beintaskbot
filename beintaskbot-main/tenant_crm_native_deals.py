"""Tenant-scoped native deal CRUD; no provider sync or external side effects."""
from __future__ import annotations
import uuid

from tenant_platform_core import _connect, _ensure_schema


def _uuid(value, label):
    try:
        return str(uuid.UUID(str(value))) if value else None
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f'invalid {label}') from None


def _fields(title, pipeline_id, stage_id, *, contact_id=None, responsible_id=None,
            channel_id=None, conversation_id='', description=''):
    title = str(title or '').strip()
    pipeline_id, stage_id = str(pipeline_id or '').strip(), str(stage_id or '').strip()
    description = str(description or '').strip()
    conversation_id = str(conversation_id or '').strip()
    if not title or len(title) > 240 or not pipeline_id or len(pipeline_id) > 120 or not stage_id or len(stage_id) > 120 or len(description) > 12000:
        raise ValueError('invalid deal fields')
    if channel_id and not conversation_id or conversation_id and not channel_id:
        raise ValueError('channel and conversation must be provided together')
    if len(conversation_id) > 240:
        raise ValueError('invalid conversation id')
    responsible = int(responsible_id) if responsible_id not in (None, '') else None
    if responsible is not None and responsible <= 0:
        raise ValueError('invalid responsible')
    return (title, pipeline_id, stage_id, _uuid(contact_id, 'contact id'), responsible,
            _uuid(channel_id, 'channel id'), conversation_id, description)


def _related(cur, tenant_id, values):
    _, pipeline, stage, contact, responsible, channel, conversation, _ = values
    cur.execute("""SELECT 1 FROM saas_tenant_pipeline_stages s JOIN saas_tenant_pipelines p
        USING (tenant_id,pipeline_id) WHERE s.tenant_id=%s::uuid AND s.pipeline_id=%s
        AND s.stage_id=%s AND p.active=TRUE""", (tenant_id, pipeline, stage))
    if not cur.fetchone():
        raise ValueError('pipeline or stage unavailable')
    if contact:
        cur.execute("SELECT 1 FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL", (tenant_id, contact))
        if not cur.fetchone(): raise ValueError('contact unavailable')
    if responsible:
        cur.execute("SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE", (tenant_id, responsible))
        if not cur.fetchone(): raise ValueError('responsible unavailable')
    if channel:
        cur.execute("SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s", (tenant_id, channel, conversation))
        if not cur.fetchone(): raise ValueError('conversation unavailable')


def create_deal(tenant_id, title, pipeline_id, stage_id, *, contact_id=None, responsible_id=None,
                channel_id=None, conversation_id='', description='', deal_id=None):
    fields = _fields(title, pipeline_id, stage_id, contact_id=contact_id, responsible_id=responsible_id,
                     channel_id=channel_id, conversation_id=conversation_id, description=description)
    ident = _uuid(deal_id, 'deal id') if deal_id else str(uuid.uuid4())
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _related(cur, tenant_id, fields)
            if fields[5]:
                cur.execute("""SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at
                    FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
                    AND conversation_id=%s AND archived_at IS NULL FOR UPDATE""", (tenant_id,fields[5],fields[6]))
                existing=cur.fetchone()
                if existing:
                    if str(existing['id']) != ident or (existing['title'],existing['pipeline_id'],existing['stage_id'],str(existing['contact_id'] or ''),existing['responsible_id'],str(existing['channel_id']),existing['conversation_id'],existing['description']) != (fields[0],fields[1],fields[2],fields[3] or '',fields[4],fields[5],fields[6],fields[7]):
                        raise ValueError('conversation already linked to another deal')
                    conn.commit()
                    return existing
            cur.execute("""INSERT INTO saas_crm_native_deals
                (id,tenant_id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description)
                VALUES (%s::uuid,%s::uuid,%s,%s,%s,%s::uuid,%s,%s::uuid,%s,%s)
                ON CONFLICT (id) DO NOTHING
                RETURNING id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at""",
                (ident,tenant_id,*fields))
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL", (tenant_id, ident))
                row = cur.fetchone()
                if not row or (row['title'],row['pipeline_id'],row['stage_id'],str(row['contact_id'] or ''),row['responsible_id'],str(row['channel_id'] or ''),row['conversation_id'],row['description']) != (fields[0],fields[1],fields[2],fields[3] or '',fields[4],fields[5] or '',fields[6],fields[7]): raise ValueError('deal id conflict')
            from tenant_crm_native_tasks import maybe_create_stage_task
            maybe_create_stage_task(cur,tenant_id,row,event_key='deal-created:'+str(row['id']))
        conn.commit()
    return row


def get_deal(tenant_id, deal_id):
    ident = _uuid(deal_id, 'deal id')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT d.id,d.title,d.pipeline_id,d.stage_id,d.contact_id,c.display_name AS contact_name,
                d.responsible_id,m.display_name AS responsible_name,d.channel_id,d.conversation_id,d.description,d.created_at,d.updated_at
                FROM saas_crm_native_deals d LEFT JOIN saas_crm_native_contacts c
                  ON c.tenant_id=d.tenant_id AND c.id=d.contact_id
                LEFT JOIN saas_tenant_members m ON m.tenant_id=d.tenant_id AND m.telegram_id=d.responsible_id
                WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.archived_at IS NULL""", (tenant_id, ident))
            return cur.fetchone()


def list_deals(tenant_id, search='', limit=50):
    needle = '%' + str(search or '').strip()[:120].replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT d.id,d.title,d.pipeline_id,d.stage_id,d.contact_id,c.display_name AS contact_name,
                d.responsible_id,d.channel_id,d.conversation_id,d.updated_at FROM saas_crm_native_deals d
                LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=d.tenant_id AND c.id=d.contact_id
                WHERE d.tenant_id=%s::uuid AND d.archived_at IS NULL AND (d.title ILIKE %s ESCAPE '\\' OR c.display_name ILIKE %s ESCAPE '\\')
                ORDER BY d.updated_at DESC,d.id LIMIT %s""",(tenant_id,needle,needle,max(1,min(int(limit),100))))
            return cur.fetchall()


def deal_options(tenant_id):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT p.pipeline_id,p.name AS pipeline_name,s.stage_id,s.name AS stage_name,s.sort_order
                FROM saas_tenant_pipelines p JOIN saas_tenant_pipeline_stages s
                USING (tenant_id,pipeline_id) WHERE p.tenant_id=%s::uuid AND p.active=TRUE
                ORDER BY p.name,s.sort_order,s.name""",(tenant_id,))
            workflows=cur.fetchall()
            cur.execute("SELECT telegram_id,display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY display_name",(tenant_id,))
            members=cur.fetchall()
            cur.execute("SELECT id,display_name FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND archived_at IS NULL ORDER BY display_name LIMIT 250",(tenant_id,))
            contacts=cur.fetchall()
    return {'workflows':workflows,'members':members,'contacts':contacts}


def update_deal(tenant_id, deal_id, title, pipeline_id, stage_id, *, contact_id=None, responsible_id=None,
                channel_id=None, conversation_id='', description=''):
    ident = _uuid(deal_id, 'deal id')
    fields = _fields(title,pipeline_id,stage_id,contact_id=contact_id,responsible_id=responsible_id,
                     channel_id=channel_id,conversation_id=conversation_id,description=description)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _related(cur,tenant_id,fields)
            cur.execute("SELECT stage_id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE",(tenant_id,ident))
            previous=cur.fetchone()
            cur.execute("""UPDATE saas_crm_native_deals SET title=%s,pipeline_id=%s,stage_id=%s,contact_id=%s::uuid,
                responsible_id=%s,channel_id=%s::uuid,conversation_id=%s,description=%s,updated_at=now()
                WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL
                RETURNING id,title,pipeline_id,stage_id,contact_id,responsible_id,channel_id,conversation_id,description,created_at,updated_at""",
                (*fields[:3], fields[3],fields[4],fields[5],fields[6],fields[7],tenant_id,ident))
            row=cur.fetchone()
            if row and previous and str(previous['stage_id']) != str(row['stage_id']):
                from tenant_crm_native_tasks import maybe_create_stage_task
                stamp=row['updated_at'].isoformat() if row.get('updated_at') else str(uuid.uuid4())
                maybe_create_stage_task(cur,tenant_id,row,event_key=f"stage-entered:{row['id']}:{previous['stage_id']}:{row['stage_id']}:{stamp}")
        conn.commit()
    return row


def archive_deal(tenant_id, deal_id):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_crm_native_deals SET archived_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,_uuid(deal_id,'deal id')))
            row=cur.fetchone()
        conn.commit()
    return row is not None
