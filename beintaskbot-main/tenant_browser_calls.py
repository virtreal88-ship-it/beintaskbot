"""Source-bound browser calls. Durable commands; no browser provider keys.

The separately paired gateway polls outbound HTTPS. Every browser action checks
fresh membership and source rights. Unknown gateway outcomes never unlock calls.
"""
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone

from tenant_call_recording_bundle import timestamp, MANAGED_MAX_SECONDS, MAX_SECONDS
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_platform import _connect, _json

FINAL = {'ended', 'failed'}
LIVE = {'incoming', 'starting', 'ringing', 'connected', 'ending', 'unknown'}
ID = re.compile(r'^[A-Za-z0-9_-]{5,160}$')


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_browser_call_gateways (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, gateway_id UUID NOT NULL, boot_id UUID NOT NULL,
      last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), seq BIGINT NOT NULL DEFAULT 0,
      connected BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(tenant_id,gateway_id))''')
    cur.execute('ALTER TABLE saas_browser_call_gateways ADD COLUMN IF NOT EXISTS max_seconds INTEGER NOT NULL DEFAULT 120')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_browser_calls (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, gateway_id UUID NOT NULL,
      boot_id UUID NOT NULL, channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
      lead_id BIGINT NOT NULL, peer_jid TEXT NOT NULL, provider_id TEXT,
      actor_id BIGINT, client_request UUID, direction TEXT NOT NULL,
      state TEXT NOT NULL, consent BOOLEAN NOT NULL DEFAULT FALSE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      ended_at TIMESTAMPTZ, recording_id UUID, recording_state TEXT NOT NULL DEFAULT 'none',
      browser_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(tenant_id,gateway_id,provider_id), UNIQUE(tenant_id,actor_id,client_request))''')
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS saas_browser_calls_one_active ON saas_browser_calls(tenant_id,gateway_id) WHERE state NOT IN ('ended','failed')")
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_browser_call_commands (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, call_id UUID NOT NULL REFERENCES saas_browser_calls(id) ON DELETE CASCADE,
      boot_id UUID NOT NULL, op TEXT NOT NULL, payload JSONB NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending', result JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(call_id,op))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_browser_call_cancellations (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      actor_id BIGINT NOT NULL, client_request UUID NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,actor_id,client_request))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_browser_call_retired_boots (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      gateway_id UUID NOT NULL, boot_id UUID NOT NULL,
      retired_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,gateway_id,boot_id))''')
    from tenant_call_sources import migrate as migrate_sources
    migrate_sources(cur)


def config():
    try:
        raw=os.environ.get('BEINTASKBOT_BROWSER_CALL_PILOT','')
        if len(raw)>12000:return None
        c=json.loads(raw)
        for key in ('tenant_id','gateway_id','channel_id'):c[key]=str(uuid.UUID(c[key]))
        if (timestamp(c['expires_at'])<=datetime.now(timezone.utc)
                or len(bytes.fromhex(c['token_sha256']))!=32
                or len(bytes.fromhex(c['public_key']))!=32
                or not 1<=len(c['actors'])<=5 or not all(type(x) is int and x>0 for x in c['actors'])
                or not isinstance(c['routes'],dict) or not 1<=len(c['routes'])<=5
                or not re.fullmatch(r'[0-9]{5,22}@s\.whatsapp\.net',c['account_jid'])):return None
        for peer,route in c['routes'].items():
            if (not re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|lid)',peer)
                    or not re.fullmatch(r'[0-9]{5,22}@s\.whatsapp\.net',route['dial_jid'])
                    or not re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|lid)',route['conversation_id'])
                    or type(route['lead_id']) is not int or route['lead_id']<=0):return None
        return c
    except (ValueError,KeyError,TypeError):return None


def gateway_auth(header):
    c=config()
    token=header[7:] if isinstance(header,str) and header.startswith('Bearer ') else ''
    if not 32<=len(token)<=256:raise TenantLinearError('gateway_access_denied',403)
    digest=hashlib.sha256(token.encode()).hexdigest()
    from tenant_call_sources import configs
    managed=configs(token_hash=digest)
    if len(managed)==1:return managed[0]
    if not c or not hmac.compare_digest(digest,c['token_sha256']):
        raise TenantLinearError('gateway_access_denied',403)
    return c


def configurations(profile,channel=None):
    from tenant_call_sources import configs
    managed=configs(profile,channel=channel)
    legacy=config()
    if (legacy and member_enabled(legacy,profile) and (not channel or channel==legacy['channel_id'])
            and not any(c['channel_id']==legacy['channel_id'] for c in managed)):
        managed.append(legacy)
    return managed


def source_config(profile,channel):
    try:channel=str(uuid.UUID(str(channel)))
    except (ValueError,TypeError):raise TenantLinearError('Источник звонка не определён.',400) from None
    candidates=configurations(profile,channel)
    if len(candidates)!=1:raise TenantLinearError('Звонки не подключены к этому источнику.',403)
    return candidates[0]


def _lock(cur,c):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('browser-call:'+c['tenant_id']+':'+c['gateway_id'],))


def member_enabled(c,profile):
    return (str(profile.get('tenant_id'))==c['tenant_id'] and
            (c.get('all_company_members') is True or int(profile.get('telegram_id') or 0) in c['actors']))


def authorize(cur,profile,route,c,*,write=False,require_accepted=True):
    tenant,actor,policy=access(cur,profile,'dialogs',action='update' if write else 'view')
    if not member_enabled(c,policy.profile):
        raise TenantLinearError('Звонки пока недоступны этому сотруднику.',403)
    try:
        channel=str(uuid.UUID(str(route['channel_id'])));lead=int(route['lead_id']);conversation=str(route['conversation_id'])
    except (KeyError,ValueError,TypeError):raise TenantLinearError('Диалог не определён.',400) from None
    if channel!=c['channel_id']:raise TenantLinearError('Звонок относится к другому источнику.',403)
    if c.get('managed'):
        from tenant_call_sources import route as exact_route
        cur.execute('''SELECT state FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
          AND gateway_id=%s::uuid AND account_jid=%s AND public_key=%s AND enabled=TRUE FOR SHARE''',
                    (tenant,channel,c['gateway_id'],c['account_jid'],c['public_key']))
        binding=cur.fetchone()
        if not binding or (write and binding['state']!='ready'):raise TenantLinearError('Источник звонков изменился или отключён.',409)
        matches=[exact_route(cur,c,conversation=conversation,lead=lead)]
    else:
        matches=[v for v in c['routes'].values() if v['conversation_id']==conversation and v['lead_id']==lead]
    if not matches:raise TenantLinearError('Этот диалог не включён в звонки.',403)
    cur.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE',(tenant,channel))
    source=cur.fetchone()
    # A dedicated test gateway may be explicitly bound to the selected archived
    # route. It never changes that source or claims it is live in Connect.
    owner_bound_test=c.get('owner_bound_test_route') is True and policy.profile.get('role')=='owner'
    if not source or (not source['active'] and not owner_bound_test):
        raise TenantLinearError('Источник звонка недоступен.',403)
    if owner_bound_test or (write and not require_accepted):
        # Explicit owner-selected lab destination, not a Connect send or a reopened
        # support episode. It may be archived or an already completed support conversation.
        # Still enforce current source update grants and lifecycle visibility;
        # never access a conversation assigned to another employee.
        from tenant_dialog_access import require_source
        require_source(cur,tenant,policy,'connect',channel,'update' if write else 'view')
    selected=assert_route(cur,tenant,policy,'connect',conversation,channel,lead,action='update' if write and require_accepted and not owner_bound_test else 'view')
    cur.execute('''SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s FOR SHARE''',(tenant,channel,conversation,lead))
    if not cur.fetchone() or selected.get('conversation_kind')=='group':raise TenantLinearError('Адрес диалога изменился.',409)
    return actor,matches[0]


def offer(profile,route):
    try:c=source_config(profile,str(route.get('channel_id')))
    except TenantLinearError:return False
    if c.get('managed'):
        from tenant_call_sources import JID
        return bool(c['ready'] and JID.fullmatch(str(route.get('conversation_id',''))) and route.get('conversation_kind')!='group')
    return bool(member_enabled(c,profile) and any(str(v['lead_id'])==str(route.get('lead_id'))
        and v['conversation_id']==str(route.get('conversation_id')) for v in c['routes'].values()))


def _public(row,actor):
    return {'id':str(row['id']),'state':row['state'],'direction':row['direction'],
        'channel_id':str(row['channel_id']),'conversation_id':row['conversation_id'],'lead_id':row['lead_id'],
        'display_name':str(row.get('display_name') or '')[:120],
        'owned':row['actor_id']==actor,'claimed':row['actor_id'] is not None,
        'created_at':row['created_at'].isoformat(),'recording_state':row['recording_state'],
        'recording_id':str(row['recording_id']) if row.get('recording_id') else None}


def status(profile,request_id=None,heartbeat=True):
    configured=configurations(profile)
    if not configured:return {'enabled':False}
    sources=[];calls=[]
    from tenant_dialog_access import require_source
    for c in configured:
        # Sync/action take this gateway fence before touching call rows or
        # authorizing a dialog. A heartbeat must use the same order: otherwise
        # it can own the dialog lock while a sync/audio check owns the call row.
        # Separate transactions avoid retaining locks across unrelated sources.
        with _connect() as conn,conn.cursor() as cur:
            _lock(cur,c)
            tenant,actor,policy=access(cur,profile,'dialogs')
            try:require_source(cur,tenant,policy,'connect',c['channel_id'],'view')
            except TenantLinearError:continue
            source,items=_source_status(cur,profile,c,tenant,actor,request_id,heartbeat)
            sources.append(source);calls.extend(items)
    return {'enabled':bool(sources),'online':any(s['online'] for s in sources),'sources':sources,
            'test_number':sources[0]['number'] if len(sources)==1 else '',
            'calls':calls,'max_seconds':max((s['max_seconds'] for s in sources),default=MAX_SECONDS),'retention_hours':sources[0]['retention_hours'] if len(sources)==1 else 720}


def _source_status(cur,profile,c,tenant,actor,request_id,heartbeat):
        cur.execute('SELECT *,last_seen>now()-interval \'10 seconds\' AS fresh FROM saas_browser_call_gateways WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid',(tenant,c['gateway_id']))
        gateway=cur.fetchone()
        cur.execute('''SELECT bc.*,COALESCE(NULLIF(cv.manual_display_name,''),NULLIF(cv.display_name,''),cv.contact_name,'') AS display_name
          FROM saas_browser_calls bc LEFT JOIN saas_connect_conversations cv
          ON cv.tenant_id=bc.tenant_id AND cv.connect_channel_id=bc.channel_id
            AND cv.conversation_id=bc.conversation_id AND cv.lead_id=bc.lead_id
          WHERE bc.tenant_id=%s::uuid AND bc.gateway_id=%s::uuid
          AND (bc.state NOT IN ('ended','failed') OR bc.updated_at>now()-interval '10 minutes') ORDER BY bc.created_at DESC LIMIT 20''',(tenant,c['gateway_id']))
        calls=[]
        for row in cur.fetchall():
            try:authorize(cur,profile,row,c)
            except TenantLinearError:continue
            if heartbeat and request_id and row['actor_id']==actor and str(row.get('client_request'))==request_id and row['state'] not in FINAL:
                cur.execute('UPDATE saas_browser_calls SET browser_seen=now() WHERE id=%s::uuid',(str(row['id']),))
            item=_public(row,actor)
            if row['actor_id']==actor and request_id and str(row.get('client_request'))==request_id:
                item['request_matches']=True
                cur.execute("SELECT result FROM saas_browser_call_commands WHERE call_id=%s::uuid AND op IN ('start','accept') AND state='done'",(str(row['id']),))
                result=cur.fetchone()
                if result and isinstance(result['result'],dict):
                    item['sdp_answer']=result['result'].get('sdp_answer','')
                    item['error_code']=result['result'].get('error_code','')
                    item['audio_ready']=bool(c.get('managed') and result['result'].get('ok') and row.get('provider_id'))
            calls.append(item)
        return {'channel_id':c['channel_id'],'online':bool(gateway and gateway['fresh'] and gateway['connected'] and (not c.get('managed') or c['ready'])),
            'max_seconds':gateway.get('max_seconds',MAX_SECONDS) if gateway else MAX_SECONDS,'number':c['account_jid'].split('@')[0],
            'media':c.get('media','webrtc'),'retention_hours':c.get('retention_hours',24),'permanent':bool(c.get('managed'))},calls


def _command(cur,c,row,op,payload=None):
    cur.execute('''INSERT INTO saas_browser_call_commands(id,tenant_id,call_id,boot_id,op,payload)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb) ON CONFLICT(call_id,op) DO NOTHING''',
      (uuid.uuid4(),c['tenant_id'],str(row['id']),str(row['boot_id']),op,_json(payload or {})))


def _accept_incoming_dialog(profile,data,c,request):
    """Explicit accept uses the existing episode admission, never a second owner system."""
    import tenant_dialog_lifecycle as lifecycle
    with _connect() as conn,conn.cursor() as cur:
        actor,_=authorize(cur,profile,data,c)
        if not lifecycle.enabled(cur,c['tenant_id']):return
        try:call=str(uuid.UUID(str(data.get('call_id'))))
        except (ValueError,TypeError):raise TenantLinearError('Звонок не определён.',400) from None
        cur.execute('''SELECT state,actor_id FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid
          AND id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s FOR SHARE''',
          (c['tenant_id'],c['gateway_id'],call,c['channel_id'],data['conversation_id'],data['lead_id']))
        active=cur.fetchone()
        if not active or active['state']!='incoming' or active['actor_id'] is not None:
            raise TenantLinearError('Вызов уже изменился.',409)
        row=lifecycle._row(cur,c['tenant_id'],c['channel_id'],data['conversation_id'])
        if row and row['state']=='accepted' and row['operator_id']==actor:return
        if row and row['state']!='waiting':raise TenantLinearError('Сначала откройте и примите обращение.',409)
        revision=int(row['revision']) if row else 0
    lifecycle.mutate(profile,{**{k:data[k] for k in ('channel_id','conversation_id','lead_id')},
        'provider':'connect','action':'accept','expected_revision':revision,'request_id':request})


def action(profile,data):
    c=source_config(profile,data.get('channel_id'))
    op=data.get('op')
    if op not in {'start','accept','reject','end'}:raise TenantLinearError('Действие недоступно.',400)
    try:request=str(uuid.UUID(str(data['request_id'])))
    except (KeyError,ValueError,TypeError):raise TenantLinearError('Нет идентификатора запроса.',400) from None
    sdp=data.get('sdp_offer','')
    media_ok=(data.get('media')=='websocket' if c.get('managed') else isinstance(sdp,str) and 50<len(sdp)<24000 and sdp.startswith('v=0'))
    if op in {'start','accept'} and (data.get('consent_confirmed') is not True or not media_ok):
        raise TenantLinearError('Подтвердите согласие на запись и подключите микрофон.',400)
    if c.get('managed') and op in {'start','accept'}:
        with _connect() as conn,conn.cursor() as cur:authorize(cur,profile,data,c,write=True,require_accepted=op!='accept')
        from tenant_call_sources import confirmed_account
        if not c['ready'] or confirmed_account(profile,c['channel_id'])!=c['account_jid']:
            raise TenantLinearError('Номер источника не подтверждён для звонка.',409)
        if op=='accept':_accept_incoming_dialog(profile,data,c,request)
    with _connect() as conn,conn.cursor() as cur:
        if op in {'start','accept'}:
            tenant,actor,_=access(cur,profile,'dialogs',action='update')
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('browser-call-actor:'+tenant+':'+str(actor),))
        _lock(cur,c)
        # Cancelling a failed/pending accept must not first require a successful
        # dialog admission. Fresh source grants and exact call ownership below
        # remain mandatory, as for rejecting an unclaimed incoming call.
        actor,target=authorize(cur,profile,data,c,write=True,require_accepted=op not in {'reject','end'})
        cur.execute('SELECT *,last_seen>now()-interval \'10 seconds\' AS fresh FROM saas_browser_call_gateways WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid FOR UPDATE',(c['tenant_id'],c['gateway_id']))
        gateway=cur.fetchone()
        if op in {'start','accept'}:
            cur.execute('SELECT 1 FROM saas_browser_call_cancellations WHERE tenant_id=%s::uuid AND actor_id=%s AND client_request=%s::uuid',(c['tenant_id'],actor,request))
            if cur.fetchone():raise TenantLinearError('Звонок отменён.',409)
            cur.execute("SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s::uuid AND actor_id=%s AND client_request<>%s::uuid AND state NOT IN ('ended','failed')",(c['tenant_id'],actor,request))
            if cur.fetchone():raise TenantLinearError('У вас уже открыт другой звонок.',409)
        if op=='end':
            # Fence cancellation before a delayed start/accept can commit.
            cur.execute('INSERT INTO saas_browser_call_cancellations(tenant_id,actor_id,client_request) VALUES(%s::uuid,%s,%s::uuid) ON CONFLICT DO NOTHING',(c['tenant_id'],actor,request))
        if op=='start':
            cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s::uuid AND actor_id=%s AND client_request=%s::uuid',(c['tenant_id'],actor,request))
            previous=cur.fetchone()
            if previous:
                if str(previous['lead_id'])!=str(data['lead_id']) or previous['conversation_id']!=data['conversation_id']:
                    raise TenantLinearError('Запрос относится к другому диалогу.',409)
                return _public(previous,actor)
            if not gateway or not gateway['fresh'] or not gateway['connected']:raise TenantLinearError('Источник звонков не подключён к серверу.',409)
            cur.execute("SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND state NOT IN ('ended','failed')",(c['tenant_id'],c['gateway_id']))
            if cur.fetchone():raise TenantLinearError('Другой звонок ещё не завершён.',409)
            if not c.get('managed'):
                cur.execute("SELECT count(*) AS n FROM saas_browser_calls WHERE tenant_id=%s::uuid AND created_at>now()-interval '24 hours'",(c['tenant_id'],))
                if cur.fetchone()['n']>=20:raise TenantLinearError('Лимит теста: 20 звонков за 24 часа.',429)
            cur.execute('''INSERT INTO saas_browser_calls(id,tenant_id,gateway_id,boot_id,channel_id,conversation_id,lead_id,peer_jid,
              actor_id,client_request,direction,state,consent,recording_state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'outbound','starting',TRUE,'pending') RETURNING *''',
              (uuid.uuid4(),c['tenant_id'],c['gateway_id'],str(gateway['boot_id']),c['channel_id'],target['conversation_id'],target['lead_id'],target['dial_jid'],actor,request))
            row=cur.fetchone()
        else:
            if op=='end' and not data.get('call_id'):
                cur.execute('SELECT id FROM saas_browser_calls WHERE tenant_id=%s::uuid AND actor_id=%s AND client_request=%s::uuid',(c['tenant_id'],actor,request))
                pending=cur.fetchone()
                if not pending:return {'state':'not_started'}
                data={**data,'call_id':str(pending['id'])}
            try:call_id=str(uuid.UUID(str(data['call_id'])))
            except (KeyError,ValueError,TypeError):raise TenantLinearError('Звонок не определён.',400) from None
            cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND id=%s::uuid FOR UPDATE',(c['tenant_id'],c['gateway_id'],call_id))
            row=cur.fetchone()
            if not row or any(str(row[k])!=str(data.get(k)) for k in ('channel_id','conversation_id','lead_id')):raise TenantLinearError('Звонок не относится к диалогу.',404)
            if row['actor_id'] is not None and row['actor_id']!=actor:raise TenantLinearError('Звонок принят другим сотрудником.',409)
            if row['state'] in FINAL:return _public(row,actor)
            if op=='accept':
                if row['state']!='incoming' or not gateway or not gateway['fresh']:raise TenantLinearError('Вызов уже изменился.',409)
                cur.execute("UPDATE saas_browser_calls SET actor_id=%s,client_request=%s::uuid,state='starting',consent=TRUE,recording_state='pending',browser_seen=now(),updated_at=now() WHERE id=%s::uuid RETURNING *",(actor,request,call_id))
                row=cur.fetchone()
            else:
                if op=='reject' and row['state']!='incoming':raise TenantLinearError('Звонок уже принят.',409)
                cur.execute("UPDATE saas_browser_calls SET state='ending',actor_id=COALESCE(actor_id,%s),updated_at=now() WHERE id=%s::uuid",(actor,call_id))
                row={**row,'state':'ending','actor_id':actor}
        _command(cur,c,row,op,{'media':c.get('media','webrtc'),'sdp_offer':sdp,'consent_confirmed':True} if op in {'start','accept'} else {})
        return _public(row,actor)


def sync(c,data):
    """Ordered gateway snapshot plus durable command acknowledgements.

    Only a same-boot explicit ended event closes a provider-backed call. Missing
    calls, silence, stale heartbeats and new processes are never closure proof.
    """
    try:
        boot=str(uuid.UUID(data['boot_id']));seq=data['seq']
        active=data['active'];ended=data['ended'];results=data.get('results',[])
        closed=data.get('closed_process_boots',[])
        if not isinstance(closed,list) or len(closed)>16 or (closed and not c.get('managed')):raise ValueError()
        closed=[str(uuid.UUID(value)) for value in closed]
        if boot in closed or len(set(closed))!=len(closed):raise ValueError()
        if (type(seq) is not int or not 0<seq<2**53 or type(data.get('connected')) is not bool
                or not isinstance(active,list) or len(active)>1 or not isinstance(ended,list) or len(ended)>20
                or not isinstance(results,list) or len(results)>5):raise ValueError()
        for item in active+ended:
            from tenant_call_sources import JID
            valid_peer=bool(JID.fullmatch(item.get('peer_jid',''))) if c.get('managed') and isinstance(item,dict) else isinstance(item,dict) and item.get('peer_jid') in c['routes']
            if not isinstance(item,dict) or not ID.fullmatch(item['provider_id']) or not valid_peer:
                raise ValueError()
            if item['direction'] not in {'inbound','outbound'} or item['state'] not in {'ringing','connected','ended'}:raise ValueError()
        for result in results:
            uuid.UUID(result['id'])
            if type(result.get('ok')) is not bool or len(_json(result))>26000:raise ValueError()
            if result.get('provider_id') and not ID.fullmatch(result['provider_id']):raise ValueError()
            if result.get('sdp_answer') and (not isinstance(result['sdp_answer'],str) or not result['sdp_answer'].startswith('v=0') or len(result['sdp_answer'])>24000):raise ValueError()
    except (ValueError,KeyError,TypeError):raise TenantLinearError('gateway_snapshot_invalid',400) from None
    with _connect() as conn,conn.cursor() as cur:
        _lock(cur,c)
        cur.execute('SELECT 1 FROM saas_browser_call_retired_boots WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND boot_id=%s::uuid',(c['tenant_id'],c['gateway_id'],boot))
        if cur.fetchone():raise TenantLinearError('gateway_retired_process',409)
        cur.execute('SELECT *,last_seen>now()-interval \'20 seconds\' AS fresh FROM saas_browser_call_gateways WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid FOR UPDATE',(c['tenant_id'],c['gateway_id']))
        old=cur.fetchone()
        if old and str(old['boot_id'])!=boot:
            if c.get('managed') and str(old['boot_id']) in closed:
                # Hosted engine holds an OS-exclusive lifetime lock before
                # restoring WhatsApp. Acquisition proves the previous process
                # owning this same durable source has exited. A timer or a
                # missing call snapshot is never accepted as this proof.
                cur.execute('''UPDATE saas_browser_calls SET state='ended',ended_at=COALESCE(ended_at,now()),updated_at=now(),
                  recording_state=CASE WHEN recording_state='pending' THEN 'unconfirmed' ELSE recording_state END
                  WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND boot_id=%s::uuid AND state NOT IN ('ended','failed')''',
                  (c['tenant_id'],c['gateway_id'],str(old['boot_id'])))
                cur.execute('''UPDATE saas_browser_call_commands q SET state='done',payload='{}'::jsonb,
                  result='{"ok":false,"error_code":"process_closed"}'::jsonb FROM saas_browser_calls c
                  WHERE c.id=q.call_id AND c.tenant_id=%s::uuid AND c.gateway_id=%s::uuid AND c.boot_id=%s::uuid AND q.state<>'done' ''',
                  (c['tenant_id'],c['gateway_id'],str(old['boot_id'])))
            else:
                cur.execute("SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND state NOT IN ('ended','failed')",(c['tenant_id'],c['gateway_id']))
                if cur.fetchone() or old['fresh']:raise TenantLinearError('gateway_previous_process_unconfirmed',409)
            cur.execute('INSERT INTO saas_browser_call_retired_boots(tenant_id,gateway_id,boot_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(c['tenant_id'],c['gateway_id'],str(old['boot_id'])))
        if old and str(old['boot_id'])==boot and seq<int(old['seq']):raise TenantLinearError('gateway_stale_snapshot',409)
        fresh=not old or str(old['boot_id'])!=boot or seq>int(old['seq'])
        if fresh:
            for result in results:
                cur.execute('''SELECT q.*,c.provider_id,c.state AS call_state FROM saas_browser_call_commands q
                  JOIN saas_browser_calls c ON c.id=q.call_id WHERE q.tenant_id=%s::uuid AND q.id=%s::uuid
                  AND q.boot_id=%s::uuid AND c.gateway_id=%s::uuid FOR UPDATE OF q,c''',(c['tenant_id'],result['id'],boot,c['gateway_id']))
                command=cur.fetchone()
                if not command or command['state']=='done':continue
                provider=result.get('provider_id') or command['provider_id']
                if command['provider_id'] and provider!=command['provider_id']:raise TenantLinearError('gateway_call_mismatch',409)
                safe={'ok':result['ok'],'provider_id':provider,
                      'sdp_answer':result.get('sdp_answer','') if result['ok'] else '',
                      'error_code':result.get('error_code') if result.get('error_code') in
                      {'busy','scope_denied','microphone_setup_failed','call_ended','call_reject_unconfirmed','provider_start_failed','command_uncertain','recording_unavailable'} else ''}
                cur.execute("UPDATE saas_browser_call_commands SET state='done',result=%s::jsonb,payload='{}'::jsonb WHERE id=%s::uuid",(_json(safe),result['id']))
                cur.execute('UPDATE saas_browser_calls SET provider_id=COALESCE(provider_id,%s),updated_at=now() WHERE id=%s::uuid',(provider,str(command['call_id'])))
                if result.get('not_started') is True and not provider and command['op']=='start' and not result['ok']:
                    cur.execute("UPDATE saas_browser_calls SET state='failed',recording_state='none',ended_at=now() WHERE id=%s::uuid",(str(command['call_id']),))
            for item in ended:
                if item['state']!='ended':continue
                cur.execute('''UPDATE saas_browser_calls SET state='ended',ended_at=COALESCE(ended_at,now()),updated_at=now()
                  WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND boot_id=%s::uuid AND provider_id=%s AND peer_jid=%s''',
                  (c['tenant_id'],c['gateway_id'],boot,item['provider_id'],item['peer_jid']))
            for item in active:
                if item['state']=='ended':continue
                cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND provider_id=%s FOR UPDATE',(c['tenant_id'],c['gateway_id'],item['provider_id']))
                row=cur.fetchone()
                if row:
                    if str(row['boot_id'])!=boot or row['peer_jid']!=item['peer_jid']:raise TenantLinearError('gateway_call_mismatch',409)
                    if row['state'] in FINAL:continue
                    if row['state'] not in {'incoming','ending','unknown'}:
                        cur.execute('UPDATE saas_browser_calls SET state=%s,updated_at=now() WHERE id=%s::uuid',(item['state'],str(row['id'])))
                elif item['direction']=='inbound':
                    cur.execute("SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND state NOT IN ('ended','failed')",(c['tenant_id'],c['gateway_id']))
                    if cur.fetchone():continue
                    if c.get('managed'):
                        from tenant_call_sources import route as exact_route
                        if not data['connected']:continue
                        target=exact_route(cur,c,peer=item['peer_jid'],create_incoming=True)
                    else:target=c['routes'][item['peer_jid']]
                    incoming_id=uuid.uuid4()
                    cur.execute('''INSERT INTO saas_browser_calls(id,tenant_id,gateway_id,boot_id,channel_id,conversation_id,lead_id,peer_jid,
                      provider_id,direction,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'inbound','incoming')''',
                      (incoming_id,c['tenant_id'],c['gateway_id'],boot,c['channel_id'],target['conversation_id'],target['lead_id'],item['peer_jid'],item['provider_id']))
                    if c.get('managed'):
                        from tenant_dialog_lifecycle import note_inbound_call
                        note_inbound_call(cur,c['tenant_id'],c['channel_id'],target['conversation_id'],incoming_id)
            cur.execute('''UPDATE saas_browser_call_commands q SET payload='{}'::jsonb,result=result-'sdp_answer'
              FROM saas_browser_calls c WHERE c.id=q.call_id AND c.tenant_id=%s::uuid AND c.state IN ('ended','failed')''',(c['tenant_id'],))
            cur.execute('''INSERT INTO saas_browser_call_gateways(tenant_id,gateway_id,boot_id,seq,connected,max_seconds)
              VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,gateway_id) DO UPDATE SET
              boot_id=EXCLUDED.boot_id,seq=EXCLUDED.seq,connected=EXCLUDED.connected,max_seconds=EXCLUDED.max_seconds,last_seen=now()''',
              (c['tenant_id'],c['gateway_id'],boot,seq,data['connected'],
               MANAGED_MAX_SECONDS if c.get('managed') and data.get('max_seconds')==MANAGED_MAX_SECONDS else MAX_SECONDS))
        cur.execute("SELECT *,browser_seen<now()-interval '15 seconds' AS browser_gone FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND boot_id=%s::uuid AND state NOT IN ('ended','failed') AND actor_id IS NOT NULL",(c['tenant_id'],c['gateway_id'],boot))
        for row in cur.fetchall():
            try:
                if row['browser_gone']:raise TenantLinearError('browser_gone',409)
                authorize(cur,{'tenant_id':c['tenant_id'],'telegram_id':row['actor_id']},row,c,write=True)
            except TenantLinearError:
                _command(cur,c,row,'end')
                cur.execute("UPDATE saas_browser_calls SET state='ending',updated_at=now() WHERE id=%s::uuid",(str(row['id']),))
        cur.execute('''SELECT q.id,q.call_id,q.op,q.payload,c.provider_id,c.peer_jid,c.actor_id,c.state AS call_state
          FROM saas_browser_call_commands q JOIN saas_browser_calls c ON c.id=q.call_id
          WHERE q.tenant_id=%s::uuid AND q.boot_id=%s::uuid AND c.gateway_id=%s::uuid AND q.state<>'done'
          ORDER BY q.created_at,q.id LIMIT 1''',(c['tenant_id'],boot,c['gateway_id']))
        commands=[]
        for row in cur.fetchall():
            cur.execute("UPDATE saas_browser_call_commands SET state='dispatched' WHERE id=%s::uuid",(str(row['id']),))
            commands.append({'id':str(row['id']),'call_id':str(row['call_id']),'op':row['op'],
                'provider_id':row['provider_id'],'peer_jid':row['peer_jid'],'payload':row['payload'],
                'cancelled':row['call_state'] in FINAL or (row['call_state']=='ending' and row['op'] in {'start','accept'})})
        cur.execute('''SELECT id,provider_id,recording_id FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid
          AND (boot_id=%s::uuid OR %s) AND consent=TRUE AND provider_id IS NOT NULL
          AND created_at>now()-interval '24 hours' ORDER BY (recording_id IS NULL) DESC,created_at ASC LIMIT 20''',(c['tenant_id'],c['gateway_id'],boot,bool(c.get('managed'))))
        recordings=[{'call_id':str(r['id']),'provider_id':r['provider_id'],'saved':bool(r['recording_id'])} for r in cur.fetchall()]
        return {'commands':commands,'recordings':recordings,'ack_seq':seq}


def save_recording(c,call_id,bundle):
    from tenant_call_recording_bundle import decode
    from tenant_call_recordings import persist
    try:call_id=str(uuid.UUID(str(call_id)))
    except (ValueError,TypeError):raise TenantLinearError('recording_call_invalid',400) from None
    manifest,wav=decode(bundle,bytes.fromhex(c['public_key']),retention_hours=720 if c.get('managed') else 24,
                        max_seconds=MANAGED_MAX_SECONDS if c.get('managed') else MAX_SECONDS)
    encoded=None
    if c.get('managed'):
        from tenant_call_audio_storage import compress
        encoded=compress(wav)
    with _connect() as conn,conn.cursor() as cur:
        _lock(cur,c)
        cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND id=%s::uuid FOR UPDATE',(c['tenant_id'],c['gateway_id'],call_id))
        row=cur.fetchone()
        if (not row or row['state']!='ended' or row['consent'] is not True or not row['actor_id']
                or row['provider_id']!=manifest['call_id'] or row['peer_jid']!=manifest['peer_jid']
                or manifest['account_jid']!=c['account_jid']):raise TenantLinearError('recording_call_mismatch',409)
        profile={'tenant_id':c['tenant_id'],'telegram_id':row['actor_id']}
        actor,_=authorize(cur,profile,row,c,write=True)
        selected={'connect_channel_id':row['channel_id'],'conversation_id':row['conversation_id'],'lead_id':row['lead_id']}
        saved=persist(cur,c['tenant_id'],actor,c,selected,manifest,wav,bundle,encoded_audio=encoded)
        cur.execute('UPDATE saas_test_call_recordings SET browser_call_id=%s::uuid WHERE id=%s::uuid AND (browser_call_id IS NULL OR browser_call_id=%s::uuid) RETURNING id',(call_id,saved['id'],call_id))
        if not cur.fetchone():raise TenantLinearError('recording_binding_conflict',409)
        cur.execute('UPDATE saas_browser_calls SET recording_id=%s::uuid,recording_state=%s,updated_at=now() WHERE id=%s::uuid',
          (saved['id'],'ready' if manifest['complete'] else 'incomplete',call_id))
        return saved
