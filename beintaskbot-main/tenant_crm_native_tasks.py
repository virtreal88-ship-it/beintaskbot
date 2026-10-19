"""Native, tenant-owned task lifecycle and stage-entry workflow hook."""
from __future__ import annotations
import uuid
import hashlib
from datetime import datetime,timedelta,timezone
from tenant_platform_core import _connect,_ensure_schema


def _id(value,label):
    try:return str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError):raise ValueError(f'invalid {label}') from None

def _text(value):
    value=str(value or '').strip()
    if not value or len(value)>3500:return None
    return value

def _due(value):
    if value in (None,''):return None
    try:dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (TypeError,ValueError):raise ValueError('invalid due date') from None
    if dt.tzinfo is None:raise ValueError('due date needs timezone')
    return dt.astimezone(timezone.utc)

def _assignee(cur,tenant_id,responsible_id):
    try:person=int(responsible_id)
    except (ValueError,TypeError):raise ValueError('invalid responsible') from None
    cur.execute("""SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s
        AND active=TRUE AND (role='owner' OR permissions @> '[\"tasks\"]'::jsonb)""",(tenant_id,person))
    if not cur.fetchone():raise ValueError('responsible cannot use tasks')
    return person

def _deal(cur,tenant_id,deal_id):
    cur.execute("""SELECT id,title,contact_id,responsible_id,channel_id,conversation_id,pipeline_id,stage_id
        FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE""",(tenant_id,deal_id))
    row=cur.fetchone()
    if not row:raise ValueError('deal unavailable')
    return row

def _row(cur,tenant_id,task_id):
    cur.execute("""SELECT t.id,t.deal_id,d.title AS deal_title,t.contact_id,c.display_name AS contact_name,
        t.channel_id,t.conversation_id,t.text,t.due_at,t.responsible_id,m.display_name AS responsible_name,
        t.status,t.completed_at,t.created_by,t.created_at,t.updated_at,d.pipeline_id,d.stage_id
        FROM saas_crm_native_tasks t JOIN saas_crm_native_deals d ON d.tenant_id=t.tenant_id AND d.id=t.deal_id
        LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=t.tenant_id AND c.id=t.contact_id
        LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.responsible_id
        WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.archived_at IS NULL""",(tenant_id,task_id))
    return cur.fetchone()

def create_task(tenant_id,deal_id,text,responsible_id,*,due_at=None,created_by=None,idempotency_key=None):
    deal_id=_id(deal_id,'deal id'); text=_text(text)
    if not text:raise ValueError('invalid task text')
    due=_due(due_at); key='manual:'+_id(idempotency_key,'request id')
    created_by=int(created_by) if created_by else None
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            deal=_deal(cur,tenant_id,deal_id); assignee=_assignee(cur,tenant_id,responsible_id)
            cur.execute("""INSERT INTO saas_crm_native_tasks
                (id,tenant_id,deal_id,contact_id,channel_id,conversation_id,text,due_at,responsible_id,created_by,idempotency_key)
                VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,idempotency_key) WHERE idempotency_key<>'' DO NOTHING RETURNING id""",
                (str(uuid.uuid4()),tenant_id,deal_id,deal['contact_id'],deal['channel_id'],deal['conversation_id'],text,due,assignee,created_by,key))
            inserted=cur.fetchone()
            if inserted:task_id=str(inserted['id'])
            else:
                cur.execute("SELECT id,deal_id,text,responsible_id,due_at,created_by,archived_at FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND idempotency_key=%s",(tenant_id,key))
                previous=cur.fetchone()
                if not previous or (str(previous['deal_id']),previous['text'],previous['responsible_id'],previous['due_at'],previous['created_by'])!=(deal_id,text,assignee,due,created_by):raise ValueError('idempotency key conflict')
                if previous['archived_at'] is not None:raise ValueError('idempotent task is archived')
                task_id=str(previous['id'])
            result=_row(cur,tenant_id,task_id)
        conn.commit()
    return result

def list_tasks(tenant_id,*,search='',responsible_id=None,scope=None,limit=100):
    q='%'+str(search or '').strip()[:120].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    args=[tenant_id,q,q]
    clause='t.tenant_id=%s::uuid AND t.archived_at IS NULL AND (t.text ILIKE %s ESCAPE \'\\\' OR d.title ILIKE %s ESCAPE \'\\\')'
    if responsible_id is not None:clause+=' AND (t.responsible_id=%s OR t.created_by=%s)';args.extend([int(responsible_id),int(responsible_id)])
    if scope is not None:
        parts=[]
        for item in scope:
            part='d.pipeline_id=%s';args.append(str(item['pipeline_id']))
            if item.get('status_ids'):
                part+=' AND d.stage_id=ANY(%s)';args.append([str(value) for value in item['status_ids']])
            parts.append('('+part+')')
        clause+=' AND ('+' OR '.join(parts)+')' if parts else ' AND FALSE'
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT t.id,t.deal_id,d.title AS deal_title,t.contact_id,c.display_name AS contact_name,
                t.channel_id,t.conversation_id,t.text,t.due_at,t.responsible_id,m.display_name AS responsible_name,
                t.status,t.completed_at,t.created_by,t.created_at,t.updated_at FROM saas_crm_native_tasks t
                JOIN saas_crm_native_deals d ON d.tenant_id=t.tenant_id AND d.id=t.deal_id
                LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=t.tenant_id AND c.id=t.contact_id
                LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.responsible_id
                WHERE {clause} ORDER BY (t.status='open') DESC,t.due_at ASC NULLS LAST,t.created_at DESC LIMIT %s""",[*args,max(1,min(int(limit),200))])
            return cur.fetchall()

def task_options(tenant_id,actor_id,privileged=False):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if privileged:
                cur.execute("SELECT id,title,contact_id,channel_id,conversation_id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND archived_at IS NULL ORDER BY updated_at DESC LIMIT 200",(tenant_id,))
                deals=cur.fetchall()
                cur.execute("SELECT telegram_id,display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE AND (role='owner' OR permissions @> '[\"tasks\"]'::jsonb) ORDER BY display_name",(tenant_id,))
                members=cur.fetchall()
            else:
                cur.execute("SELECT id,title,contact_id,channel_id,conversation_id FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND archived_at IS NULL AND responsible_id=%s ORDER BY updated_at DESC LIMIT 200",(tenant_id,int(actor_id)))
                deals=cur.fetchall();members=[]
    return {'deals':deals,'members':members}

def get_task(tenant_id,task_id):
    task_id=_id(task_id,'task id')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:return _row(cur,tenant_id,task_id)

def update_task(tenant_id,task_id,text,responsible_id,*,due_at=None):
    task_id=_id(task_id,'task id'); text=_text(text)
    if not text:raise ValueError('invalid task text')
    due=_due(due_at)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT deal_id FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE",(tenant_id,task_id))
            task=cur.fetchone()
            if not task: return None
            _deal(cur,tenant_id,str(task['deal_id']));assignee=_assignee(cur,tenant_id,responsible_id)
            cur.execute("UPDATE saas_crm_native_tasks SET text=%s,responsible_id=%s,due_at=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(text,assignee,due,tenant_id,task_id))
            row=_row(cur,tenant_id,task_id)
        conn.commit()
    return row

def set_task_state(tenant_id,task_id,status):
    if status not in {'open','completed'}:raise ValueError('invalid task status')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_crm_native_tasks SET status=%s,completed_at=CASE WHEN %s='completed' THEN COALESCE(completed_at,now()) ELSE NULL END,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(status,status,tenant_id,_id(task_id,'task id')))
            row=cur.fetchone()
            result=_row(cur,tenant_id,task_id) if row else None
        conn.commit()
    return result

def archive_task(tenant_id,task_id):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_crm_native_tasks SET archived_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,_id(task_id,'task id')))
            result=cur.fetchone() is not None
        conn.commit()
    return result

def maybe_create_stage_task(cur,tenant_id,deal,*,event_key):
    """Owner-configured trigger. Off unless that exact stage opts in."""
    cur.execute("SELECT settings FROM saas_tenant_pipeline_stages WHERE tenant_id=%s::uuid AND pipeline_id=%s AND stage_id=%s",(tenant_id,deal['pipeline_id'],deal['stage_id']))
    row=cur.fetchone(); settings=(row or {}).get('settings') or {}
    if not isinstance(settings,dict):return None
    trigger=settings.get('native_task') or {}
    if not isinstance(trigger,dict) or trigger.get('enabled') is not True or not str(trigger.get('text') or '').strip():return None
    assignee=trigger.get('responsible_id') or deal.get('responsible_id')
    if not assignee:return None
    try:days=max(0,min(int(trigger.get('due_days',1)),365))
    except (ValueError,TypeError):return None
    due=datetime.now(timezone.utc).replace(microsecond=0)
    text=_text(trigger.get('text'))
    if not text:return None
    try:assignee=_assignee(cur,tenant_id,assignee)
    except (ValueError,TypeError):return None
    event_key='workflow:'+hashlib.sha256(str(event_key).encode('utf-8')).hexdigest()
    cur.execute("""INSERT INTO saas_crm_native_tasks(id,tenant_id,deal_id,contact_id,channel_id,conversation_id,text,due_at,responsible_id,created_by,idempotency_key)
        VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,NULL,%s)
        ON CONFLICT(tenant_id,idempotency_key) WHERE idempotency_key<>'' DO NOTHING RETURNING id""",
        (str(uuid.uuid4()),tenant_id,str(deal['id']),deal.get('contact_id'),deal.get('channel_id'),deal.get('conversation_id') or '',text,due+timedelta(days=days),int(assignee),event_key[:240]))
    return cur.fetchone()
