"""Opt-in, queued owner history joins. Neither routes nor messages are rewritten."""
import asyncio
import logging
import re
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, TenantPlatformError

from tenant_dialog_history_merge_scope import FLAG, MAX_ACTIVE_PAIRS, allows, configured, source_for
TABLE = 'saas_dialog_history_merge_jobs'
INDEXES = ('saas_dialog_history_merge_pending', 'saas_dialog_history_merge_active_source',
           'saas_dialog_history_merge_left', 'saas_dialog_history_merge_right',
           'saas_dialog_history_merge_pair', 'saas_dialog_history_merge_running')
COLUMNS = ('tenant_id','id','actor_id','channel_id','left_id','right_id','left_lead','right_lead',
           'state','step','code','created_at','updated_at','due_at','lease_token','lease_until',
           'attempts','principal_id','proof_generation','proof_gateway_id','proof_at',
           'display_name_lead','display_phone_lead','display_revision','display_name','display_phone')
PENDING = ('queued', 'running', 'verified')


def enabled():
    return configured() is not None


def ready(cur):
    from tenant_schema_preflight import ready as schema_ready
    return schema_ready(cur, {TABLE: COLUMNS}, INDEXES)


def projection_ready(cur):
    from tenant_schema_preflight import ready as schema_ready
    return schema_ready(cur, {'saas_crm_messages': ('tenant_id','kommo_lead_id','external_id','happened_at','created_at')},
                        ['saas_crm_messages_history_position'])


def provision(cur):
    """Additive fixed schema; called only from opt-in bounded boot."""
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_history_merge_jobs (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      id UUID NOT NULL, actor_id BIGINT NOT NULL, channel_id UUID NOT NULL,
      left_id TEXT NOT NULL, right_id TEXT NOT NULL,
      left_lead BIGINT NOT NULL, right_lead BIGINT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('queued','running','verified','completed','blocked','failed','cancelled','undone')),
      step SMALLINT NOT NULL DEFAULT 0 CHECK(step BETWEEN 0 AND 3),
      code TEXT NOT NULL DEFAULT '', attempts SMALLINT NOT NULL DEFAULT 0,
      created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      due_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      lease_token UUID, lease_until TIMESTAMPTZ,
      principal_id TEXT, proof_generation TEXT, proof_gateway_id UUID, proof_at TIMESTAMPTZ,
      display_name_lead BIGINT, display_phone_lead BIGINT, display_revision TEXT,
      display_name TEXT, display_phone TEXT,
      CHECK(display_name_lead IS NULL OR display_name_lead IN(left_lead,right_lead)),
      CHECK(display_phone_lead IS NULL OR display_phone_lead IN(left_lead,right_lead)),
      CHECK(display_revision IS NULL OR display_revision ~ '^[0-9a-f]{64}$'),
      CHECK(display_name IS NULL OR length(display_name) BETWEEN 1 AND 180),
      CHECK(display_phone IS NULL OR display_phone ~ '^[+][1-9][0-9]{6,14}$'),
      PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE,
      CHECK(left_id<>right_id AND left_lead<>right_lead),
      CHECK(state<>'completed' OR (step=3 AND principal_id IS NOT NULL AND proof_generation IS NOT NULL
                                  AND proof_gateway_id IS NOT NULL AND proof_at IS NOT NULL))
    )""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_history_merge_pending
      ON saas_dialog_history_merge_jobs(tenant_id,channel_id,due_at,id) WHERE state IN ('queued','running','verified')""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_dialog_history_merge_active_source
      ON saas_dialog_history_merge_jobs(tenant_id,channel_id) WHERE state IN ('queued','running','verified')""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_history_merge_pair
      ON saas_dialog_history_merge_jobs(tenant_id,actor_id,channel_id,left_lead,right_lead,created_at DESC)""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_history_merge_running
      ON saas_dialog_history_merge_jobs(tenant_id,channel_id,lease_until) WHERE state='running'""")
    for side in ('left','right'):
        cur.execute(f"""CREATE INDEX IF NOT EXISTS saas_dialog_history_merge_{side}
          ON saas_dialog_history_merge_jobs(tenant_id,channel_id,{side}_lead) WHERE state='completed'""")


def _budget(cur):
    cur.execute("SET LOCAL statement_timeout='1500ms'")
    cur.execute("SET LOCAL lock_timeout='350ms'")
    if getattr(getattr(cur.connection, 'info', None), 'server_version', 0) >= 170000:
        cur.execute("SET LOCAL transaction_timeout='3000ms'")


def available(profile):
    channel=source_for(profile.get('tenant_id'))
    if not channel or profile.get('role') != 'owner' or profile.get('active') is not True:
        return False
    try:
        with _connect() as conn, conn.cursor() as cur:
            _budget(cur)
            from tenant_connect_direct_reconcile import _owner
            tenant,_=_owner(cur, profile)
            from tenant_operator_access import access
            from tenant_dialog_access import require_source
            _,_,policy=access(cur,profile,'dialogs',action='update')
            require_source(cur,tenant,policy,'connect',channel,'update')
            if not allows(tenant,channel):
                return False
            cur.execute("SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active",
                        (tenant,channel))
            return bool(cur.fetchone() and ready(cur) and projection_ready(cur))
    except Exception:
        return False


def _uuid(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise TenantLinearError('Некорректный запрос.', 400) from None


def _pair(payload):
    from tenant_dialog_duplicate_actions import _route
    a, b = sorted((_route(payload['left']), _route(payload['right'])))
    if a[0] != b[0] or a[1] == b[1] or a[2] == b[2]:
        raise TenantLinearError('Выберите разные личные диалоги одного канала.', 400)
    return a[0], a[1], b[1], a[2], b[2]


def _key(job):
    return str(job['channel_id']),job['left_id'],job['right_id'],int(job['left_lead']),int(job['right_lead'])


def _public(job):
    if not job:
        return None
    state = 'running' if job['state'] == 'verified' else job['state']
    result = {'id':str(job['id']), 'state':state, 'processed':int(job['step']),
              'total':3, 'unit':'steps', 'code':job['code']}
    if state=='completed':
        lead=job['left_lead'] if job['principal_id']==job['left_id'] else job['right_lead']
        result['dialog']={'provider':'connect','channel_id':str(job['channel_id']),
                          'conversation_id':job['principal_id'],'lead_id':int(lead)}
    return result


def _current_result(cur,profile,job):
    result=_public(job)
    if result and result['state']=='completed':
        from tenant_operator_access import access
        from tenant_dialog_history_merge_projection import available_job
        tenant,_,policy=access(cur,profile,'dialogs')
        if not available_job(cur,tenant,policy,job):
            return {**result,'state':'blocked','code':'scope_changed','dialog':None}
    return result


def _exact(cur,profile,pair,*,write=False):
    from tenant_connect_direct_reconcile import _owner
    from tenant_operator_access import access, assert_route
    from tenant_dialog_access import require_source
    from tenant_dialog_history_merge_projection import lifecycle_enabled
    tenant,actor=_owner(cur,profile)
    channel,left,right,ll,rl=pair
    if not allows(tenant,channel):
        raise TenantLinearError('Операция недоступна.',404)
    _,_,policy=access(cur,profile,'dialogs')
    if write:
        _,_,up=access(cur,profile,'dialogs',action='update')
        require_source(cur,tenant,up,'connect',channel,'update')
    # Logical joins can be reviewed with the existing owner overview. This
    # does not delegate visibility to anyone else or permit route mutations.
    rows=[]
    for route,lead in ((left,ll),(right,rl)):
        selected=assert_route(cur,tenant,policy,'connect',route,channel,lead,allow_closed_archive=True,
                              owner_overview=lifecycle_enabled())
        if selected.get('conversation_kind') not in ('contact',''):
            raise TenantLinearError('Выберите личные WhatsApp-диалоги.', 400)
        rows.append(selected)
    return tenant,actor,rows


def _selection(payload,key):
    raw=payload.get('display')
    if 'display' not in payload:  # no choices supplied; never infer values
        return None,None,None
    if not isinstance(raw,dict) or set(raw)!={'name_lead_id','phone_lead_id','revision'}:
        raise TenantLinearError('Некорректный выбор отображения.',400)
    name,phone,revision=raw['name_lead_id'],raw['phone_lead_id'],raw['revision']
    if (not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{64}',revision)
            or any(x is not None and (type(x) is not int or x not in key[3:]) for x in (name,phone))):
        raise TenantLinearError('Некорректный выбор отображения.',400)
    return name,phone,revision


def _saved_selection(row):
    return row['display_name_lead'],row['display_phone_lead'],row['display_revision']


def _display_snapshot(cur,tenant,channel,rows,selection):
    name,phone,revision=selection
    if revision is None:
        return None,None
    from tenant_dialog_merge_display import merge_display_options
    options=merge_display_options(cur,tenant,channel,rows)
    if revision!=options['display_revision']:
        raise TenantLinearError('Варианты отображения изменились. Сравните диалоги повторно.',409)
    visible_names={n['lead_id']:n['display_name'] for n in options['name_choices']}
    visible_phones={p['lead_id']:p['display_phone'] for p in options['phone_choices']}
    if (name is not None and name not in visible_names) or (phone is not None and phone not in visible_phones):
        raise TenantLinearError('Вариант отображения недоступен.',409)
    return visible_names.get(name),visible_phones.get(phone)


def _at_capacity(cur,tenant,channel):
    # Replays and existing pairs always remain readable. Only new work is
    # rejected. The pending unique source slot serializes publication.
    cur.execute("""SELECT count(*) >= %s AS full FROM (
        SELECT 1 FROM saas_dialog_history_merge_jobs WHERE tenant_id=%s::uuid
           AND channel_id=%s::uuid AND state='completed' LIMIT %s
        ) AS capped""",(MAX_ACTIVE_PAIRS,tenant,channel,MAX_ACTIVE_PAIRS))
    return cur.fetchone()['full']


def _lookup(cur,tenant,actor,job_id,*,lock=False):
    channel=source_for(tenant)
    if not channel:
        return None
    cur.execute("""SELECT * FROM saas_dialog_history_merge_jobs
      WHERE tenant_id=%s::uuid AND actor_id=%s AND id=%s::uuid AND channel_id=%s::uuid"""+(" FOR UPDATE" if lock else ""),
      (tenant,actor,job_id,channel))
    return cur.fetchone()


def action(profile,payload):
    if not isinstance(profile,dict) or not source_for(profile.get('tenant_id')):
        raise TenantLinearError('Операция недоступна.',404)
    op=payload.get('operation')
    fields=({'operation','left','right','request_id'} if op=='create' else
            {'operation','left','right'} if op=='latest' else {'operation','job_id'})
    actual=set(payload)
    if op not in ('create','latest','status','cancel','undo') or (
            actual!=fields and not (op=='create' and actual==fields|{'display'})):
        raise TenantLinearError('Некорректный запрос.',400)
    from tenant_connect_direct_reconcile import _owner
    key=_pair(payload) if op in ('create','latest') else None
    if key and not allows(profile.get('tenant_id'),key[0]):
        raise TenantLinearError('Операция недоступна.',404)
    selection=_selection(payload,key) if op=='create' else None
    job_id=(_uuid(payload['request_id']) if op=='create'
            else _uuid(payload['job_id']) if op!='latest' else None)
    with _connect() as conn, conn.cursor() as cur:
        _budget(cur)
        tenant,actor=_owner(cur,profile)
        if not ready(cur):
            raise TenantLinearError('Операция пока недоступна.',503)
        if op in ('latest','create'):
            _,_,visible_rows=_exact(cur,profile,key,write=op=='create')
            if op=='create':
                # A single slot for this exact source, not per process. No queue growth from repeated clicks.
                cur.execute("""SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS ok""",
                            ('dialog-history-merge-submit:'+str(tenant),))
                if not cur.fetchone()['ok']:
                    raise TenantLinearError('Объединение уже подготавливается.',409)
                replay=_lookup(cur,tenant,actor,job_id)
                if replay:
                    if _key(replay)!=key or _saved_selection(replay)!=selection:
                        raise TenantLinearError('Этот запрос уже использован.',409)
                    return _current_result(cur,profile,replay)
            cur.execute("""SELECT * FROM saas_dialog_history_merge_jobs
              WHERE tenant_id=%s::uuid AND actor_id=%s AND channel_id=%s::uuid
              AND left_id=%s AND right_id=%s AND left_lead=%s AND right_lead=%s
              ORDER BY (state='completed') DESC,created_at DESC LIMIT 1""",(tenant,actor,*key))
            previous=cur.fetchone()
            if op=='latest':
                return _current_result(cur,profile,previous)
            if previous and previous['state'] in (*PENDING,'completed'):
                if _saved_selection(previous)!=selection:
                    raise TenantLinearError('Для этой пары уже сохранён другой выбор отображения.',409)
                return _current_result(cur,profile,previous)
            if _at_capacity(cur,tenant,key[0]):
                raise TenantLinearError('Достигнут лимит активных объединений для этого источника.',409)
            display_name,display_phone=_display_snapshot(cur,tenant,key[0],visible_rows,selection)
            cur.execute("""INSERT INTO saas_dialog_history_merge_jobs
              (tenant_id,id,actor_id,channel_id,left_id,right_id,left_lead,right_lead,state,
                display_name_lead,display_phone_lead,display_revision,display_name,display_phone)
              VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s)
              ON CONFLICT DO NOTHING RETURNING *""",(tenant,job_id,actor,*key,*selection,display_name,display_phone))
            row=cur.fetchone()
            if not row:
                raise TenantLinearError('Другое объединение уже выполняется.',409)
            return _public(row)
        original=_lookup(cur,tenant,actor,job_id,lock=op in ('cancel','undo'))
        if not original:
            raise TenantLinearError('Объединение не найдено.',404)
        _exact(cur,profile,_key(original),write=op=='undo')
        if op=='status':
            return _current_result(cur,profile,original)
        allowed=PENDING if op=='cancel' else ('completed',)
        changed='cancelled' if op=='cancel' else 'undone'
        cur.execute("""UPDATE saas_dialog_history_merge_jobs SET
          state=%s,updated_at=clock_timestamp(),lease_token=NULL,lease_until=NULL
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND actor_id=%s
            AND state=ANY(%s::text[]) RETURNING *""",(changed,tenant,job_id,actor,list(allowed)))
        changed_row=cur.fetchone()
        if changed_row and op=='undo':
            from tenant_ai_agents_store import _audit
            _audit(cur,str(tenant),int(actor),'dialog_history_join_undone','dialog_history_job',
                   str(job_id),{'presentation_only':True})
        return _public(changed_row or _lookup(cur,tenant,actor,job_id))


class Blocked(Exception):
    """Code only, no identity or provider payload."""


def _inspect(cur,job):
    """Exact database checks. No message histories, only fixed-key evidence."""
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_connect_phone_evidence import normalize_phone_jid
    from tenant_connect_dialog_equivalence import assert_lifecycle_unaffected
    from tenant_dialog_history_merge_projection import lifecycle_enabled
    from tenant_whatsapp_native_transport import bind
    tenant=str(job['tenant_id'])
    profile={'tenant_id':tenant,'telegram_id':int(job['actor_id'])}
    channel,left,right,_,_=_key(job)
    # Keep common barrier/member/source order. For the existing legacy no-op
    # route check, take exact route locks before the final checks as well.
    _exact(cur,profile,_key(job),write=True)
    ids=[left,right]
    for route in ids:
        lock_dialog(cur,tenant,'connect',channel,route)
    _exact(cur,profile,_key(job),write=True)
    # Exact direct ledger records, not raw history. Contradictions are blocked
    # permanently rather than treated as editable name/number evidence.
    if not projection_ready(cur):
        raise Blocked('scope_changed')
    phones=[x for x in ids if x.endswith(('@s.whatsapp.net','@c.us')) and len(normalize_phone_jid(x))>=7]
    lids=[x for x in ids if x.endswith('@lid')]
    if len(phones)!=1 or len(lids)!=1:
        raise Blocked('identity_unproven')
    phone,lid=phones[0],lids[0]
    digits=normalize_phone_jid(phone)
    aliases=[lid,digits+'@s.whatsapp.net',digits+'@c.us']
    cur.execute("""SELECT EXISTS(SELECT 1 FROM saas_connect_dialog_links
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND active
        AND (source_id=ANY(%s::text[]) OR canonical_id=ANY(%s::text[]))) AS linked""",
      (tenant,channel,aliases,aliases))
    if cur.fetchone()['linked']:
        raise Blocked('scope_changed')
    scoped=lifecycle_enabled()
    if not scoped:
        try:
            assert_lifecycle_unaffected(cur,tenant,channel,ids)
        except TenantLinearError:
            raise Blocked('lifecycle_in_use') from None
    cur.execute("""SELECT
      (NOT %s AND (EXISTS(SELECT 1 FROM saas_connect_identity_conflicts f
        WHERE f.tenant_id=%s::uuid AND f.connect_channel_id=%s::uuid AND f.conversation_id=ANY(%s::text[]))
      OR EXISTS(SELECT 1 FROM saas_connect_identity_conflict_events f
        WHERE f.tenant_id=%s::uuid AND f.connect_channel_id=%s::uuid AND f.conversation_id=ANY(%s::text[]))))
      OR EXISTS(SELECT 1 FROM saas_connect_direct_identities a
        WHERE a.tenant_id=%s::uuid AND a.connect_channel_id=%s::uuid
        AND a.identity_key=ANY(%s::text[]) AND a.canonical_conversation_id<>a.identity_key
        AND NOT (a.identity_key<>ALL(%s::text[]) AND a.canonical_conversation_id=%s))
      OR EXISTS(SELECT 1 FROM saas_connect_conversations c
        WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
        AND c.conversation_id=ANY(%s::text[]) AND c.conversation_id<>ALL(%s::text[])) AS conflict""",
          (scoped,tenant,channel,aliases,tenant,channel,aliases,tenant,channel,aliases,ids,phone,tenant,channel,aliases,ids))
    if cur.fetchone()['conflict']:
        raise Blocked('identity_unproven')
    if scoped:
        from tenant_whatsapp_pair_evidence import check_pair_evidence
        cutoff=job.get('proof_at')
        if cutoff is None:
            cur.execute("SELECT clock_timestamp() AS ts")
            cutoff=cur.fetchone()['ts']
        if not check_pair_evidence(cur,tenant,channel,phone,lid,cutoff)['safe']:
            raise Blocked('identity_unproven')
    try:
        native=bind(cur,{'tenant_id':tenant,'channel_id':channel,'api_key':''}).get('native')
    except TenantPlatformError:
        native=None
    if not native:
        raise Blocked('scope_changed')
    cur.execute("""SELECT 1 FROM saas_whatsapp_native_routes WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND generation=%s AND enabled AND NOT paused""",
          (tenant,channel,native['scope']['generation']))
    if not cur.fetchone():
        raise Blocked('scope_changed')
    return phone,lid,digits,native


def _next():
    """One scoped proof in flight; the lock lasts only for claim."""
    scope=configured()
    if not scope:
        return None
    with _connect() as conn, conn.cursor() as cur:
        _budget(cur)
        if not ready(cur):
            return None
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('dialog-history-merge-worker',0)) AS ok")
        if not cur.fetchone()['ok']:
            return None
        cur.execute("""SELECT * FROM saas_dialog_history_merge_jobs
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
          AND state IN ('queued','running','verified') AND due_at<=clock_timestamp()
          ORDER BY due_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""",scope)
        job=cur.fetchone()
        if not job or not allows(job['tenant_id'],job['channel_id']):
            return None
        if job['state']=='verified':
            return dict(job)
        if int(job['attempts'])>=3:
            _terminal(cur,job,'failed','proof_timeout')
            return None
        cur.execute("""SELECT 1 FROM saas_dialog_history_merge_jobs WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
          AND state='running' AND lease_until>clock_timestamp() LIMIT 1""",scope)
        if cur.fetchone():
            return None
        token=uuid.uuid4()
        cur.execute("""UPDATE saas_dialog_history_merge_jobs SET
          state='running',step=0,code='',attempts=attempts+1,lease_token=%s::uuid,
          lease_until=clock_timestamp()+interval '12 seconds',
          due_at=clock_timestamp()+interval '12 seconds',updated_at=clock_timestamp()
          WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *""",
          (token,job['tenant_id'],job['id']))
        return dict(cur.fetchone())


def _locked_job(cur,claimed,desired):
    if not allows(claimed['tenant_id'],claimed['channel_id']):
        return None
    cur.execute("""SELECT * FROM saas_dialog_history_merge_jobs
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel_id=%s::uuid AND state=%s
        AND (state<>'running' OR (lease_token=%s::uuid AND lease_until>clock_timestamp()))
      FOR UPDATE""",(claimed['tenant_id'],claimed['id'],claimed['channel_id'],desired,claimed.get('lease_token')))
    return cur.fetchone()


def _terminal(cur,job,state,code):
    cur.execute("""UPDATE saas_dialog_history_merge_jobs SET state=%s,code=%s,
      lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp()
      WHERE tenant_id=%s::uuid AND id=%s::uuid""",(state,code,job['tenant_id'],job['id']))


def _prepare(claimed):
    """Authorize under short locks, then release all locks before provider I/O."""
    with _connect() as conn, conn.cursor() as cur:
        _budget(cur)
        job=_locked_job(cur,claimed,'running')
        if not job:
            return None
        try:
            phone,lid,digits,native=_inspect(cur,job)
        except Blocked as e:
            _terminal(cur,job,'blocked',str(e))
            return None
        except TenantLinearError as e:
            if e.status in (403,404):
                _terminal(cur,job,'blocked','scope_changed')
            return None
        cur.execute("""UPDATE saas_dialog_history_merge_jobs SET
          step=1,updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid""",
          (job['tenant_id'],job['id']))
        return phone,lid,digits,native


def _record_proof(claimed,prepared,details):
    phone,_,digits,native=prepared
    with _connect() as conn,conn.cursor() as cur:
        _budget(cur)
        job=_locked_job(cur,claimed,'running')
        if not job:
            return
        if details.get('phone')!='+'+digits:
            _terminal(cur,job,'blocked','identity_unproven')
            return
        cur.execute("""UPDATE saas_dialog_history_merge_jobs SET
          state='verified',step=2,principal_id=%s,proof_generation=%s,proof_gateway_id=%s::uuid,
          proof_at=clock_timestamp(),updated_at=clock_timestamp(),due_at=clock_timestamp(),
          lease_token=NULL,lease_until=NULL WHERE tenant_id=%s::uuid AND id=%s::uuid""",
          (phone,native['scope']['generation'],native['gateway_id'],job['tenant_id'],job['id']))


def _publish(claimed):
    """One short atomic publication; history book-keeping never touches source records."""
    with _connect() as conn,conn.cursor() as cur:
        _budget(cur)
        job=_locked_job(cur,claimed,'verified')
        if not job:
            return
        cur.execute('SELECT clock_timestamp()-%s::timestamptz <= interval \'30 seconds\' AS fresh',(job['proof_at'],))
        if not cur.fetchone()['fresh']:
            _terminal(cur,job,'blocked','stale_proof')
            return
        try:
            phone,_,_,binding=_inspect(cur,job)
        except Blocked as e:
            _terminal(cur,job,'blocked',str(e))
            return
        except TenantLinearError as e:
            if e.status in (403,404):
                _terminal(cur,job,'blocked','scope_changed')
            return
        if (phone!=job['principal_id'] or binding['scope']['generation']!=job['proof_generation']
                or binding['gateway_id']!=str(job['proof_gateway_id'])):
            _terminal(cur,job,'blocked','stale_proof')
            return
        channel,_,_,ll,rl=_key(job)
        cur.execute("""SELECT 1 FROM saas_dialog_history_merge_jobs WHERE tenant_id=%s::uuid
          AND channel_id=%s::uuid AND state='completed'
          AND (left_lead=ANY(%s::bigint[]) OR right_lead=ANY(%s::bigint[])) LIMIT 1""",
          (job['tenant_id'],channel,[ll,rl],[ll,rl]))
        if cur.fetchone():
            _terminal(cur,job,'blocked','scope_changed')
            return
        if _at_capacity(cur,job['tenant_id'],channel):
            _terminal(cur,job,'blocked','capacity_reached')
            return
        if job['display_name'] or job['display_phone']:
            from tenant_dialog_merge_display import exact_options
            _,left,right,ll,rl=_key(job)
            options=exact_options(cur,job['tenant_id'],channel,((left,ll),(right,rl)),lock=True)
            if not options or options['display_revision']!=job['display_revision']:
                _terminal(cur,job,'blocked','display_changed')
                return
        from tenant_ai_agents_store import _audit
        _audit(cur,str(job['tenant_id']),int(job['actor_id']),'dialog_history_joined','dialog_history_job',
               str(job['id']),{'presentation_only':True})
        cur.execute("""UPDATE saas_dialog_history_merge_jobs SET
          state='completed',step=3,updated_at=clock_timestamp()
          WHERE tenant_id=%s::uuid AND id=%s::uuid""",(job['tenant_id'],job['id']))


async def tick(fetch=None):
    if not enabled():
        return
    job=await asyncio.to_thread(_next)
    if not job:
        return
    if job['state']=='verified':
        await asyncio.to_thread(_publish,job)
        return
    prepared=await asyncio.to_thread(_prepare,job)
    if not prepared or not allows(job['tenant_id'],job['channel_id']):
        return
    if fetch is None:
        from tenant_contact_phone import fetch_contact
        fetch=fetch_contact
    # Existing scope-bound protocol has its own 3s timeout. No transactional
    # ownership lock, source lock, DB transaction or live credential logged.
    try:
        details=await asyncio.wait_for(fetch({'conversation_id':prepared[1],'native':prepared[3]}),timeout=4)
    except asyncio.TimeoutError:
        return  # Short lease recovery will retry at most three attempts.
    await asyncio.to_thread(_record_proof,job,prepared,details or {})


async def worker():
    log=logging.getLogger(__name__)
    while enabled():
        try:
            await tick()
        except Exception as e:
            log.warning('Dialogue history merge tick deferred (%s)',type(e).__name__)
        await asyncio.sleep(2)
