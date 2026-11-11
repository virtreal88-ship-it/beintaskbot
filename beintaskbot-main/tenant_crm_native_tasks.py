"""Native, tenant-owned task lifecycle and stage-entry workflow hook."""
from __future__ import annotations
import uuid
import hashlib
from datetime import datetime,timedelta,timezone
from tenant_platform_core import _connect
from tenant_policy import TenantPolicy
from tenant_scoped_access import scoped_access_enabled, ACTIVE_TENANT_STATUSES
from tenant_scoped_access_store import resource_scope_sql
from tenant_member_access import page_capabilities
from tenant_native_task_access import allows as scoped_task_allowed, capabilities as task_capabilities, read_where

from tenant_workflow_store import _read_tenant_workflow


class NativeTaskAccessError(ValueError):
    def __init__(self,code,status=403):
        super().__init__(code)
        self.code,self.status=code,status


def _require_schema(conn):
    """Startup owns schema installation; task reads/writes never repair it."""
    with conn.cursor() as cur:
        cur.execute('''SELECT bool_and(to_regclass(name) IS NOT NULL) AS ready
          FROM unnest(%s::text[]) AS required(name)''',([
            'saas_tenants','saas_tenant_members','saas_tenant_pipelines',
            'saas_tenant_pipeline_stages','saas_tenant_workflow_policies',
            'saas_crm_native_tasks','saas_crm_native_deals','saas_crm_native_contacts'],))
        if not (cur.fetchone() or {}).get('ready'):
            raise NativeTaskAccessError('task_schema_unavailable',503)


def _tenant(cur,tenant_id,*,lock=False):
    cur.execute('SELECT id,modules,status FROM saas_tenants WHERE id=%s::uuid'+(' FOR SHARE' if lock else ''),(tenant_id,))
    row=cur.fetchone()
    if not row or row.get('status','active') not in ACTIVE_TENANT_STATUSES:raise NativeTaskAccessError('task_access_revoked')
    return row


def _profiles(cur,tenant_id,company,ids=None,*,lock=False):
    # Workflow writes update these policies before memberships. Notifications
    # and unrelated policy rows are deliberately outside this fence.
    if lock:
        cur.execute('''SELECT policy_key FROM saas_tenant_workflow_policies
          WHERE tenant_id=%s::uuid AND policy_key=ANY(%s::text[])
          ORDER BY policy_key FOR SHARE''',(tenant_id,['members','modules']))
        cur.fetchall()
    args=[tenant_id]
    clause=''
    if ids is not None:
        clause=' AND telegram_id=ANY(%s::bigint[])';args.append(sorted(set(ids)))
    cur.execute('''SELECT tenant_id,telegram_id,display_name,role,permissions,active,department_id
      FROM saas_tenant_members WHERE tenant_id=%s::uuid'''+clause+
      ' ORDER BY telegram_id'+(' FOR SHARE' if lock else ''),args)
    rows=cur.fetchall()
    # Read pipeline/stage visibility at the admission point, after a pending
    # membership/policy writer has committed. No stage locks invert FK checks.
    workflow=_read_tenant_workflow(cur,tenant_id)
    return {int(row['telegram_id']):{**dict(row),'tenant_id':str(tenant_id),
        'permissions':row['permissions'] if isinstance(row['permissions'],list) else [],
        'modules':company.get('modules') or {},'tenant_status':company.get('status','active'),'workflow':workflow} for row in rows}


def _actor(profiles,actor_id,action):
    policy=TenantPolicy(profiles.get(int(actor_id)) or {})
    # API creator authority retains its existing granular-action contract.
    # Work eligibility separately requires a visible Tasks page and view/update.
    if not policy.allows_action('tasks',action):
        raise NativeTaskAccessError('task_access_revoked')
    return policy


def _in_manager_scope(policy,deal):
    return any(str(deal.get('pipeline_id'))==str(item['pipeline_id'])
      and (not item['status_ids'] or str(deal.get('stage_id')) in {str(value) for value in item['status_ids']})
      for item in policy.pipeline_scope())


def _eligible(profile,deal):
    policy=TenantPolicy(profile or {})
    if profile and policy.has_scoped_access('tasks'):
        return profile.get('active') is True
    return bool(profile and page_capabilities(profile)['tasks'] and policy.allows_action('tasks','view')
        and policy.allows_action('tasks','update')
        and (profile.get('role')!='manager' or _in_manager_scope(policy,deal)))


def task_access_allowed(policy,row,actor_id,operation='view'):
    return scoped_task_allowed(policy,row,actor_id,operation)


def _creation_deal_allowed(policy,deal,actor_id):
    # Creating a new task/deal link is not permission to enumerate an otherwise
    # closed deal section. Preserve legacy creation restrictions as an additional
    # condition; related data in an already-authorized task is handled separately.
    if policy.has_scoped_access('deals','view') and not policy.allows_resource('deals','view',{
            **deal,'responsible_member_id':deal.get('access_responsible_id'),
            'responsible_department_id':deal.get('access_responsible_department_id')}):
        return False
    return bool(policy.privileged or (int(deal.get('responsible_id') or 0)==int(actor_id)
      and (policy.profile.get('role')!='manager' or _in_manager_scope(policy,deal))))


def _admit(cur,tenant_id,company,deal,responsible_id,actor_id=None,*,task=None,action='create'):
    ids=[int(responsible_id)]
    if actor_id is not None:ids.append(int(actor_id))
    if task and task.get('access_responsible_id'):ids.append(int(task['access_responsible_id']))
    if task is None and deal.get('access_responsible_id'):ids.append(int(deal['access_responsible_id']))
    profiles=_profiles(cur,tenant_id,company,ids,lock=True)
    if actor_id is not None:
        if task is None:
            deal_owner=profiles.get(int(deal.get('access_responsible_id') or 0)) or {}
            deal={**deal,'access_responsible_department_id':deal_owner.get('department_id')}
        policy=_actor(profiles,actor_id,action)
        if task is not None:
            owner=profiles.get(int(task.get('access_responsible_id') or 0)) or {}
            current={**deal,**task,'tenant_id':str(tenant_id),
                'access_responsible_department_id':owner.get('department_id')}
            if not task_access_allowed(policy,current,actor_id,action):
                raise NativeTaskAccessError('task_unavailable',404)
        elif not _creation_deal_allowed(policy,deal,actor_id):
            raise NativeTaskAccessError('deal_unavailable',404)
        if task is None and policy.has_scoped_access('tasks','create') and not policy.allows_resource('tasks','create',
                {'tenant_id':str(tenant_id),'responsible_member_id':int(actor_id),
                 'responsible_department_id':profiles[int(actor_id)].get('department_id')}):
            raise NativeTaskAccessError('task_access_revoked')
        # Creation always owns the new document. Assignment scope is a separate
        # unresolved policy: preserve the existing self-only non-admin rule.
        changing_assignee=not scoped_access_enabled() or task is None or int(task['responsible_id'])!=int(responsible_id)
        if changing_assignee and not policy.privileged and int(responsible_id)!=int(actor_id):
            raise NativeTaskAccessError('forbidden_assignment')
        if action=='create' and not policy.privileged and policy.requires_task_approval(
                creator_id=int(actor_id),executor_id=int(responsible_id)):
            raise NativeTaskAccessError('approval_required')
    # An unchanged assigned member need not retain generic task update rights.
    if scoped_access_enabled() and task and int(task['responsible_id'])==int(responsible_id):
        return int(responsible_id)
    return _assignee(cur,tenant_id,responsible_id,deal=deal,profiles=profiles)


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

def _assignee(cur,tenant_id,responsible_id,*,deal=None,profiles=None):
    try:person=int(responsible_id)
    except (ValueError,TypeError):raise ValueError('invalid responsible') from None
    if profiles is None:
        company=_tenant(cur,tenant_id,lock=True)
        profiles=_profiles(cur,tenant_id,company,[person],lock=True)
    if not _eligible(profiles.get(person),deal or {}):
        raise NativeTaskAccessError('assignee_unavailable',409)
    return person

def _deal(cur,tenant_id,deal_id,*,lock=True):
    cur.execute("""SELECT d.id,d.title,d.contact_id,d.responsible_id,d.channel_id,d.conversation_id,d.pipeline_id,d.stage_id,
        d.tenant_id,d.access_responsible_id,owner.department_id AS access_responsible_department_id
        FROM saas_crm_native_deals d
        LEFT JOIN saas_tenant_members owner ON owner.tenant_id=d.tenant_id AND owner.telegram_id=d.access_responsible_id
        WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.archived_at IS NULL"""+(' FOR UPDATE OF d' if lock else ''),(tenant_id,deal_id))
    row=cur.fetchone()
    if not row:raise NativeTaskAccessError('deal_unavailable',404)
    return row


def _row(cur,tenant_id,task_id):
    cur.execute("""SELECT t.id,t.deal_id,d.title AS deal_title,t.contact_id,c.display_name AS contact_name,
        t.channel_id,t.conversation_id,t.text,t.due_at,t.responsible_id,m.display_name AS responsible_name,
        t.status,t.completed_at,t.created_by,t.created_at,t.updated_at,d.pipeline_id,d.stage_id,
        t.tenant_id,t.access_responsible_id,owner.department_id AS access_responsible_department_id
        FROM saas_crm_native_tasks t JOIN saas_crm_native_deals d ON d.tenant_id=t.tenant_id AND d.id=t.deal_id
        LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=t.tenant_id AND c.id=t.contact_id
        LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.responsible_id
        LEFT JOIN saas_tenant_members owner ON owner.tenant_id=t.tenant_id AND owner.telegram_id=t.access_responsible_id
        WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.archived_at IS NULL""",(tenant_id,task_id))
    return cur.fetchone()

def create_task(tenant_id,deal_id,text,responsible_id,*,due_at=None,created_by=None,idempotency_key=None,actor_id=None):
    deal_id=_id(deal_id,'deal id'); text=_text(text)
    if not text:raise ValueError('invalid task text')
    due=_due(due_at); key='manual:'+_id(idempotency_key,'request id')
    created_by=int(created_by) if created_by else None
    actor_id=int(actor_id) if actor_id is not None else created_by
    if actor_id is not None and created_by!=actor_id:raise NativeTaskAccessError('task_access_revoked')
    with _connect() as conn:
        _require_schema(conn)
        with conn.cursor() as cur:
            company=_tenant(cur,tenant_id,lock=True)
            deal=_deal(cur,tenant_id,deal_id)
            assignee=_admit(cur,tenant_id,company,deal,responsible_id,actor_id)
            cur.execute("""INSERT INTO saas_crm_native_tasks
                (id,tenant_id,deal_id,contact_id,channel_id,conversation_id,text,due_at,responsible_id,created_by,access_responsible_id,idempotency_key)
                VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(tenant_id,idempotency_key) WHERE idempotency_key<>'' DO NOTHING RETURNING id""",
                (str(uuid.uuid4()),tenant_id,deal_id,deal['contact_id'],deal['channel_id'],deal['conversation_id'],text,due,assignee,created_by,created_by,key))
            inserted=cur.fetchone()
            if inserted:task_id=str(inserted['id'])
            else:
                cur.execute("SELECT id,deal_id,text,responsible_id,due_at,created_by,archived_at FROM saas_crm_native_tasks WHERE tenant_id=%s::uuid AND idempotency_key=%s",(tenant_id,key))
                previous=cur.fetchone()
                if not previous or (str(previous['deal_id']),previous['text'],previous['responsible_id'],previous['due_at'],previous['created_by'])!=(deal_id,text,assignee,due,created_by):raise ValueError('idempotency key conflict')
                if previous['archived_at'] is not None:raise ValueError('idempotent task is archived')
                task_id=str(previous['id'])
            result=_row(cur,tenant_id,task_id)
            if actor_id is not None:
                result['capabilities']=task_capabilities(TenantPolicy(_profiles(cur,tenant_id,company,[actor_id])[actor_id]),result,actor_id)
        conn.commit()
    return result

def list_tasks(tenant_id,*,search='',responsible_id=None,scope=None,limit=100,actor_id=None):
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
        _require_schema(conn)
        with conn.cursor() as cur:
            policy=None
            if actor_id is not None:
                company=_tenant(cur,tenant_id,lock=True)
                profiles=_profiles(cur,tenant_id,company,lock=True)
                profile=profiles.get(int(actor_id))
                if not profile or profile.get('active') is not True:raise NativeTaskAccessError('task_access_revoked')
                policy=TenantPolicy(profile)
                clause,args=read_where(profile)
                clause+=" AND (t.text ILIKE %s ESCAPE '\\' OR d.title ILIKE %s ESCAPE '\\')"
                args.extend([q,q])
            cur.execute(f"""SELECT t.id,t.deal_id,d.title AS deal_title,t.contact_id,c.display_name AS contact_name,
                t.channel_id,t.conversation_id,t.text,t.due_at,t.responsible_id,m.display_name AS responsible_name,
                t.status,t.completed_at,t.created_by,t.created_at,t.updated_at,d.pipeline_id,d.stage_id,
                t.tenant_id,t.access_responsible_id,owner.department_id AS access_responsible_department_id FROM saas_crm_native_tasks t
                JOIN saas_crm_native_deals d ON d.tenant_id=t.tenant_id AND d.id=t.deal_id
                LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=t.tenant_id AND c.id=t.contact_id
                LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.responsible_id
        LEFT JOIN saas_tenant_members owner ON owner.tenant_id=t.tenant_id AND owner.telegram_id=t.access_responsible_id
                WHERE {clause} ORDER BY (t.status='open') DESC,t.due_at ASC NULLS LAST,t.created_at DESC LIMIT %s""",[*args,max(1,min(int(limit),200))])
            rows=cur.fetchall()
            if policy:
                for row in rows:row['capabilities']=task_capabilities(policy,row,int(actor_id))
            return rows

def task_options(tenant_id,actor_id,privileged=False,*,deal_id=None,task_id=None):
    """The legacy privilege argument is not authority; resolve current rows."""
    actor_id=int(actor_id)
    with _connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL '+('READ COMMITTED' if scoped_access_enabled() else 'REPEATABLE READ'))
        _require_schema(conn)
        with conn.cursor() as cur:
            company=_tenant(cur,tenant_id,lock=scoped_access_enabled())
            profiles=_profiles(cur,tenant_id,company,lock=scoped_access_enabled())
            policy=TenantPolicy(profiles.get(actor_id) or {})
            if not profiles.get(actor_id) or profiles[actor_id].get('active') is not True:
                raise NativeTaskAccessError('task_access_revoked')
            action=('update' if task_id else 'create') if scoped_access_enabled() else 'view'
            if not policy.allows_action('tasks',action):raise NativeTaskAccessError('task_access_revoked')
            args=[tenant_id]
            scope=''
            if not policy.privileged:
                scope=' AND d.responsible_id=%s';args.append(actor_id)
                if policy.profile.get('role')=='manager':
                    parts=[]
                    for item in policy.pipeline_scope():
                        part='d.pipeline_id=%s';args.append(str(item['pipeline_id']))
                        if item['status_ids']:
                            part+=' AND d.stage_id=ANY(%s::text[])'
                            args.append([str(value) for value in item['status_ids']])
                        parts.append('('+part+')')
                    scope+=' AND ('+' OR '.join(parts)+')' if parts else ' AND FALSE'
            if policy.has_scoped_access('deals','view'):
                allowed,values=resource_scope_sql(policy.profile,'deals','view',alias='d')
                scope+=' AND ('+allowed+')';args.extend(values)
            cur.execute('''SELECT d.id,d.title,d.contact_id,d.channel_id,d.conversation_id,d.responsible_id,d.pipeline_id,d.stage_id
              FROM saas_crm_native_deals d WHERE d.tenant_id=%s::uuid AND d.archived_at IS NULL'''+scope+
              ' ORDER BY d.updated_at DESC,d.id LIMIT 200',args)
            deals=cur.fetchall()
            selected=None
            if task_id:
                task=_row(cur,tenant_id,_id(task_id,'task id'))
                if not task_access_allowed(policy,task,actor_id,'update' if scoped_access_enabled() else 'view'):raise NativeTaskAccessError('task_unavailable',404)
                selected=_deal(cur,tenant_id,str(task['deal_id']),lock=False)
                if deal_id and _id(deal_id,'deal id')!=str(selected['id']):raise NativeTaskAccessError('deal_unavailable',404)
            elif deal_id:
                # The current selection can legitimately be older than this
                # bounded picker window; authorize it independently.
                selected=_deal(cur,tenant_id,_id(deal_id,'deal id'),lock=False)
                if not _creation_deal_allowed(policy,selected,actor_id):
                    raise NativeTaskAccessError('deal_unavailable',404)
            elif deals:selected=deals[0]
            if selected and not any(str(row['id'])==str(selected['id']) for row in deals):
                deals=[selected,*deals[:199]]
            people=profiles.values() if policy.privileged else [profiles[actor_id]]
            members=[{'telegram_id':person['telegram_id'],'display_name':person['display_name']}
              for person in people if selected and _eligible(person,selected)]
            members.sort(key=lambda row:(row['display_name'],int(row['telegram_id'])))
    fields=('id','title','contact_id','channel_id','conversation_id')
    return {'deals':[{key:row.get(key) for key in fields} for row in deals], 'members':members,
      'selected_deal_id':str(selected['id']) if selected else None,
      'assignment':{'scope':'tenant' if policy.privileged else 'self','criterion':'active_tasks_assignment' if scoped_access_enabled() else 'active_tasks_view_update',
        'status':'ready' if members else 'no_eligible_members' if selected else 'no_deals',
        'can_manage_employees':policy.allows_action('employees','manage'),
        'settings_available':policy.allows('settings')}}

def get_task(tenant_id,task_id,*,actor_id=None,operation='view'):
    task_id=_id(task_id,'task id')
    with _connect() as conn:
        _require_schema(conn)
        with conn.cursor() as cur:
            row=_row(cur,tenant_id,task_id)
            if actor_id is not None:
                company=_tenant(cur,tenant_id,lock=True)
                profiles=_profiles(cur,tenant_id,company,lock=True)
                profile=profiles.get(int(actor_id))
                if not profile or profile.get('active') is not True:raise NativeTaskAccessError('task_access_revoked')
                # Re-read after the membership/department fence, not before it.
                row=_row(cur,tenant_id,task_id)
                policy=TenantPolicy(profile)
                if not task_access_allowed(policy,row,actor_id,operation):return None
                row['capabilities']=task_capabilities(policy,row,int(actor_id))
            return row


def _locked_task(cur,tenant_id,task_id):
    cur.execute('''SELECT id,tenant_id,deal_id,responsible_id,created_by,access_responsible_id,status,updated_at FROM saas_crm_native_tasks
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR UPDATE''',(tenant_id,task_id))
    return cur.fetchone()


def _check_version(task,expected_updated_at):
    if expected_updated_at is None:return
    if not isinstance(expected_updated_at,str) or not expected_updated_at:
        raise ValueError('invalid task version')
    current=task.get('updated_at')
    if not current or current.isoformat()!=expected_updated_at:
        raise NativeTaskAccessError('task_conflict',409)


def _task_actor(cur,tenant_id,company,task,deal,actor_id,action,*,completion=False,archive=False):
    if actor_id is None:return
    ids=[int(actor_id)]
    if task.get('access_responsible_id'):ids.append(int(task['access_responsible_id']))
    profiles=_profiles(cur,tenant_id,company,ids,lock=True)
    profile=profiles.get(int(actor_id))
    if not profile or profile.get('active') is not True:raise NativeTaskAccessError('task_access_revoked')
    policy=TenantPolicy(profile)
    owner=profiles.get(int(task.get('access_responsible_id') or 0)) or {}
    current={**deal,**task,'tenant_id':str(tenant_id),
        'access_responsible_department_id':owner.get('department_id')}
    operation='complete' if completion else 'delete' if archive else action
    if not task_access_allowed(policy,current,actor_id,operation):raise NativeTaskAccessError('task_unavailable',404)
    if not scoped_access_enabled() and completion and not policy.privileged and policy.requires_task_approval(
            creator_id=int(task.get('created_by') or 0),executor_id=int(task['responsible_id']),completion=True):
        raise NativeTaskAccessError('approval_required')


def update_task(tenant_id,task_id,text,responsible_id,*,due_at=None,actor_id=None,expected_updated_at=None):
    task_id=_id(task_id,'task id'); text=_text(text)
    if not text:raise ValueError('invalid task text')
    due=_due(due_at)
    with _connect() as conn:
        _require_schema(conn)
        with conn.cursor() as cur:
            company=_tenant(cur,tenant_id,lock=True)
            task=_locked_task(cur,tenant_id,task_id)
            if not task: return None
            deal=_deal(cur,tenant_id,str(task['deal_id']))
            assignee=_admit(cur,tenant_id,company,deal,responsible_id,actor_id,task=task,action='update')
            _check_version(task,expected_updated_at)
            cur.execute("UPDATE saas_crm_native_tasks SET text=%s,responsible_id=%s,due_at=%s,updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL",(text,assignee,due,tenant_id,task_id))
            row=_row(cur,tenant_id,task_id)
            if actor_id is not None:
                row['capabilities']=task_capabilities(TenantPolicy(_profiles(cur,tenant_id,company,[actor_id])[actor_id]),row,actor_id)
        conn.commit()
    return row

def set_task_state(tenant_id,task_id,status,*,actor_id=None,expected_updated_at=None):
    if status not in {'open','completed'}:raise ValueError('invalid task status')
    with _connect() as conn:
        _require_schema(conn)
        with conn.cursor() as cur:
            company=_tenant(cur,tenant_id,lock=True)
            task=_locked_task(cur,tenant_id,_id(task_id,'task id'))
            if not task:return None
            deal=_deal(cur,tenant_id,str(task['deal_id']))
            _task_actor(cur,tenant_id,company,task,deal,actor_id,'update',completion=status=='completed')
            _check_version(task,expected_updated_at)
            cur.execute("UPDATE saas_crm_native_tasks SET status=%s,completed_at=CASE WHEN %s='completed' THEN COALESCE(completed_at,now()) ELSE NULL END,updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(status,status,tenant_id,_id(task_id,'task id')))
            row=cur.fetchone()
            result=_row(cur,tenant_id,task_id) if row else None
            if result and actor_id is not None:
                result['capabilities']=task_capabilities(TenantPolicy(_profiles(cur,tenant_id,company,[actor_id])[actor_id]),result,actor_id)
        conn.commit()
    return result

def archive_task(tenant_id,task_id,*,actor_id=None,expected_updated_at=None):
    with _connect() as conn:
        _require_schema(conn)
        with conn.cursor() as cur:
            company=_tenant(cur,tenant_id,lock=True)
            task=_locked_task(cur,tenant_id,_id(task_id,'task id'))
            if not task:return False
            deal=_deal(cur,tenant_id,str(task['deal_id']))
            _task_actor(cur,tenant_id,company,task,deal,actor_id,'delete',archive=True)
            _check_version(task,expected_updated_at)
            cur.execute("UPDATE saas_crm_native_tasks SET archived_at=now(),updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL RETURNING id",(tenant_id,_id(task_id,'task id')))
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
    try:assignee=_assignee(cur,tenant_id,assignee,deal=deal)
    except (ValueError,TypeError):return None
    event_key='workflow:'+hashlib.sha256(str(event_key).encode('utf-8')).hexdigest()
    cur.execute("""INSERT INTO saas_crm_native_tasks(id,tenant_id,deal_id,contact_id,channel_id,conversation_id,text,due_at,responsible_id,created_by,idempotency_key)
        VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,NULL,%s)
        ON CONFLICT(tenant_id,idempotency_key) WHERE idempotency_key<>'' DO NOTHING RETURNING id""",
        (str(uuid.uuid4()),tenant_id,str(deal['id']),deal.get('contact_id'),deal.get('channel_id'),deal.get('conversation_id') or '',text,due+timedelta(days=days),int(assignee),event_key[:240]))
    return cur.fetchone()
