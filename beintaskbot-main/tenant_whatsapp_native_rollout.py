"""Explicit operator rollout of an existing, disabled source; no public endpoint.

Stage does not activate CRM, send messages or copy a device. The operator must
stop any isolated test owner before pairing. Activation requires a disabled
legacy source and the new gateway's confirmed exact account. Existing production
call-only engines need a separate maintenance transition, never an implicit
restart here. Old messages, permissions, credentials and source IDs are retained.
"""
import hashlib
import os
import re
import secrets
import uuid
from functools import wraps

from tenant_platform import _connect, _fernet
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_call_sources import remote


def migrate(cur):
    from tenant_whatsapp_native_inbox import migrate as inbox
    from tenant_whatsapp_native_consumer import migrate as consumer
    from tenant_whatsapp_native_transport import migrate as transport
    inbox(cur);consumer(cur);transport(cur)


def owner(cur,profile,source):
    tenant,actor,policy=access(cur,profile,'dialogs',action='update')
    if policy.profile.get('role')!='owner':raise TenantLinearError('native_owner_required',403)
    from tenant_dialog_access import require_source
    require_source(cur,tenant,policy,'connect',source,'update')
    return tenant,actor


def legacy_disabled(profile,source,account):
    from tenant_connect_transport import _stored_connect_credentials,_request
    key=_stored_connect_credentials(profile)['api_key']
    result=_request(key,'GET','/v1/channels/'+source+'/status')
    if (result.get('id')!=source or result.get('status')!='disabled'
            or result.get('phoneConflict') or str(result.get('phone') or '').lstrip('+')+'@s.whatsapp.net'!=account):
        raise TenantLinearError('native_legacy_not_disabled',409)


def serialized(function):
    @wraps(function)
    def run(profile,*args,**kwargs):
        from tenant_connect_transport import _tenant_connect_lock
        with _tenant_connect_lock(str(uuid.UUID(str(profile['tenant_id'])))):
            return function(profile,*args,**kwargs)
    return run


@serialized
def stage(profile,source,account,generation):
    source=str(uuid.UUID(str(source)))
    if (not isinstance(generation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',generation)
            or not re.fullmatch(r'[1-9][0-9]{5,21}@s\.whatsapp\.net',account)):
        raise TenantLinearError('native_setup_invalid',400)
    with _connect() as conn,conn.cursor() as cur:
        tenant,actor=owner(cur,profile,source)
    legacy_disabled(profile,source,account)
    with _connect() as conn,conn.cursor() as cur:
        owner(cur,profile,source)
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('whatsapp-source-mode:'+tenant+':'+source,))
        cur.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR UPDATE',(tenant,source))
        channel=cur.fetchone()
        if not channel or channel['active']:raise TenantLinearError('native_source_not_staged',409)
        cur.execute('SELECT * FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid FOR UPDATE',(tenant,source))
        row=cur.fetchone()
        if row:
            cur.execute('SELECT generation,enabled FROM saas_whatsapp_native_routes WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,source))
            previous=cur.fetchone()
            if not previous or previous['enabled'] or previous['generation']!=generation or row['account_jid']!=account:
                raise TenantLinearError('native_existing_source_requires_transition',409)
        else:
            token=secrets.token_urlsafe(48)
            cur.execute('''INSERT INTO saas_browser_call_sources
              (tenant_id,channel_id,gateway_id,account_jid,token_sha256,encrypted_token,created_by)
              VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
              (tenant,source,uuid.uuid4(),account,hashlib.sha256(token.encode()).hexdigest(),_fernet().encrypt(token.encode()),actor))
            row=cur.fetchone()
            cur.execute('''INSERT INTO saas_whatsapp_capture_generations
              (tenant_id,channel_id,generation,gateway_id,account_jid) VALUES(%s,%s,%s,%s,%s)''',
              (tenant,source,generation,row['gateway_id'],account))
            cur.execute('''INSERT INTO saas_whatsapp_native_routes(tenant_id,channel_id,generation)
              VALUES(%s,%s,%s)''',(tenant,source,generation))
    result=remote('/v1/sources/'+str(row['gateway_id']),{'tenant_id':tenant,'channel_id':source,
        'account_jid':account,'gateway_token':_fernet().decrypt(bytes(row['encrypted_token'])).decode(),
        'messaging_generation':generation})
    public=result.get('public_key')
    if not isinstance(public,str) or not re.fullmatch('[0-9a-f]{64}',public):raise TenantLinearError('native_gateway_key_invalid',503)
    with _connect() as conn,conn.cursor() as cur:
        owner(cur,profile,source)
        cur.execute('''UPDATE saas_browser_call_sources SET public_key=%s,state='pairing',updated_at=now()
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND gateway_id=%s::uuid
          AND account_jid=%s AND (public_key='' OR public_key=%s) RETURNING gateway_id''',
          (public,tenant,source,row['gateway_id'],account,public))
        if not cur.fetchone():raise TenantLinearError('native_source_changed',409)
    return {'source':source,'generation':generation,'state':'pairing'}


def staged(cur,tenant,source):
    cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:return False
    cur.execute('''SELECT 1 FROM saas_whatsapp_native_routes r
      JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
      JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid AND NOT r.enabled AND NOT g.enabled
        AND s.enabled''',(tenant,source))
    return bool(cur.fetchone())


def modes(cur,tenant):
    cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:return {}
    cur.execute('''SELECT r.channel_id,r.enabled,s.account_jid FROM saas_whatsapp_native_routes r
      JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
      JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      WHERE r.tenant_id=%s::uuid''',(tenant,))
    return {str(row['channel_id']):row for row in cur.fetchall()}


@serialized
def activate(profile,source,generation):
    source=str(uuid.UUID(str(source)))
    if any(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag)!='1' for flag in ('INBOX','PROJECTION','SEND')):
        raise TenantLinearError('native_flags_disabled',409)
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile,source)
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('whatsapp-source-mode:'+tenant+':'+source,))
        cur.execute('''SELECT s.* FROM saas_browser_call_sources s
          JOIN saas_whatsapp_native_routes r USING(tenant_id,channel_id)
          JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
          WHERE s.tenant_id=%s::uuid AND s.channel_id=%s::uuid AND r.generation=%s
            AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
            AND s.enabled AND s.public_key<>'' AND NOT r.enabled AND NOT g.enabled
          FOR UPDATE OF r,g,s''',(tenant,source,generation))
        row=cur.fetchone()
        if not row:raise TenantLinearError('native_setup_changed',409)
        # Read the real configured legacy endpoint while the source mode lock
        # excludes prepared old sends. A boolean supplied by an operator is not proof.
        legacy_disabled(profile,source,row['account_jid'])
        status=remote('/v1/sources/'+str(row['gateway_id']))
        if status.get('connected') is not True or status.get('account_jid')!=row['account_jid']:
            raise TenantLinearError('native_pairing_unconfirmed',409)
        cur.execute("UPDATE saas_browser_call_sources SET state='ready',updated_at=now() WHERE tenant_id=%s::uuid AND channel_id=%s::uuid",(tenant,source))
        cur.execute("UPDATE saas_connect_channels SET active=TRUE,status='connected',updated_at=now() WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(tenant,source))
        cur.execute('UPDATE saas_whatsapp_capture_generations SET enabled=TRUE,projection_enabled=TRUE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s',(tenant,source,generation))
        cur.execute('UPDATE saas_whatsapp_native_routes SET enabled=TRUE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s',(tenant,source,generation))
    return {'source':source,'generation':generation,'state':'active'}
