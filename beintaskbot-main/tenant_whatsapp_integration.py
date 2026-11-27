"""Owner onboarding for the existing native source/gateway, not a second router.

Fresh sources have durable provenance. Transfers keep the original source ID
and require the old sender's transactional fence before staging the new device.
"""
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_whatsapp_native_rollout import serialized
import tenant_whatsapp_native_rollout as rollout
import tenant_call_sources as sources


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_whatsapp_onboarding (
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL, account_jid TEXT NOT NULL,
      generation TEXT NOT NULL, fresh BOOLEAN NOT NULL, created_by BIGINT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,channel_id), UNIQUE(tenant_id,account_jid),
      FOREIGN KEY(tenant_id,channel_id) REFERENCES saas_connect_channels(tenant_id,connect_channel_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_whatsapp_integration_removals (
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL,
      removed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,channel_id),
      FOREIGN KEY(tenant_id,channel_id)
        REFERENCES saas_connect_channels(tenant_id,connect_channel_id) ON DELETE CASCADE)''')


def removed_sources(cur,tenant):
    cur.execute("SELECT to_regclass('saas_whatsapp_integration_removals') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:return set()
    cur.execute('SELECT channel_id FROM saas_whatsapp_integration_removals WHERE tenant_id=%s',(tenant,))
    return {str(row['channel_id']) for row in cur.fetchall()}


def require_visible(cur,tenant,source):
    if source in removed_sources(cur,tenant):
        raise TenantLinearError('Источник недоступен.',404)


def owner(cur, profile):
    tenant, actor, policy = access(cur, profile, 'dialogs', action='update')
    if policy.profile.get('role') != 'owner':
        raise TenantLinearError('Управлять подключением WhatsApp может владелец компании.',403)
    return tenant,actor


def fresh_source(profile, source, account):
    # Only our transaction that created a NEW UUID can set fresh. Existing
    # channels can never acquire this exemption through the HTTP request.
    with rollout._connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile)
        cur.execute("SELECT to_regclass('saas_whatsapp_onboarding') IS NOT NULL AS ready")
        if not cur.fetchone()['ready']:return False
        cur.execute('''SELECT 1 FROM saas_whatsapp_onboarding WHERE tenant_id=%s::uuid
          AND channel_id=%s::uuid AND account_jid=%s AND fresh''',(tenant,source,account))
        return bool(cur.fetchone())


def legacy_inventory(profile):
    from tenant_connect_transport import _stored_connect_credentials,_request
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile)
        cur.execute("SELECT status FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'",(tenant,))
        integration=cur.fetchone()
        cur.execute('''SELECT c.connect_channel_id FROM saas_connect_channels c
          LEFT JOIN saas_whatsapp_native_routes r ON r.tenant_id=c.tenant_id AND r.channel_id=c.connect_channel_id
          WHERE c.tenant_id=%s::uuid AND r.channel_id IS NULL''',(tenant,))
        legacy=cur.fetchall()
    if not integration or integration['status']!='connected':
        if legacy:raise TenantLinearError('Сначала восстановите доступ к резервному Connect для проверки номеров.',409)
        return []
    rows=_request(_stored_connect_credentials(profile)['api_key'],'GET','/v1/channels').get('data')
    if not isinstance(rows,list) or any(not isinstance(x,dict) for x in rows):
        raise TenantLinearError('Список номеров Connect не подтверждён.',503)
    return rows


def inventory(profile):
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile)
        schema(cur)
        removed=removed_sources(cur,tenant)
        modes=rollout.modes(cur,tenant)
        rows=[(source,row) for source,row in modes.items() if source not in removed]
        registered_accounts={row['account_jid'] for row in modes.values() if row['registered']}
        cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
        ready=cur.fetchone()['ready']
        if ready:cur.execute('''SELECT o.channel_id,c.name,o.account_jid FROM saas_whatsapp_onboarding o
          JOIN saas_connect_channels c ON c.tenant_id=o.tenant_id AND c.connect_channel_id=o.channel_id
          LEFT JOIN saas_whatsapp_native_routes r ON r.tenant_id=o.tenant_id AND r.channel_id=o.channel_id
          WHERE o.tenant_id=%s::uuid AND r.channel_id IS NULL''',(tenant,))
        pending=[{'id':str(r['channel_id']),'name':r['name'],'phone':r['account_jid'].split('@')[0],
            'state':'preparing','messages_ready':False,'calls_ready':False,'activation_pending':True,'can_remove':True,
            'duplicate_pending':r['account_jid'] in registered_accounts}
            for r in (cur.fetchall() if ready else []) if str(r['channel_id']) not in removed]
    def status(item):
        source,row=item
        removable=not (row['active'] or row['enabled'])
        duplicate=removable and not row['registered'] and row['account_jid'] in registered_accounts
        if row.get('paused'):
            stopped=removable and row['state']=='stopped'
            return {'id':source,'name':row['name'],'phone':row['account_jid'].split('@')[0],
                'state':'stopped' if stopped else 'stopping','messages_ready':False,'calls_ready':False,
                'paused':True,'activation_pending':False,'can_remove':stopped,'duplicate_pending':duplicate}
        try:
            value=sources.source_status(profile,source)
            ready=value['state']=='ready' and row['enabled']
            return {'id':source,'name':row['name'],'phone':row['account_jid'].split('@')[0],
                'state':value['state'],'messages_ready':bool(ready),'calls_ready':bool(ready),
                'activation_pending':not row['enabled'],'can_remove':removable,'duplicate_pending':duplicate}
        except TenantLinearError as exc:
            if exc.status==403:raise
            return {'id':source,'name':row['name'],'phone':row['account_jid'].split('@')[0],
                'state':'unavailable','messages_ready':False,'calls_ready':False,
                'activation_pending':not row['enabled'],'can_remove':removable,'duplicate_pending':duplicate}
    with ThreadPoolExecutor(max_workers=4) as pool: result=list(pool.map(status,rows))
    with _connect() as conn,conn.cursor() as cur:owner(cur,profile)
    return {'channels':result+pending}


def check_setup():
    if any(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_'+f)!='1' for f in ('INBOX','PROJECTION','SEND')):
        raise TenantLinearError('Единый WhatsApp пока не включён на сервере.',409)
    sources.service()


@serialized
def begin(profile,phone,name):
    if not isinstance(phone,str) or not re.fullmatch(r'\+?[1-9][0-9]{5,21}',phone):
        raise TenantLinearError('Введите номер с кодом страны, например +994…',400)
    if not isinstance(name,str) or len(name.strip())>100:
        raise TenantLinearError('Название: не более 100 символов.',400)
    check_setup()
    phone=phone.lstrip('+')
    account=phone+'@s.whatsapp.net'
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile);rollout.migrate(cur);schema(cur)
        cur.execute('''SELECT channel_id,account_jid,generation FROM saas_whatsapp_onboarding
          WHERE tenant_id=%s::uuid AND account_jid=%s''',(tenant,account))
        pending=cur.fetchone()
        existing={key:row for key,row in rollout.modes(cur,tenant).items() if row['account_jid']==account}
        removed=removed_sources(cur,tenant)
        cur.execute('''SELECT 1 FROM saas_whatsapp_native_routes r
          JOIN saas_browser_call_sources s USING(tenant_id,channel_id)
          WHERE r.tenant_id=%s AND s.account_jid=%s AND r.legacy_fallback''',(tenant,account))
        fallback=bool(cur.fetchone())
    if fallback:
        raise TenantLinearError('У номера уже есть привязка к резервному Connect. Проверьте её перед подключением.',409)
    if pending:
        existing.setdefault(str(pending['channel_id']),None)
    # A previously registered gateway owns this number even if a failed
    # attempt left a second local record for it.
    registered=[key for key,row in existing.items() if row and row['registered']]
    candidates=registered or [key for key in existing if key not in removed] or list(existing)
    if len(candidates)>1:
        raise TenantLinearError('У номера несколько подключений. Проверьте существующие источники.',409)
    if candidates:
        source=candidates[0]
        pending=pending if pending and str(pending['channel_id'])==source else None
        result=continue_existing(profile,source,existing[source],pending,restore=source in removed)
        if source in removed:
            with _connect() as conn,conn.cursor() as cur:
                tenant,_=rollout.owner(cur,profile,source)
                cur.execute('DELETE FROM saas_whatsapp_integration_removals WHERE tenant_id=%s AND channel_id=%s',(tenant,source))
        return result

    matches=[row for row in legacy_inventory(profile) if str(row.get('phone') or '').lstrip('+')==phone]
    if len(matches)>1 or any(row.get('phoneConflict') for row in matches):
        raise TenantLinearError('Номер имеет неоднозначную привязку. Перенос остановлен.',409)
    old=matches[0] if matches else None
    if old and old.get('status')!='disconnected':
        raise TenantLinearError('Сначала отключите этот номер в Connect. История останется в прежнем источнике.',409)
    source=str(uuid.UUID(old['id'])) if old else str(uuid.uuid4())
    generation='app-'+uuid.uuid4().hex
    with _connect() as conn,conn.cursor() as cur:
        tenant,actor=owner(cur,profile)
        cur.execute('''SELECT count(*) AS n FROM saas_whatsapp_onboarding o
          LEFT JOIN saas_whatsapp_native_routes r USING(tenant_id,channel_id)
          LEFT JOIN saas_whatsapp_integration_removals d USING(tenant_id,channel_id)
          WHERE o.tenant_id=%s AND d.channel_id IS NULL AND NOT coalesce(r.enabled,FALSE)''',(tenant,))
        if cur.fetchone()['n']>=3:raise TenantLinearError('Завершите ранее начатые подключения.',409)
        if old:
            cur.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR UPDATE',(tenant,source))
            local=cur.fetchone()
            if not local:raise TenantLinearError('Источник резервной интеграции не найден в компании.',409)
            cur.execute('SELECT 1 FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,source))
            if cur.fetchone():raise TenantLinearError('У номера уже есть сервер звонков; требуется проверка перехода без активных звонков.',409)
        else:
            cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active) VALUES(%s,%s,%s,'disconnected',FALSE)",(tenant,source,name.strip() or '+'+phone))
        cur.execute('INSERT INTO saas_whatsapp_onboarding(tenant_id,channel_id,account_jid,generation,fresh,created_by) VALUES(%s,%s,%s,%s,%s,%s)',(tenant,source,account,generation,not bool(old),actor))
    return finish_staging(profile,source,account,generation)


def finish_staging(profile,source,account,generation):
    if not fresh_source(profile,source,account):
        phone=account.split('@')[0]
        from tenant_connect_transport import _stored_connect_credentials,_request
        result=_request(_stored_connect_credentials(profile)['api_key'],'POST','/v1/channels/'+source+'/native-transfer',{'phone':phone,'migration':generation})
        if result.get('channelId')!=source or result.get('status')!='disabled':
            raise TenantLinearError('Старый отправитель не подтвердил остановку.',409)
        # Reconcile the local selection flag only after the old sender is fenced.
        rollout.legacy_disabled(profile,source,account)
        with _connect() as conn,conn.cursor() as cur:
            tenant,_=rollout.owner(cur,profile,source)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('whatsapp-source-mode:'+tenant+':'+source,))
            cur.execute('''UPDATE saas_connect_channels c SET active=FALSE,status='disconnected',updated_at=now()
              FROM saas_whatsapp_onboarding o WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
                AND o.tenant_id=c.tenant_id AND o.channel_id=c.connect_channel_id
                AND o.generation=%s AND o.account_jid=%s AND NOT o.fresh
              RETURNING c.connect_channel_id''',(tenant,source,generation,account))
            if not cur.fetchone():raise TenantLinearError('native_setup_changed',409)
    # Reuse the existing staged-source protocol, same immutable gateway/token.
    # Already serialized by this endpoint, so avoid a second tenant lock.
    rollout.stage.__wrapped__(profile,source,account,generation)
    return sources.source_status(profile,source)


def continue_existing(profile,source,mode,pending,*,restore=False):
    if mode and mode['paused']:
        from tenant_whatsapp_source_control import resume
        return resume.__wrapped__(profile,source,restore=restore)
    if mode and (mode['enabled'] or mode['registered']):
        status=sources.source_status(profile,source)
        if mode['enabled'] or status['state']!='pairing' or status.get('qr'):
            return status
        # An unpaired engine issues a new QR only after setup is requested.
        # Stage the known gateway and generation again, including for a cutover
        # source that never had an onboarding row.
        check_setup()
        rollout.stage.__wrapped__(profile,source,mode['account_jid'],mode['generation'])
        return sources.source_status(profile,source)
    if pending:
        return finish_staging(profile,source,pending['account_jid'],pending['generation'])
    if mode:
        rollout.stage.__wrapped__(profile,source,mode['account_jid'],mode['generation'])
        return sources.source_status(profile,source)
    raise TenantLinearError('Источник недоступен.',404)


@serialized
def continue_pairing(profile,source):
    source=str(uuid.UUID(source))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=rollout.owner(cur,profile,source)
        require_visible(cur,tenant,source)
        all_modes=rollout.modes(cur,tenant)
        mode=all_modes.get(source)
        schema(cur)
        cur.execute('''SELECT o.account_jid,o.generation FROM saas_whatsapp_onboarding o
          LEFT JOIN saas_whatsapp_native_routes r USING(tenant_id,channel_id)
          WHERE o.tenant_id=%s AND o.channel_id=%s AND (r.channel_id IS NULL OR NOT r.legacy_fallback)''',(tenant,source))
        pending=cur.fetchone()
    if mode and (mode['enabled'] or mode['registered']):
        return continue_existing(profile,source,mode,pending)
    account=(mode or pending or {}).get('account_jid')
    if any(row['registered'] and key!=source and row['account_jid']==account for key,row in all_modes.items()):
        raise TenantLinearError('У номера уже есть другой источник. Продолжите подключение в его карточке.',409)
    check_setup()
    return continue_existing(profile,source,mode,pending)


def pairing(profile,source):
    source=str(uuid.UUID(source))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=rollout.owner(cur,profile,source)
        require_visible(cur,tenant,source)
        mode=rollout.modes(cur,tenant).get(source)
        if not mode:raise TenantLinearError('Источник недоступен.',404)
        if mode['paused']:raise TenantLinearError('Номер отключён. Вернитесь к списку номеров.',409)
    return sources.source_status(profile,source)


@serialized
def activate(profile,source):
    source=str(uuid.UUID(source))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=rollout.owner(cur,profile,source)
        require_visible(cur,tenant,source)
        cur.execute('''SELECT generation,enabled FROM saas_whatsapp_native_routes
          WHERE tenant_id=%s AND channel_id=%s AND NOT paused AND NOT legacy_fallback''',(tenant,source))
        row=cur.fetchone()
        if not row:raise TenantLinearError('Источник недоступен.',404)
    if not row['enabled']:rollout.activate.__wrapped__(profile,source,row['generation'])
    return pairing(profile,source)
