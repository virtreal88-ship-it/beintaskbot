"""Due reminders shared on the explicitly configured operator sources."""
from datetime import datetime, timezone
import os

from psycopg.errors import LockNotAvailable, QueryCanceled

from tenant_dialog_access import require_source, sql_filter
from tenant_dialog_lifecycle import enabled as lifecycle_enabled, _row
from tenant_dialog_presence import is_effectively_online, work_status_routing_sources
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_personal_dialog_reminders import SessionUnavailable, _route, _time, _uuid
from tenant_schema_preflight import ready

ENABLE = 'TENANT_SHARED_DIALOG_REMINDERS_ENABLED'
TABLE = 'saas_shared_dialog_reminders'
PENDING = 'saas_shared_dialog_reminders_pending'
MAX_VISIBLE = 100


def configured():
    return os.environ.get(ENABLE) == '1'


def enabled(tenant):
    return configured() and bool(work_status_routing_sources(tenant))


def creation_capability(policy):
    """A shared reminder is a separate action on an already visible dialogue."""
    # The private reminder flag is independent of shared reminders.
    from tenant_scoped_access import ACTIVE_TENANT_STATUSES
    profile = policy.profile
    scope = policy.action_scope('dialogs', 'view')
    return (enabled(profile.get('tenant_id')) and profile.get('active') is True
            and profile.get('tenant_status', 'active') in ACTIVE_TENANT_STATUSES
            and (scope != 'none' if scope is not None else policy.legacy_allows('dialogs')))


def capability(policy):
    # Receiving and clearing team reminders stays operator-only.
    return policy.profile.get('role') == 'operator' and creation_capability(policy)


def schema_ready(cur):
    columns = ('tenant_id','id','channel_id','conversation_id','lead_id','created_by',
               'note','due_at','opened_at','opened_by')
    if not ready(cur,{TABLE:columns},(PENDING,)):
        return False
    cur.execute("""SELECT 1 FROM pg_index WHERE indexrelid=to_regclass(%s)
        AND indrelid=to_regclass(%s) AND indisunique AND indisready AND indislive
        AND pg_get_expr(indpred,indrelid)='(opened_at IS NULL)'
        AND pg_get_indexdef(indexrelid,1,true)='tenant_id'
        AND pg_get_indexdef(indexrelid,2,true)='channel_id'
        AND pg_get_indexdef(indexrelid,3,true)='conversation_id'
        AND pg_get_indexdef(indexrelid,4,true)='lead_id' AND indnkeyatts=4
        AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=indrelid AND contype='p'
                     AND convalidated AND pg_get_constraintdef(oid)='PRIMARY KEY (tenant_id, id)')""", (PENDING,TABLE))
    return bool(cur.fetchone())


def provision_schema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_shared_dialog_reminders (
        tenant_id UUID NOT NULL,
        id UUID NOT NULL,
        channel_id UUID NOT NULL,
        conversation_id TEXT NOT NULL,
        lead_id BIGINT NOT NULL CHECK (lead_id>0),
        created_by BIGINT NOT NULL,
        note TEXT NOT NULL CHECK (length(note)<=280),
        due_at TIMESTAMPTZ NOT NULL,
        opened_at TIMESTAMPTZ,
        opened_by BIGINT,
        CHECK ((opened_at IS NULL)=(opened_by IS NULL)),
        PRIMARY KEY(tenant_id,id)
    )""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_shared_dialog_reminders_pending
        ON saas_shared_dialog_reminders(tenant_id,channel_id,conversation_id,lead_id)
        WHERE opened_at IS NULL""")
    if not schema_ready(cur):
        raise RuntimeError('Shared reminder schema is unverified.')


def _public_route(channel, conversation, lead):
    return {'provider':'connect','channel_id':str(channel),'conversation_id':conversation,'lead_id':lead}


def _targets(cur, tenant, actor, policy):
    sources=sorted(work_status_routing_sources(tenant))
    online=is_effectively_online(cur,tenant,actor)
    view=sql_filter(policy,"'connect'",'r.channel_id::text','view')
    cur.execute(f"""SELECT r.id::text,r.channel_id::text,r.conversation_id,r.lead_id,r.due_at,
        COALESCE(NULLIF(c.manual_display_name,''),NULLIF(c.display_name,''),NULLIF(c.contact_name,''),'') AS title,
        r.due_at<=CURRENT_TIMESTAMP AS due,w.operator_id=%s AS may_open_dialog
        FROM {TABLE} r
        JOIN saas_connect_conversations c ON (c.tenant_id,c.connect_channel_id,c.conversation_id,c.lead_id)
            =(r.tenant_id,r.channel_id,r.conversation_id,r.lead_id)
        JOIN saas_connect_channels s ON (s.tenant_id,s.connect_channel_id)=(r.tenant_id,r.channel_id)
        JOIN saas_dialog_workflows w ON (w.tenant_id,w.channel_id,w.conversation_id)
            =(r.tenant_id,r.channel_id,r.conversation_id)
        WHERE r.tenant_id=%s::uuid AND r.channel_id=ANY(%s::uuid[]) AND s.active
          AND r.opened_at IS NULL AND ({view})
          AND ((w.state='accepted' AND w.operator_id=%s)
            OR (w.state IN ('closed','dismissed') AND %s))
        ORDER BY r.due_at,r.id LIMIT %s""", (actor,tenant,sources,actor,online,MAX_VISIBLE+1))
    rows=cur.fetchall()
    return {'reminders':[{'id':r['id'],'title':r['title'],'due_at':r['due_at'].isoformat(),'due':r['due'],
        **({'dialog':_public_route(r['channel_id'],r['conversation_id'],r['lead_id'])} if r['may_open_dialog'] else {})}
        for r in rows[:MAX_VISIBLE]], 'has_more':len(rows)>MAX_VISIBLE}


def _may_schedule(policy, route):
    return creation_capability(policy) and route.get('provider')=='connect' and str(route.get('channel_id')) in work_status_routing_sources(policy.profile['tenant_id'])


def dialogue_marker(cur, tenant, actor, policy, route, *, workflow, source_active,
                    exact_history, view='', include_delivery=False):
    """Project creation after a verified history read; never acknowledge a reminder."""
    if not _may_schedule(policy,route):
        return None
    states=('accepted','closed','dismissed')
    state=route.get('lifecycle') or {}
    can_create=bool(exact_history and source_active and workflow and workflow.get('state') in states)
    can_mark=(include_delivery and capability(policy) and state.get('state') in states
              and state.get('operator_id')==actor)
    if not (can_create or can_mark) or not schema_ready(cur):
        return None
    result={'can_schedule':can_create}
    if can_create and view:
        result['schedule_view']=view
    if not can_mark or (state['state']!='accepted' and not is_effectively_online(cur,tenant,actor)):
        return result
    channel,conversation,lead=str(route['channel_id']),route['conversation_id'],route['lead_id']
    cur.execute(f"""SELECT id::text FROM {TABLE} WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
        AND conversation_id=%s AND lead_id=%s AND opened_at IS NULL AND due_at<=CURRENT_TIMESTAMP""",
        (tenant,channel,conversation,lead))
    row=cur.fetchone()
    return {**result,**({'id':row['id']} if row else {})}


def timeline_page(conn, cur, tenant, channel, conversation, lead, **message_window):
    """Due notes in exact authorized chat history, independent of badge reads."""
    if not configured() or str(channel) not in work_status_routing_sources(tenant):
        return {}
    from tenant_dialog_operator_timeline import history_window
    try:
        # Bounded optional read, separate from lifecycle audit backoff. A
        # blocked reminder relation must not suppress the rest of the chat.
        with conn.transaction():
            cur.execute("""SELECT current_setting('statement_timeout') AS previous,setting::integer AS milliseconds
                FROM pg_settings WHERE name='statement_timeout'""")
            setting=cur.fetchone()
            budget=min(setting['milliseconds'] or 200,200)
            cur.execute("SELECT set_config('statement_timeout',%s,true)",(f"{budget}ms",))
            rows=[]
            if ready(cur,{TABLE:('tenant_id','id','channel_id','conversation_id','lead_id','note','due_at')},()):
                window=history_window(cur,tenant,conversation,**message_window)
                if window is not None:
                    lower,upper=window
                    cur.execute(f"""SELECT id::text,note,due_at FROM {TABLE}
                        WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s
                          AND due_at<=CURRENT_TIMESTAMP AND note ~ '[^[:space:]]'
                          AND (%s::timestamptz IS NULL OR due_at>=%s::timestamptz)
                          AND (%s::timestamptz IS NULL OR due_at<%s::timestamptz)
                        ORDER BY due_at DESC,id DESC LIMIT %s""",
                        (tenant,channel,conversation,lead,lower,lower,upper,upper,MAX_VISIBLE+1))
                    rows=cur.fetchall()
            cur.execute("SELECT set_config('statement_timeout',%s,true)",(setting['previous'],))
    except (LockNotAvailable, QueryCanceled):
        return {}
    shown=rows[:MAX_VISIBLE]
    result={'shared_reminder_events':[
        {'id':row['id'],'note':row['note'],'at':row['due_at'].isoformat()} for row in reversed(shown)]}
    if len(rows)>MAX_VISIBLE:
        result['shared_reminder_events_omitted_at']=shown[-1]['due_at'].isoformat()
    return result


def _missing():
    return TenantLinearError('Общее напоминание недоступно.',404)


def _schedule(cur, tenant, actor, policy, ident, data):
    fields={'action','id','note','due_at','provider','channel_id','conversation_id','lead_id'}
    view=data.get('history_view','')
    if view=='owner':
        fields.add('history_view')
        if policy.profile.get('role')!='owner':
            raise _missing()
    elif view=='search':
        fields.update(('history_view','search'))
        search=data.get('search')
        if not isinstance(search,str) or not 0<len(search.strip())<=200:
            raise ValueError('invalid search context')
    elif view!='':
        raise ValueError('invalid history context')
    if set(data)!=fields:
        raise ValueError('invalid schedule')
    provider,channel,conversation,lead=_route(data)
    if not _may_schedule(policy,_public_route(channel,conversation,lead)):
        raise _missing()
    note=data['note']
    if not isinstance(note,str) or len(note.strip())>280 or any(ord(ch)<32 and ch not in '\t\n\r' for ch in note):
        raise ValueError('invalid note')
    due=_time(data['due_at'])
    # Same exact-route visibility as history, never an update/send grant.
    # Preserve the view-source then conversation lock order used by history.
    require_source(cur,tenant,policy,provider,channel,'view')
    cur.execute("""SELECT 1 FROM saas_connect_channels
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active FOR SHARE""",(tenant,channel))
    if not cur.fetchone():
        raise _missing()
    assert_route(cur,tenant,policy,provider,conversation,channel,lead,allow_closed_archive=True,
                 owner_overview=view=='owner',search_overview=view=='search')
    from tenant_connect_dialog_equivalence import canonical
    if canonical(cur,tenant,channel,conversation)!=conversation:
        raise _missing()
    if view=='search':
        # Do not accept a caller's unchecked cross-owner marker. Revalidate the
        # same exact current search match used by the normal history endpoint.
        from tenant_operator_dialogs import search_overview_matches
        if not search_overview_matches(cur,tenant,policy,channel,conversation,lead,search):
            raise _missing()
    row=_row(cur,tenant,channel,conversation)
    if not row or row['state'] not in ('accepted','closed','dismissed'):
        raise _missing()
    cur.execute(f"""SELECT channel_id::text,conversation_id,lead_id,created_by,note,due_at
        FROM {TABLE} WHERE tenant_id=%s::uuid AND id=%s::uuid""",(tenant,ident))
    existing=cur.fetchone()
    if existing:
        if tuple(existing.values())!=(channel,conversation,lead,actor,note.strip(),due):
            raise TenantLinearError('Запрос изменился. Создайте новое напоминание.',409)
        return {'id':ident}
    if due<=datetime.now(timezone.utc):
        raise TenantLinearError('Выберите время в будущем.',400)
    cur.execute(f"""INSERT INTO {TABLE}(tenant_id,id,channel_id,conversation_id,lead_id,created_by,note,due_at)
        VALUES (%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
        (tenant,ident,channel,conversation,lead,actor,note.strip(),due))
    if not cur.fetchone():
        raise TenantLinearError('У диалога уже есть общее напоминание.',409)
    return {'id':ident}


def _open(cur, tenant, actor, policy, ident):
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_connect_dialog_equivalence import canonical
    cur.execute(f"""SELECT channel_id::text,conversation_id,lead_id FROM {TABLE}
        WHERE tenant_id=%s::uuid AND id=%s::uuid AND due_at<=CURRENT_TIMESTAMP""",(tenant,ident))
    route=cur.fetchone()
    if not route or route['channel_id'] not in work_status_routing_sources(tenant):
        raise _missing()
    channel,conversation,lead=route['channel_id'],route['conversation_id'],route['lead_id']
    try:
        require_source(cur,tenant,policy,'connect',channel,'view')
    except TenantLinearError:
        raise _missing() from None
    lock_dialog(cur,tenant,'connect',channel,conversation)
    if canonical(cur,tenant,channel,conversation)!=conversation:
        raise _missing()
    cur.execute("""SELECT COALESCE(NULLIF(c.manual_display_name,''),NULLIF(c.display_name,''),NULLIF(c.contact_name,''),'') AS title
        FROM saas_connect_conversations c JOIN saas_connect_channels s
        ON (s.tenant_id,s.connect_channel_id)=(c.tenant_id,c.connect_channel_id)
        WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s
          AND c.lead_id=%s AND s.active""",(tenant,channel,conversation,lead))
    chat=cur.fetchone()
    workflow=_row(cur,tenant,channel,conversation)
    if not chat or not workflow:
        raise _missing()
    own=workflow['operator_id']==actor
    if workflow['state']=='accepted':
        eligible=own
    else:
        eligible=workflow['state'] in ('closed','dismissed') and is_effectively_online(cur,tenant,actor)
    if not eligible:
        raise _missing()
    cur.execute(f"""UPDATE {TABLE} SET opened_at=COALESCE(opened_at,clock_timestamp()),opened_by=%s
        WHERE tenant_id=%s::uuid AND id=%s::uuid AND (opened_at IS NULL OR opened_by=%s)
        RETURNING note,due_at""",(actor,tenant,ident,actor))
    row=cur.fetchone()
    if not row:
        raise _missing()
    return {'id':ident,'title':chat['title'],'note':row['note'],'due_at':row['due_at'].isoformat()}


def execute(profile,data=None):
    if not enabled(profile.get('tenant_id')):
        raise _missing()
    from tenant_platform import _connect
    with _connect() as conn, conn.cursor() as cur:
        try:
            tenant,actor,policy=access(cur,profile)
        except TenantLinearError as error:
            if error.status==403:
                raise SessionUnavailable(str(error),403) from error
            raise
        allowed=creation_capability if data is not None and data.get('action')=='schedule' else capability
        if not allowed(policy) or not lifecycle_enabled(cur,tenant):
            raise _missing()
        if not schema_ready(cur):
            raise TenantLinearError('Общие напоминания временно недоступны.',503)
        if data is None:
            return _targets(cur,tenant,actor,policy)
        ident=_uuid(data['id'])
        if data['action']=='schedule':
            result=_schedule(cur,tenant,actor,policy,ident,data)
        elif data['action']=='open' and set(data)=={'action','id'}:
            result=_open(cur,tenant,actor,policy,ident)
        else:
            raise ValueError('invalid shared reminder action')
        conn.commit()
        return result
