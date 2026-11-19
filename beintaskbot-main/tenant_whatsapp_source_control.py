"""Owner-requested stop/resume of existing native sources; no data deletion."""
import uuid
from tenant_platform import _connect, _fernet
from tenant_linear_policy import TenantLinearError
from tenant_whatsapp_native_rollout import serialized, modes, migrate
from tenant_whatsapp_integration import owner, pairing
from tenant_whatsapp_native_transition import source_lock
from tenant_call_sources import remote


def scope(tenant,source,row):
    return {'tenant':tenant,'source':source,'generation':row['generation'],'account':row['account_jid']}


@serialized
def disconnect(profile, sources, *, all_sources=False):
    if not isinstance(sources,list) or not 1<=len(sources)<=16:raise TenantLinearError('Выберите номера для отключения.',400)
    sources=sorted({str(uuid.UUID(s)) for s in sources})
    prepared=[]
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile);migrate(cur)
        current=modes(cur,tenant)
        if all_sources and set(sources)!=set(current):
            raise TenantLinearError('Список номеров изменился. Обновите интеграцию.',409)
        for source in sources:
            mode=current.get(source)
            if not mode:raise TenantLinearError('Источник недоступен.',404)
            if mode.get('paused') and not mode['active']:continue
            row=source_lock(cur,tenant,source)
            cur.execute("SELECT 1 FROM saas_browser_calls WHERE tenant_id=%s AND gateway_id=%s AND state NOT IN ('ended','failed')",(tenant,row['gateway_id']))
            if cur.fetchone():raise TenantLinearError('Сначала завершите текущий звонок этого номера.',409)
            cur.execute("""SELECT 1 FROM saas_browser_call_commands q JOIN saas_browser_calls c ON c.id=q.call_id
                WHERE c.tenant_id=%s AND c.gateway_id=%s AND q.state<>'done' """,(tenant,row['gateway_id']))
            if cur.fetchone():raise TenantLinearError('Завершение операций номера ещё не подтверждено. Повторите позже.',409)
            cur.execute('SELECT generation FROM saas_whatsapp_native_routes WHERE tenant_id=%s AND channel_id=%s',(tenant,source))
            row={**row,**cur.fetchone()}
            status=remote('/v1/sources/'+str(row['gateway_id']))
            if status.get('messaging_generation')!=row['generation']:
                raise TenantLinearError('Сервер не подтвердил текущее подключение номера.',409)
            if status.get('stopped') is not True:
                diag=status.get('diagnostics') or {};messages=status.get('messaging_diagnostics') or {}
                if (any(type(diag.get(k)) is not int or diag[k]!=0 for k in ('active_registry_calls','provider_start_requested','audio_attached'))
                    or any(type(messages.get(k)) is not int or messages[k]!=0 for k in ('unknown_sends','pending_events'))):
                    raise TenantLinearError('У номера есть незавершённые звонки, отправки или синхронизация. Повторите после завершения.',409)
            prepared.append((source,row))
        # Fence all selected admissions atomically before stopping any process.
        for source,row in prepared:
            cur.execute('UPDATE saas_whatsapp_native_routes SET enabled=FALSE,paused=TRUE WHERE tenant_id=%s AND channel_id=%s',(tenant,source))
    for source,row in prepared:
        expected=scope(tenant,source,row)
        result=remote('/v1/sources/'+str(row['gateway_id'])+'/disconnect',{'scope':expected})
        if result.get('stopped') is not True or result.get('scope')!=expected:
            raise TenantLinearError('Остановка ещё не подтверждена. Повторите отключение; новые операции заблокированы.',409)
        with _connect() as conn,conn.cursor() as cur:
            owner(cur,profile)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('whatsapp-source-mode:'+tenant+':'+source,))
            cur.execute('''SELECT 1 FROM saas_whatsapp_native_routes WHERE tenant_id=%s AND channel_id=%s
                AND generation=%s AND paused AND NOT enabled FOR UPDATE''',(tenant,source,row['generation']))
            if not cur.fetchone():raise TenantLinearError('Подключение изменилось. Обновите страницу.',409)
            cur.execute("UPDATE saas_connect_channels SET active=FALSE,status='disconnected',updated_at=now() WHERE tenant_id=%s AND connect_channel_id=%s",(tenant,source))
            cur.execute('UPDATE saas_whatsapp_capture_generations SET enabled=FALSE,projection_enabled=FALSE WHERE tenant_id=%s AND channel_id=%s AND generation=%s',(tenant,source,row['generation']))
            cur.execute("UPDATE saas_browser_call_sources SET state='stopped',updated_at=now() WHERE tenant_id=%s AND channel_id=%s",(tenant,source))
    return {'stopped':sources}


@serialized
def resume(profile, source):
    source=str(uuid.UUID(source))
    with _connect() as conn,conn.cursor() as cur:
        tenant,_=owner(cur,profile)
        cur.execute('''SELECT s.*,r.generation,r.paused,c.active FROM saas_whatsapp_native_routes r
          JOIN saas_browser_call_sources s USING(tenant_id,channel_id)
          JOIN saas_connect_channels c ON c.tenant_id=r.tenant_id AND c.connect_channel_id=r.channel_id
          WHERE r.tenant_id=%s AND r.channel_id=%s''',(tenant,source))
        row=cur.fetchone()
        if not row or not row['paused'] or row['active']:
            raise TenantLinearError('Сначала дождитесь подтверждённого отключения номера.',409)
    result=remote('/v1/sources/'+str(row['gateway_id']),{'tenant_id':tenant,'channel_id':source,
        'account_jid':row['account_jid'],'gateway_token':_fernet().decrypt(bytes(row['encrypted_token'])).decode(),
        'messaging_generation':row['generation'],'resume':True})
    if result.get('public_key')!=row['public_key']:raise TenantLinearError('Сервер не подтвердил прежнее подключение.',409)
    with _connect() as conn,conn.cursor() as cur:
        owner(cur,profile)
        cur.execute('UPDATE saas_whatsapp_native_routes SET paused=FALSE WHERE tenant_id=%s AND channel_id=%s AND generation=%s AND paused AND NOT enabled RETURNING channel_id',(tenant,source,row['generation']))
        if not cur.fetchone():raise TenantLinearError('Подключение изменилось.',409)
    return pairing(profile,source)

