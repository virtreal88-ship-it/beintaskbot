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
        rows=list(rollout.modes(cur,tenant).items())
        cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
        ready=cur.fetchone()['ready']
        if ready:cur.execute('''SELECT o.channel_id,c.name,o.account_jid FROM saas_whatsapp_onboarding o
          JOIN saas_connect_channels c ON c.tenant_id=o.tenant_id AND c.connect_channel_id=o.channel_id
          LEFT JOIN saas_whatsapp_native_routes r ON r.tenant_id=o.tenant_id AND r.channel_id=o.channel_id
          WHERE o.tenant_id=%s::uuid AND r.channel_id IS NULL''',(tenant,))
        pending=[{'id':str(r['channel_id']),'name':r['name'],'phone':r['account_jid'].split('@')[0],
            'state':'preparing','messages_ready':False,'calls_ready':False,'activation_pending':True} for r in (cur.fetchall() if ready else [])]
    def status(item):
        source,row=item
        if row.get('paused'):
            return {'id':source,'name':row['name'],'phone':row['account_jid'].split('@')[0],
                'state':'stopping' if row['active'] else 'stopped','messages_ready':False,'calls_ready':False,
                'paused':True,'activation_pending':False}
        try:
            value=sources.source_status(profile,source)
            ready=value['state']=='ready' and row['enabled']
            return {'id':source,'name':row['name'],'phone':row['account_jid'].split('@')[0],
                'state':value['state'],'messages_ready':bool(ready),'calls_ready':bool(ready),
                'activation_pending':not row['enabled']}
        except TenantLinearError as exc:
            if exc.status==403:raise
            return {'id':source,'name':row['name'],'phone':row['account_jid'].split('@')[0],
                'state':'unavailable','messages_ready':False,'calls_ready':False,'activation_pending':not row['enabled']}
    with ThreadPoolExecutor(max_workers=4) as pool: result=list(pool.map(status,rows))
    with _connect() as conn,conn.cursor() as cur:owner(cur,profile)
    return {'channels':result+pending}


@serialized
def begin(profile,phone,name):
    if not isinstance(phone,str) or not re.fullmatch(r'\+?[1-9][0-9]{5,21}',phone):
        raise TenantLinearError('Введите номер с кодом страны, например +994…',400)
    if not isinstance(name,str) or len(name.strip())>100:
        raise TenantLinearError('Название: не более 100 символов.',400)
    if any(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_'+f)!='1' for f in ('INBOX','PROJECTION','SEND')):
        raise TenantLinearError('Единый WhatsApp пока не включён на сервере.',409)
    sources.service()
    account=phone.lstrip('+')+'@s.whatsapp.net'
    with _connect() as conn,conn.cursor() as cur:
        tenant,actor=owner(cur,profile);rollout.migrate(cur);schema(cur)
        cur.execute('''SELECT channel_id,generation FROM saas_whatsapp_onboarding
          WHERE tenant_id=%s::uuid AND account_jid=%s''',(tenant,account))
        pending=cur.fetchone()
        existing=[(key,row) for key,row in rollout.modes(cur,tenant).items() if row['account_jid']==account]
    if existing and existing[0][1]['enabled']:
        return pairing(profile,existing[0][0])
    if existing and existing[0][1].get('paused'):
        raise TenantLinearError('Номер отключён. Нажмите «Подключить снова» в его карточке.',409)
    if pending:
        source=str(pending['channel_id']);generation=pending['generation']
    else:
        matches=[row for row in legacy_inventory(profile) if str(row.get('phone') or '').lstrip('+')==phone.lstrip('+')]
        if len(matches)>1 or any(row.get('phoneConflict') for row in matches):
            raise TenantLinearError('Номер имеет неоднозначную привязку. Перенос остановлен.',409)
        old=matches[0] if matches else None
        if old and old.get('status')!='disconnected':
            raise TenantLinearError('Сначала отключите этот номер в Connect. История останется в прежнем источнике.',409)
        source=str(uuid.UUID(old['id'])) if old else str(uuid.uuid4())
        generation='app-'+uuid.uuid4().hex
        with _connect() as conn,conn.cursor() as cur:
            tenant,actor=owner(cur,profile)
            cur.execute('SELECT count(*) AS n FROM saas_whatsapp_onboarding o LEFT JOIN saas_whatsapp_native_routes r USING(tenant_id,channel_id) WHERE o.tenant_id=%s::uuid AND NOT coalesce(r.enabled,FALSE)',(tenant,))
            if cur.fetchone()['n']>=3:raise TenantLinearError('Завершите ранее начатые подключения.',409)
            if old:
                cur.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR UPDATE',(tenant,source))
                local=cur.fetchone()
                if not local:raise TenantLinearError('Источник резервной интеграции не найден в компании.',409)
                cur.execute('SELECT 1 FROM saas_browser_call_sources WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,source))
                if cur.fetchone():raise TenantLinearError('У номера уже есть сервер звонков; требуется проверка перехода без активных звонков.',409)
            else:
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active) VALUES(%s,%s,%s,'disconnected',FALSE)",(tenant,source,name.strip() or '+'+phone.lstrip('+')))
            cur.execute('INSERT INTO saas_whatsapp_onboarding(tenant_id,channel_id,account_jid,generation,fresh,created_by) VALUES(%s,%s,%s,%s,%s,%s)',(tenant,source,account,generation,not bool(old),actor))
    if not fresh_source(profile,source,account):
        from tenant_connect_transport import _stored_connect_credentials,_request
        result=_request(_stored_connect_credentials(profile)['api_key'],'POST','/v1/channels/'+source+'/native-transfer',{'phone':phone.lstrip('+'),'migration':generation})
        if result.get('channelId')!=source or result.get('status')!='disabled':
            raise TenantLinearError('Старый отправитель не подтвердил остановку.',409)
        # Disconnecting in Connect itself does not clear the Worker's local
        # selection flag. Reconcile it only AFTER the transactional dispatch
        # fence and a fresh exact-account status check, never from inventory.
        rollout.legacy_disabled(profile,source,account)
        with _connect() as conn,conn.cursor() as cur:
            tenant,_=owner(cur,profile)
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
    return pairing(profile,source)


def pairing(profile,source):
    source=str(uuid.UUID(source))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile)
        if source not in rollout.modes(cur,tenant):raise TenantLinearError('Источник недоступен.',404)
    return sources.source_status(profile,source)


def activate(profile,source):
    source=str(uuid.UUID(source))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile)
        cur.execute('SELECT generation,enabled FROM saas_whatsapp_native_routes WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(tenant,source))
        row=cur.fetchone()
        if not row:raise TenantLinearError('Источник недоступен.',404)
    if not row['enabled']:rollout.activate(profile,source,row['generation'])
    return pairing(profile,source)
