"""Operator-only, source-preserving transition of an existing call device.

No public endpoint, source deletion, QR pairing or provider send. A durable
disabled native route fences Worker admissions before the engine restarts.
Capture must be evidenced before Connect is frozen and sending is activated.
"""
import re
import uuid

from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
from tenant_whatsapp_native_rollout import owner, serialized, legacy_disabled
from tenant_call_sources import remote


def admission(cur, tenant, source):
    """Called with browser-call lock held; closes check-to-start cutover race."""
    cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:return
    cur.execute('SELECT enabled,legacy_fallback FROM saas_whatsapp_native_routes WHERE tenant_id=%s::uuid AND channel_id=%s::uuid', (tenant,source))
    row=cur.fetchone()
    if row and not row['enabled'] and not row['legacy_fallback']:
        raise TenantLinearError('Номер переключается на единый WhatsApp. Повторите после завершения переключения.',409)


def source_lock(cur,tenant,source):
    from tenant_browser_calls import _lock
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('whatsapp-source-mode:'+tenant+':'+source,))
    cur.execute('SELECT gateway_id FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,source))
    first=cur.fetchone()
    if not first:raise TenantLinearError('native_transition_source_missing',409)
    _lock(cur,{'tenant_id':tenant,'gateway_id':str(first['gateway_id'])})
    cur.execute('''SELECT s.* FROM saas_browser_call_sources s JOIN saas_connect_channels c
      ON c.tenant_id=s.tenant_id AND c.connect_channel_id=s.channel_id
      WHERE s.tenant_id=%s::uuid AND s.channel_id=%s::uuid AND s.enabled AND c.active
        AND s.gateway_id=%s::uuid AND s.public_key<>'' FOR UPDATE OF s,c''',(tenant,source,first['gateway_id']))
    row=cur.fetchone()
    if not row:raise TenantLinearError('native_transition_source_changed',409)
    return row


def quiescent(cur,tenant,row):
    cur.execute("SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s::uuid AND gateway_id=%s::uuid AND state NOT IN ('ended','failed')",(tenant,row['gateway_id']))
    if cur.fetchone():raise TenantLinearError('native_transition_call_not_closed',409)
    cur.execute('''SELECT 1 FROM saas_browser_call_commands q JOIN saas_browser_calls c ON c.id=q.call_id
      WHERE c.tenant_id=%s::uuid AND c.gateway_id=%s::uuid AND q.state<>'done' ''',(tenant,row['gateway_id']))
    if cur.fetchone():raise TenantLinearError('native_transition_pending_command',409)
    status=remote('/v1/sources/'+str(row['gateway_id']))
    diagnostics=status.get('diagnostics') or {}
    if (status.get('connected') is not True or status.get('account_jid')!=row['account_jid']
        or any(type(diagnostics.get(k)) is not int or diagnostics[k]!=0 for k in
               ('active_registry_calls','provider_start_requested','audio_attached'))):
        raise TenantLinearError('native_transition_runtime_unconfirmed',409)
    return status


@serialized
def prepare(profile,source,account,generation):
    source=str(uuid.UUID(str(source)))
    if (not isinstance(generation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',generation)
        or not isinstance(account,str) or not re.fullmatch(r'[1-9][0-9]{5,21}@s\.whatsapp\.net',account)):
        raise TenantLinearError('native_transition_invalid',400)
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        row=source_lock(cur,tenant,source)
        if row['account_jid']!=account:raise TenantLinearError('native_transition_source_changed',409)
        cur.execute('''SELECT r.generation,r.enabled,g.enabled AS capture_enabled,g.gateway_id,g.account_jid
          FROM saas_whatsapp_native_routes r JOIN saas_whatsapp_capture_generations g
          USING(tenant_id,channel_id,generation) WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid''',(tenant,source))
        previous=cur.fetchone()
        if previous:
            if (previous['enabled'] or previous['capture_enabled'] or previous['generation']!=generation
                or previous['gateway_id']!=row['gateway_id'] or previous['account_jid']!=account):
                raise TenantLinearError('native_transition_changed',409)
        else:
            from tenant_connect_transport import _stored_connect_credentials,_request
            old=_request(_stored_connect_credentials(profile)['api_key'],'GET','/v1/channels/'+source+'/status')
            if (old.get('id')!=source or old.get('status')!='connected' or old.get('phoneConflict')
                or str(old.get('phone') or '').lstrip('+')+'@s.whatsapp.net'!=account):
                raise TenantLinearError('native_transition_legacy_changed',409)
        status=quiescent(cur,tenant,row)
        if status.get('messaging_generation') not in (None,generation):
            raise TenantLinearError('native_transition_generation_changed',409)
        if not previous:
            cur.execute('''INSERT INTO saas_whatsapp_capture_generations
              (tenant_id,channel_id,generation,gateway_id,account_jid) VALUES(%s,%s,%s,%s,%s)''',
              (tenant,source,generation,row['gateway_id'],account))
            cur.execute('INSERT INTO saas_whatsapp_native_routes(tenant_id,channel_id,generation) VALUES(%s,%s,%s)',(tenant,source,generation))
        # Public identity only. No device key or token leaves the service.
        return {k:str(v) for k,v in {'gateway_id':row['gateway_id'],'tenant_id':tenant,
            'channel_id':source,'account_jid':account,'public_key':row['public_key'],'generation':generation}.items()}


@serialized
def enable_capture(profile,source,generation):
    source=str(uuid.UUID(str(source)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        row=source_lock(cur,tenant,source)
        status=quiescent(cur,tenant,row)
        if status.get('messaging_generation')!=generation:
            raise TenantLinearError('native_transition_generation_unconfirmed',409)
        cur.execute('''UPDATE saas_whatsapp_capture_generations g SET enabled=TRUE
          FROM saas_whatsapp_native_routes r WHERE r.tenant_id=g.tenant_id AND r.channel_id=g.channel_id
            AND r.generation=g.generation AND NOT r.enabled AND NOT g.projection_enabled
            AND g.tenant_id=%s::uuid AND g.channel_id=%s::uuid AND g.generation=%s
            AND g.gateway_id=%s::uuid AND g.account_jid=%s RETURNING g.generation''',
          (tenant,source,generation,row['gateway_id'],row['account_jid']))
        if not cur.fetchone():raise TenantLinearError('native_transition_changed',409)
    return {'source':source,'generation':generation,'state':'capturing'}


@serialized
def activate(profile,source,generation):
    import os
    if any(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag)!='1' for flag in ('INBOX','PROJECTION','SEND')):
        raise TenantLinearError('native_flags_disabled',409)
    source=str(uuid.UUID(str(source)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        row=source_lock(cur,tenant,source)
        status=quiescent(cur,tenant,row)
        if status.get('messaging_generation')!=generation:
            raise TenantLinearError('native_transition_generation_unconfirmed',409)
        cur.execute('''SELECT r.enabled,g.projection_enabled,g.stored_sequence FROM saas_whatsapp_native_routes r
          JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
          WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid AND r.generation=%s AND g.enabled
            AND g.gateway_id=%s::uuid AND g.account_jid=%s FOR UPDATE OF r,g''',
          (tenant,source,generation,row['gateway_id'],row['account_jid']))
        capture=cur.fetchone()
        if not capture or capture['stored_sequence']<1:
            raise TenantLinearError('native_transition_capture_unconfirmed',409)
        require_inbound_evidence(cur,tenant,source,generation,row['account_jid'])
        legacy_disabled(profile,source,row['account_jid'])
        cur.execute('UPDATE saas_whatsapp_capture_generations SET projection_enabled=TRUE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s',(tenant,source,generation))
        cur.execute('UPDATE saas_whatsapp_native_routes SET enabled=TRUE,legacy_fallback=FALSE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s',(tenant,source,generation))
    return {'source':source,'generation':generation,'state':'active'}


@serialized
def fence_for_rollback(profile,source,generation):
    """Stops new sends first. An unknown result retains the committed fence."""
    source=str(uuid.UUID(str(source)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        row=source_lock(cur,tenant,source)
        quiescent(cur,tenant,row)
        cur.execute('''UPDATE saas_whatsapp_native_routes SET enabled=FALSE,legacy_fallback=FALSE
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s RETURNING generation''',(tenant,source,generation))
        if not cur.fetchone():raise TenantLinearError('native_transition_changed',409)
        # Retain and drain already captured events through normal scoped/dedup
        # ingestion. This does not authorize a new outgoing provider request.
        cur.execute('''UPDATE saas_whatsapp_capture_generations SET projection_enabled=TRUE
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND enabled''',(tenant,source,generation))
    return {'source':source,'generation':generation,'state':'fenced'}


@serialized
def rollback_readiness(profile,source,generation):
    """Read-only evidence after fence; never authorizes retrying an old send."""
    source=str(uuid.UUID(str(source)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        row=source_lock(cur,tenant,source)
        status=quiescent(cur,tenant,row)
        cur.execute('''SELECT r.enabled,r.legacy_fallback,g.stored_sequence,
          g.projection_enabled FROM saas_whatsapp_native_routes r JOIN saas_whatsapp_capture_generations g
          USING(tenant_id,channel_id,generation) WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid
          AND r.generation=%s AND g.gateway_id=%s::uuid AND g.account_jid=%s''',
          (tenant,source,generation,row['gateway_id'],row['account_jid']))
        capture=cur.fetchone()
        if not capture or capture['enabled']:
            raise TenantLinearError('native_rollback_not_fenced',409)
        if status.get('messaging_generation')==generation:
            proof=status.get('messaging_diagnostics') or {}
            if type(proof.get('unknown_sends')) is not int or proof['unknown_sends']!=0:
                raise TenantLinearError('native_rollback_unknown_sends',409)
            if type(proof.get('pending_events')) is not int or proof['pending_events']!=0:
                raise TenantLinearError('native_rollback_projection_pending',409)
        elif status.get('messaging_generation') is not None or capture['stored_sequence']!=0:
            raise TenantLinearError('native_rollback_generation_changed',409)
    return {'source':source,'generation':generation,'state':'fenced_and_drained'}


@serialized
def restore_legacy(profile,source,generation):
    """After explicit Connect thaw. Keep native history and dedup marker."""
    # Outer decorator already owns the tenant maintenance lock.
    rollback_readiness.__wrapped__(profile,source,generation)
    source=str(uuid.UUID(str(source)))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        row=source_lock(cur,tenant,source)
        from tenant_connect_transport import _stored_connect_credentials,_request
        old=_request(_stored_connect_credentials(profile)['api_key'],'GET','/v1/channels/'+source+'/status')
        if (old.get('id')!=source or old.get('status')!='connected' or old.get('phoneConflict')
            or str(old.get('phone') or '').lstrip('+')+'@s.whatsapp.net'!=row['account_jid']):
            raise TenantLinearError('native_rollback_legacy_unconfirmed',409)
        cur.execute('''UPDATE saas_whatsapp_native_routes SET legacy_fallback=TRUE
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND NOT enabled RETURNING generation''',(tenant,source,generation))
        if not cur.fetchone():raise TenantLinearError('native_transition_changed',409)
    return {'source':source,'generation':generation,'state':'legacy_restored'}


def require_inbound_evidence(cur,tenant,source,generation,account):
    # A group metadata read or our own send does not prove inbound reception.
    # Reuse authenticated, source-bound journal evidence; never trust a counter.
    from tenant_whatsapp_native_projection import open_stored, message_event
    scope={'tenant':tenant,'source':source,'generation':generation,'account':account}
    cur.execute('SELECT * FROM saas_whatsapp_native_inbox WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s ORDER BY sequence DESC LIMIT 100',(tenant,source,generation))
    for event in cur.fetchall():
        payload=open_stored(event,scope)
        if payload.get('kind')=='message' and payload.get('from_me') is False:
            try:
                message_event(scope,event['event_id'],payload,str(uuid.uuid5(uuid.NAMESPACE_URL,event['event_id'])))
            except TenantLinearError as error:
                if str(error)=='native_event_not_projectable':
                    continue # Retained control/status is not inbound chat proof.
                raise
            return
    raise TenantLinearError('native_transition_inbound_unconfirmed',409)
