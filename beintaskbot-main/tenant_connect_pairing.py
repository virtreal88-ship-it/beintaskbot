"""Durable, actor-bound new-number pairing. Never activate a source before scan."""
import hashlib
import re
import uuid

from tenant_connect_transport import (_connect, _owner, _stored_connect_credentials,
    _tenant_connect_lock, _request, _configure_locked, _schema as transport_schema)
from tenant_shared_integration_access import guard, principal
from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError

_ACTIONS = {'pairing_start', 'pairing_status', 'pairing_refresh', 'pairing_complete'}
_COLUMNS = {'saas_connect_pairing_intents': 'tenant_id actor_id request_id provider_request_id key_fingerprint name channel_id state capability created_at updated_at'.split()}
_INDEXES = ('saas_connect_pairing_intents_pkey', 'saas_connect_pairing_pending_actor_idx')


def _schema(conn):
    from tenant_schema_preflight import ready, lock_schema
    transport_schema(conn)
    with conn.cursor() as cur:
        if ready(cur, _COLUMNS, _INDEXES): return
        lock_schema(cur)
        if ready(cur, _COLUMNS, _INDEXES): return
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_pairing_intents(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          actor_id BIGINT NOT NULL, request_id UUID NOT NULL, provider_request_id UUID NOT NULL, key_fingerprint TEXT NOT NULL,
          name TEXT NOT NULL, channel_id UUID, state TEXT NOT NULL DEFAULT 'creating',
          capability TEXT NOT NULL DEFAULT 'v1', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,actor_id,request_id),
          FOREIGN KEY(tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id),
          CHECK(state IN ('creating','awaiting_scan','blocked','complete')))''')
        cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_connect_pairing_pending_actor_idx
          ON saas_connect_pairing_intents(tenant_id,actor_id) WHERE state<>'complete' ''')


def _uuid(value):
    try:
        if not isinstance(value, str): raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise TenantLinearError('connect_pairing_request_invalid', 400) from None


def _validate(profile, payload):
    tenant, actor = principal(profile)
    if not isinstance(payload, dict) or set(payload)-{'action','request_id','name','expected_tenant_id','expected_user_id','confirm_company_impact'}:
        raise TenantLinearError('connect_pairing_request_invalid', 400)
    if payload.get('action') not in _ACTIONS:
        raise TenantLinearError('connect_pairing_request_invalid', 400)
    if str(payload.get('expected_tenant_id')) != tenant or str(payload.get('expected_user_id')) != str(actor):
        raise TenantLinearError('integration_context_changed', 409)
    if payload.get('confirm_company_impact') is not True:
        raise TenantLinearError('integration_company_impact_confirmation_required', 409)
    request_id = _uuid(payload.get('request_id'))
    name = payload.get('name', 'WhatsApp')
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c)<32 for c in name):
        raise TenantLinearError('connect_pairing_name_invalid', 400)
    return tenant, actor, request_id, name.strip()


def _provider_request_id(tenant, actor, request_id):
    # Connect keys may be shared across CRM tenants: never expose a raw client
    # UUID as the account-wide provider idempotency namespace.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'beintaskbot:connect-pairing:{tenant}:{actor}:{request_id}'))


def _read(tenant, actor, request_id, *, pending=False):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT * FROM saas_connect_pairing_intents
              WHERE tenant_id=%s::uuid AND actor_id=%s AND ''' +
              ("state<>'complete'" if pending else 'request_id=%s::uuid'),
              (tenant,actor) if pending else (tenant,actor,request_id))
            row = cur.fetchone()
            return dict(row) if row else None


def _save(row, *, channel_id=None, state=None):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_connect_pairing_intents SET channel_id=COALESCE(%s::uuid,channel_id),
              state=COALESCE(%s,state),updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',
              (channel_id,state,str(row['tenant_id']),row['actor_id'],str(row['request_id'])))
        conn.commit()
    if channel_id: row['channel_id']=channel_id
    if state: row['state']=state


def _response(row, *, status='', phone='', qr=None, unknown=False):
    result = {'request_id':str(row['request_id']),
        'channel_id':str(row['channel_id']) if row.get('channel_id') else None,
        'state':'unknown' if unknown else row['state'], 'status':status, 'phone':phone}
    if qr is not None: result['qr']=qr
    return {'pairing':result}


def _capability(key):
    try: value = _request(key,'GET','/v1/channels/capabilities')
    except TenantPlatformError:
        raise TenantLinearError('connect_pairing_channels_write_or_capability_unavailable', 409) from None
    if value.get('createIdempotency') != 'v1' or value.get('channelCreatePath') != '/v1/channels/idempotent':
        raise TenantLinearError('connect_pairing_idempotency_unavailable', 409)


def _create(row, key):
    # Capability is checked on every ambiguous retry, never fall back to the old API.
    _capability(key)
    try:
        channel = _request(key,'POST','/v1/channels/idempotent',{'name':row['name'],'provider':'whatsapp'},
                           idempotency_key=str(row['provider_request_id']))
    except TenantPlatformError:
        return False
    echo = channel.get('idempotency')
    if (not isinstance(echo,dict) or echo.get('version')!='v1'
            or echo.get('requestId')!=str(row['provider_request_id']) or type(echo.get('replayed')) is not bool
            or channel.get('provider')!='whatsapp'):
        _save(row,state='blocked')
        raise TenantLinearError('connect_pairing_create_unverified',409)
    try: channel_id = _uuid(channel.get('id'))
    except TenantLinearError:
        _save(row,state='blocked')
        raise TenantLinearError('connect_pairing_create_unverified',409) from None
    _save(row,channel_id=channel_id,state='awaiting_scan')
    return True


def _status(row, key):
    value = _request(key,'GET','/v1/channels/'+str(row['channel_id'])+'/status')
    if value.get('id') != str(row['channel_id']):
        raise TenantLinearError('connect_pairing_status_unverified',409)
    return value


def _connected(value):
    return (value.get('status')=='connected' and not value.get('phoneConflict')
        and isinstance(value.get('phone'),str) and bool(re.fullmatch(r'\+?[1-9][0-9]{6,14}',value['phone'])))


def pairing(profile, payload, callback_url):
    _validate(profile,payload)
    # Safe actor-owned receipt only: do not require inventory view or project it.
    with _connect() as conn:
        _schema(conn)
        conn.commit()
    with guard(_connect,profile,'connect','connect',data=payload):
        return _pairing(profile,payload,callback_url)


def _pairing(profile, payload, callback_url):
    tenant, actor, request_id, name = _validate(profile,payload)
    _owner(profile)
    with _tenant_connect_lock(tenant):
        # All rows and provider work remain under the existing company mutation lock.
        credentials = _stored_connect_credentials(profile)
        key = str(credentials['api_key'])
        fingerprint = hashlib.sha256(key.encode()).hexdigest()
        row = _read(tenant,actor,request_id)
        action = payload['action']
        if row is None and action=='pairing_start':
            # A new browser/tab resumes this actor's durable unfinished intent.
            row = _read(tenant,actor,request_id,pending=True)
        if row is None:
            if action!='pairing_start': raise TenantLinearError('connect_pairing_intent_not_found',404)
            _capability(key)
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO saas_connect_pairing_intents
                      (tenant_id,actor_id,request_id,provider_request_id,key_fingerprint,name) VALUES(%s::uuid,%s,%s::uuid,%s::uuid,%s,%s)''',
                      (tenant,actor,request_id,_provider_request_id(tenant,actor,request_id),fingerprint,name))
                conn.commit()
            row = _read(tenant,actor,request_id)
        if row['key_fingerprint']!=fingerprint:
            raise TenantLinearError('connect_pairing_key_changed',409)
        if action=='pairing_start' and 'name' in payload and row['name']!=name:
            raise TenantLinearError('connect_pairing_payload_changed',409)
        if row['state']=='blocked': raise TenantLinearError('connect_pairing_create_unverified',409)
        if row['state']=='complete': return _response(row,status='connected')
        if not row.get('channel_id'):
            if action!='pairing_start': return _response(row,unknown=True)
            if not _create(row,key): return _response(row,unknown=True)
        if action in {'pairing_start','pairing_refresh'}:
            current = _status(row,key)
            if current.get('status') == 'connected':
                if not _connected(current):
                    raise TenantLinearError('connect_pairing_not_connected',409)
                result = _response(row,status='connected',phone=current['phone'])
                result['pairing']['state']='connected'
                return result
            # QR GET initiates provider pairing, so it is explicit and write-guarded.
            value = _request(key,'GET','/v1/channels/'+str(row['channel_id'])+'/qr')
            qr=value.get('code')
            if not isinstance(qr,str) or len(qr)>32768:
                raise TenantLinearError('connect_pairing_qr_invalid',409)
            return _response(row,qr=qr)
        current = _status(row,key)
        if action=='pairing_complete':
            if not _connected(current): raise TenantLinearError('connect_pairing_not_connected',409)
            # Existing additive configuration keeps other channels, webhook and call events.
            # Do not call channel_status(reconnect=True): it activates before verification.
            _configure_locked(profile,{**payload,'api_key':key,'channel_ids':[str(row['channel_id'])]},callback_url,
                              expected_pairing={'id':str(row['channel_id']),'phone':current['phone']})
            _save(row,state='complete')
        response=_response(row,status=str(current.get('status') or 'unknown'),phone=str(current.get('phone') or '') if _connected(current) else '')
        if _connected(current) and row['state']!='complete': response['pairing']['state']='connected'
        return response
