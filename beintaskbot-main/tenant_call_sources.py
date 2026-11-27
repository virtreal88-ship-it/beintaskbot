"""Permanent, tenant/source-bound call engines. No pilot expiry or source override.

The existing Connect channel owns the immutable confirmed phone. A call engine
is a separate linked device for that same phone, never a replacement source.
"""
import hashlib
import json
import os
import re
import secrets
import uuid
from urllib.parse import urlsplit

import requests
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_platform import _connect, _fernet

JID = re.compile(r'^[1-9][0-9]{5,21}@(s\.whatsapp\.net|lid)$')


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_browser_call_sources (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL, gateway_id UUID NOT NULL UNIQUE,
      account_jid TEXT NOT NULL, public_key TEXT NOT NULL DEFAULT '',
      token_sha256 TEXT NOT NULL UNIQUE, encrypted_token BYTEA NOT NULL,
      state TEXT NOT NULL DEFAULT 'provisioning', enabled BOOLEAN NOT NULL DEFAULT TRUE,
      created_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,channel_id))''')


def service():
    url=os.environ.get('BEINTASKBOT_CALLS_SERVICE_URL','').rstrip('/')
    token=os.environ.get('BEINTASKBOT_CALLS_SERVICE_TOKEN','')
    parsed=urlsplit(url)
    # Configured server endpoint only; no URLs supplied by browser or provider.
    private=parsed.scheme=='http' and (parsed.hostname or '').endswith('.railway.internal')
    if not url or not (parsed.scheme=='https' or private) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or len(token)<32:
        raise TenantLinearError('Сервер звонков ещё не настроен.',503)
    return url,token


def remote(path, payload=None, *, missing_ok=False):
    url,token=service()
    try:
        with requests.request('POST' if payload is not None else 'GET',url+path,
            headers={'Authorization':'Bearer '+token,'Accept':'application/json'},json=payload,
            timeout=(5,25),allow_redirects=False,stream=True) as response:
            missing=missing_ok and payload is None and response.status_code==404
            if not missing and response.status_code not in (200,201):
                raise TenantLinearError('Сервер звонков не подтвердил действие.',503)
            chunks=[];size=0
            for chunk in response.iter_content(8192):
                size+=len(chunk)
                if size>32768:raise ValueError()
                chunks.append(chunk)
            raw=b''.join(chunks)
            if missing:
                if raw==b'source_not_found':return None
                raise TenantLinearError('Сервер звонков не подтвердил действие.',503)
            data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError()
        return data
    except (requests.RequestException,ValueError):
        raise TenantLinearError('Сервер звонков временно недоступен.',503) from None


def _owner(cur,profile,channel):
    tenant,actor,policy=access(cur,profile,'dialogs',action='update')
    if policy.profile.get('role')!='owner':
        raise TenantLinearError('Подключать номера для звонков может владелец компании.',403)
    cur.execute('SELECT active,status FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE',(tenant,channel))
    row=cur.fetchone()
    if not row or not row['active'] or row['status'] in ('disabled','error'):
        from tenant_whatsapp_native_rollout import staged
        if not row or not staged(cur,tenant,channel):
            raise TenantLinearError('Источник отключён или его номер не подтверждён.',409)
    return tenant,actor


def confirmed_account(profile,channel):
    from tenant_whatsapp_native_transport import bind
    with _connect() as conn,conn.cursor() as cur:
        tenant,_,policy=access(cur,profile,'dialogs',action='update')
        from tenant_dialog_access import require_source
        require_source(cur,tenant,policy,'connect',channel,'update')
        route=bind(cur,{'tenant_id':tenant,'channel_id':channel,'api_key':''})
    if route.get('native'):
        result=remote('/v1/sources/'+route['native']['gateway_id'])
        if result.get('connected') is not True or result.get('account_jid')!=route['native']['scope']['account']:
            raise TenantLinearError('Единый WhatsApp не подтвердил номер источника.',409)
        return result['account_jid']
    from tenant_connect_transport import _stored_connect_credentials, _request
    credentials=_stored_connect_credentials(profile)
    current=_request(credentials['api_key'],'GET','/v1/channels/'+channel+'/status')
    phone=str(current.get('phone') or '').lstrip('+')
    if current.get('id')!=channel or current.get('status')!='connected' or current.get('phoneConflict') or not re.fullmatch(r'[1-9][0-9]{5,21}',phone):
        raise TenantLinearError('Connect не подтвердил номер этого источника.',409)
    return phone+'@s.whatsapp.net'


def provision(profile,channel):
    channel=str(uuid.UUID(str(channel)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,actor=_owner(cur,profile,channel)
        from tenant_whatsapp_native_rollout import staged
        pending=staged(cur,tenant,channel)
    if pending:
        from tenant_whatsapp_integration import continue_pairing
        return continue_pairing(profile,channel)
    account=confirmed_account(profile,channel)
    service()  # Fail before reserving any binding if hosting is not configured.
    with _connect() as conn,conn.cursor() as cur:
        _owner(cur,profile,channel)
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('call-source:'+tenant+':'+channel,))
        cur.execute('SELECT * FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid FOR UPDATE',(tenant,channel))
        row=cur.fetchone()
        if row and row['account_jid']!=account:
            raise TenantLinearError('Номер источника изменился. Старое подключение звонков не будет использовано.',409)
        if not row:
            token=secrets.token_urlsafe(48)
            cur.execute('''INSERT INTO saas_browser_call_sources
              (tenant_id,channel_id,gateway_id,account_jid,token_sha256,encrypted_token,created_by)
              VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
              (tenant,channel,uuid.uuid4(),account,hashlib.sha256(token.encode()).hexdigest(),_fernet().encrypt(token.encode()),actor))
            row=cur.fetchone()
    data=remote('/v1/sources/'+str(row['gateway_id']),{
        'tenant_id':tenant,'channel_id':channel,'account_jid':account,
        'gateway_token':_fernet().decrypt(bytes(row['encrypted_token'])).decode()})
    public=data.get('public_key','')
    if not isinstance(public,str) or not re.fullmatch(r'[0-9a-f]{64}',public):
        raise TenantLinearError('Сервер звонков не подтвердил ключ записи.',503)
    with _connect() as conn,conn.cursor() as cur:
        _owner(cur,profile,channel)
        cur.execute('''UPDATE saas_browser_call_sources SET public_key=%s,state='pairing',updated_at=now()
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND gateway_id=%s::uuid
          AND account_jid=%s AND (public_key='' OR public_key=%s) RETURNING gateway_id''',
          (public,tenant,channel,str(row['gateway_id']),account,public))
        if not cur.fetchone():raise TenantLinearError('Привязка звонков изменилась.',409)
    return source_status(profile,channel)


def source_status(profile,channel):
    channel=str(uuid.UUID(str(channel)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=_owner(cur,profile,channel)
        from tenant_whatsapp_native_rollout import modes
        native_mode=modes(cur,tenant).get(channel)
        cur.execute('SELECT * FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,channel))
        row=cur.fetchone()
    if not row:return {'channel_id':channel,'state':'not_configured'}
    data=remote('/v1/sources/'+str(row['gateway_id']))
    account=data.get('account_jid')
    state=('ready' if data.get('connected') is True else 'offline') if account==row['account_jid'] else 'identity_mismatch' if account else 'pairing'
    with _connect() as conn,conn.cursor() as cur:
        _owner(cur,profile,channel)
        cur.execute('UPDATE saas_browser_call_sources SET state=%s,updated_at=now() WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND gateway_id=%s::uuid',
                    (state,tenant,channel,str(row['gateway_id'])))
    qr=data.get('qr') if state=='pairing' else None
    if qr is not None and (not isinstance(qr,str) or len(qr)>24000 or not re.fullmatch(r'[A-Za-z0-9+/=]*',qr)):
        raise TenantLinearError('QR подключения недоступен.',503)
    return {'channel_id':channel,'state':state,'phone':row['account_jid'].split('@')[0],'qr':qr,
            'permanent':True,'retention_hours':30*24,
            **({'unified':True,'activation_pending':not native_mode['enabled']} if native_mode else {})}


def as_config(row):
    return {**row,'tenant_id':str(row['tenant_id']),'channel_id':str(row['channel_id']),
            'gateway_id':str(row['gateway_id']),'managed':True,'all_company_members':True,'actors':[],
            'ready':row['state']=='ready','media':'websocket','retention_hours':30*24}


def configs(profile=None,*,channel=None,token_hash=None):
    if not os.environ.get('BEINTASKBOT_CALLS_SERVICE_URL'):return []
    with _connect() as conn,conn.cursor() as cur:
        if token_hash:
            cur.execute("SELECT * FROM saas_browser_call_sources WHERE token_sha256=%s AND enabled=TRUE AND public_key<>''",(token_hash,))
        else:
            tenant,_,_=access(cur,profile,'dialogs')
            cur.execute("SELECT * FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND enabled=TRUE AND (%s::uuid IS NULL OR channel_id=%s::uuid) AND public_key<>''",
                        (tenant,channel,channel))
        # Raw gateway credentials never leave the internal setup boundary.
        return [as_config({k:v for k,v in row.items() if k!='encrypted_token'}) for row in cur.fetchall()]


def route(cur,c,*,conversation=None,lead=None,peer=None,create_incoming=False,caller=None):
    """Only exact channel history or a previously verified PN/LID alias."""
    identity={}
    if peer and caller and create_incoming:
        from tenant_call_identity import enrich
        identity=enrich(cur,c,peer,caller)
        peer=identity.get('canonical',peer)
    if peer:
        if not JID.fullmatch(peer):raise TenantLinearError('call_peer_invalid',400)
        cur.execute('''SELECT c.* FROM saas_connect_conversations c WHERE c.tenant_id=%s::uuid
          AND c.connect_channel_id=%s::uuid AND (c.conversation_id=%s OR c.conversation_id IN
          (SELECT canonical_conversation_id FROM saas_connect_direct_identities WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND identity_key=%s))''',
          (c['tenant_id'],c['channel_id'],peer,c['tenant_id'],c['channel_id'],peer))
    else:
        cur.execute('''SELECT * FROM saas_connect_conversations WHERE tenant_id=%s::uuid
          AND connect_channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s''',
          (c['tenant_id'],c['channel_id'],conversation,lead))
    rows=cur.fetchall()
    if not rows and peer and create_incoming:
        # The paired device supplied the exact peer JID for a live inbound call.
        # Reuse ordinary conversation storage; never join by a display name or
        # infer that a numeric LID is a telephone number.
        from tenant_connect_transport import _upsert_conversation
        lead=_upsert_conversation(cur,c['tenant_id'],c['channel_id'],peer,identity.get('metadata') or {'kind':'contact','provider_conversation_id':peer})
        return {'conversation_id':peer,'lead_id':lead,'dial_jid':peer}
    if len(rows)!=1 or rows[0].get('conversation_kind')=='group' or not JID.fullmatch(rows[0]['conversation_id']):
        raise TenantLinearError('Нет однозначной переписки в этом источнике.',409)
    row=rows[0]
    return {'conversation_id':row['conversation_id'],'lead_id':row['lead_id'],'dial_jid':row['conversation_id']}


def audio_authority(profile,call_id,request_id):
    from tenant_browser_calls import authorize, source_config, _lock
    try:call_id=str(uuid.UUID(str(call_id)));request_id=str(uuid.UUID(str(request_id)))
    except (TypeError,ValueError):raise TenantLinearError('call_identity_invalid',400) from None
    with _connect() as conn,conn.cursor() as cur:
        tenant,actor,_=access(cur,profile,'dialogs',action='update')
        cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,call_id))
        row=cur.fetchone()
    if not row:raise TenantLinearError('call_audio_denied',403)
    c=source_config(profile,str(row['channel_id']))
    with _connect() as conn,conn.cursor() as cur:
        # Match status/sync/action before acquiring call-row and dialog locks.
        # This remains a fresh, fenced authorization on every audio check.
        _lock(cur,c)
        # Re-read after resolving config. Final authority cannot use the earlier
        # call snapshot after a close, transfer or account switch.
        cur.execute('SELECT * FROM saas_browser_calls WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,call_id))
        row=cur.fetchone()
        if (not row or not c.get('managed') or str(row['gateway_id'])!=c['gateway_id'] or row['actor_id']!=actor
                or str(row['client_request'])!=request_id or row['state'] not in ('starting','ringing','connected')
                or not row['provider_id'] or not row['consent']):raise TenantLinearError('call_audio_denied',403)
        authorize(cur,profile,row,c,write=True)
        return c['gateway_id'],row['provider_id'],call_id
