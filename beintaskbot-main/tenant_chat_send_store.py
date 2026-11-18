"""Durable, tenant/actor-specific receipts reserved before external writes."""
import json
import uuid
from tenant_platform import _connect, _read_tenant_workflow, _fernet
from tenant_chat_send_schema import ensure_schema
from tenant_chat_send_policy import fingerprint
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_connect_transport import connect_route


def authority(cur, session, lead_id, *, lifecycle_receipt=None, lifecycle_status=None):
    tenant, actor = str(uuid.UUID(str(session['tenant_id']))), int(session['telegram_id'])
    from tenant_dialog_rollout import guard
    guard(cur, tenant)
    from tenant_scoped_access_store import read_access_profile
    profile=read_access_profile(cur,tenant,actor,lock=True)
    if not profile or profile['active'] is not True or profile['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    profile = {**profile, 'tenant_id': tenant}
    policy = TenantPolicy(profile)
    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE', (tenant, lead_id))
    deal = cur.fetchone()
    from tenant_dialog_access import require_source,scope
    if scope(policy,'update') is None and not policy.legacy_allows('dialogs') and (not policy.allows('deals') or not policy.can_access_deal(deal)):
        raise TenantLinearError('Mesaj göndərmək üçün icazəniz yoxdur.', 403)
    connect = connect_route(cur, tenant, lead_id)
    if connect:
        require_source(cur,tenant,policy,'connect',connect['channel_id'],'update')
        from tenant_dialog_lifecycle import require_access, enabled as lifecycle_enabled
        if lifecycle_status is not None and lifecycle_enabled(cur, tenant):
            # Read-only delivery status/reconciliation of this actor's durable
            # operation. Never used by reserve, upload, mark_sending or dispatch.
            from tenant_ai_dialog_scope import lock_dialog
            lock_dialog(cur,tenant,'connect',connect['channel_id'],connect['conversation_id'])
            cur.execute('''SELECT 1 FROM saas_chat_send_receipts sr
              JOIN saas_dialog_episodes e ON e.tenant_id=sr.tenant_id AND e.id::text=sr.route->>'lifecycle_episode_id'
              WHERE sr.tenant_id=%s::uuid AND sr.actor_id=%s AND sr.request_id=%s::uuid AND sr.lead_id=%s
                AND sr.state IN ('sending','unknown','accepted') AND e.channel_id=%s::uuid
                AND e.conversation_id=%s AND e.lead_id=sr.lead_id''',
              (tenant,actor,str(uuid.UUID(str(lifecycle_status))),lead_id,connect['channel_id'],connect['conversation_id']))
            if not cur.fetchone():raise TenantLinearError('Сохранённая отправка не найдена.',404)
        elif lifecycle_receipt is not None and lifecycle_enabled(cur, tenant):
            # Internal persistence only, never send/upload authority. An accepted
            # dispatch receipt proves the operation before close/transfer.
            request_id,external_id=lifecycle_receipt
            from tenant_ai_dialog_scope import lock_dialog
            lock_dialog(cur,tenant,'connect',connect['channel_id'],connect['conversation_id'])
            cur.execute('''SELECT 1 FROM saas_dialog_episode_sends es
              JOIN saas_chat_send_receipts sr ON sr.tenant_id=es.tenant_id AND sr.request_id=es.request_id
              WHERE es.tenant_id=%s::uuid AND es.channel_id=%s::uuid AND es.conversation_id=%s
                AND es.request_id=%s::uuid AND es.external_id=%s
                AND sr.actor_id=%s AND sr.lead_id=%s AND sr.state IN ('sending','unknown','accepted')''',
              (tenant,connect['channel_id'],connect['conversation_id'],str(uuid.UUID(str(request_id))),str(external_id).removeprefix('connect:'),actor,lead_id))
            if not cur.fetchone():raise TenantLinearError('Подтверждённая отправка не найдена.',409)
        else:
            require_access(cur,tenant,actor,'connect',connect['channel_id'],connect['conversation_id'],'update')
        return profile, ['connect', connect['channel_id'], connect['conversation_id']], {'provider': 'connect', 'route': connect}
    cur.execute('SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND lead_id=%s',(tenant,lead_id))
    if cur.fetchone():
        raise TenantLinearError('Исходный Connect-канал недоступен; отправка через другой канал запрещена.',409)
    if scope(policy,'update') is None and (not policy.allows('deals') or not policy.can_access_deal(deal)):
        raise TenantLinearError('Mesaj göndərmək üçün icazəniz yoxdur.', 403)
    if not policy.can_read_kommo_chat(deal):
        raise TenantLinearError('Сделка недоступна по назначенным воронкам Kommo.', 403)
    require_source(cur,tenant,policy,'kommo','','update')
    # Match the credential reader: OAuth wins when connected, otherwise the
    # tenant's manual connection. Lock both rows so switching modes cannot race
    # the final provider dispatch, and never fall back from broken OAuth secrets.
    cur.execute("""SELECT provider,account_domain,connected_at,status,secrets
      FROM saas_tenant_integrations WHERE tenant_id=%s::uuid
        AND provider IN ('kommo','kommo_manual')
      ORDER BY CASE WHEN provider='kommo' THEN 0 ELSE 1 END FOR SHARE""", (tenant,))
    integration = next((row for row in cur.fetchall() if row['status'] == 'connected'), None)
    if not integration or integration['status'] != 'connected' or not integration['secrets']:
        raise TenantLinearError('Bu çat üçün Connect və ya Kommo bağlantısını yoxlayın.', 409)
    prefix = 'manual:' if integration.get('provider') == 'kommo_manual' else ''
    return profile, [prefix + integration['account_domain'], str(integration['connected_at'])], integration


def _kommo_send_tokens(integration, tenant):
    try:
        tokens = json.loads(_fernet().decrypt(bytes(integration['secrets'])))
    except Exception as exc:
        raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409) from exc
    if (not isinstance(tokens, dict) or not isinstance(tokens.get('access_token'), str)
            or not tokens['access_token'] or
            (integration.get('provider') == 'kommo_manual' and
             (tokens.get('tenant_id') != tenant or tokens.get('provider') != 'kommo_manual'))):
        raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409)
    return tokens


def quote_target(cur,tenant,command,connection):
    if not command.get('quote_message_id'):return None
    if connection[0]!='connect' or connection[1:]!=[command['quote_channel_id'],command['quote_conversation_id']]:
        raise TenantLinearError('İstinad edilən dialoq dəyişib.',409)
    cur.execute("""SELECT raw->>'externalMessageId' AS provider_id FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s
        AND direction IN ('incoming','outgoing') FOR SHARE""",
      (tenant,command['quote_message_id'],command['lead_id'],'connect:'+connection[1]))
    found=cur.fetchone();value=(found or {}).get('provider_id')
    import re
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{5,200}',value):
        raise TenantLinearError('İstinad edilən mesaj bu dialoqda əlçatan deyil.',409)
    return value


def reserve(session, command):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur, session, command['lead_id'])
            quote=quote_target(cur,profile['tenant_id'],command,connection)
            key = (profile['tenant_id'], int(profile['telegram_id']), command['request_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f"chat-send:{key[0]}:{command['lead_id']}",))
            cur.execute("UPDATE saas_chat_send_receipts SET state=CASE WHEN state='preparing' THEN 'blocked' ELSE 'unknown' END,updated_at=now() WHERE tenant_id=%s::uuid AND lead_id=%s AND state IN ('preparing','sending') AND updated_at<now()-interval '10 minutes'", (key[0], command['lead_id']))
            cur.execute('SELECT * FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid FOR UPDATE', key)
            row = cur.fetchone()
            if row:
                if row['input_hash'] != fingerprint(command):
                    raise TenantLinearError('Eyni sorğu kodu ilə fərqli mesaj göndərilib.', 409)
                if row['state'] == 'accepted':
                    cached={'state':'accepted','message_id':row['message_id'],**({'manual_review':True} if row.get('resolution') else {})}
                    return {'cached_result':cached,**({'key':key,'command':command,'connection':connection,'profile':profile,'quote':quote,'native':bool(integration.get('route',{}).get('native'))} if command.get('quote_message_id') else {})}
                from tenant_connect_transport import quoted_once_scope
                if quote and connection[0]=='connect' and (integration.get('route',{}).get('native') or quoted_once_scope(key[0],connection[1])) and row['state'] in {'sending','unknown'}:
                    return {'check_only':True,'key':key,'command':command,'connection':connection,'profile':profile,'quote':quote,'native':bool(integration.get('route',{}).get('native'))}
                error = TenantLinearError('Bu sorğu təkrarlanmır. Göndərilmə vəziyyətini Kommo-da yoxlayın.', 409)
                error.keep_request = row['state'] != 'blocked'
                conn.commit()  # Persist stale receipt transitions before returning the refusal.
                raise error
            # A voice attempt with an unknown outcome remains non-retryable by
            # its request ID, but must not freeze unrelated conversation sends.
            cur.execute("""SELECT request_id FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND lead_id=%s
              AND state IN ('preparing','sending','unknown')
              AND NOT (state='unknown' AND COALESCE(route->>'voice_protocol','false')='true') LIMIT 1""", (key[0], command['lead_id']))
            if cur.fetchone():
                conn.commit()
                raise TenantLinearError('Bu çatda başqa göndərmə yoxlanılır. Kommo-da vəziyyəti yoxlayın.', 409)
            from tenant_dialog_rollout import guard
            rollout = guard(cur, profile['tenant_id'])
            generation = rollout.generation if rollout.enforced and connection[0]=='connect' else 0
            cur.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,route) VALUES(%s::uuid,%s,%s::uuid,%s,%s,'preparing',%s::jsonb)", (*key, command['lead_id'], fingerprint(command),json.dumps({'lifecycle_generation':generation})))
        conn.commit()
    return {'key': key, 'command': command, 'connection': connection, 'profile': profile, 'quote':quote}


def mark_sending(item, route):
    with _connect() as conn:
        from tenant_ai_group_coordination import group_jid,stop as stop_group
        is_group=item.get('connection',[None])[0]=='connect' and group_jid(item['connection'][2])
        if is_group:
            from tenant_ai_agents_schema import ensure_schema as ensure_ai_schema
            ensure_ai_schema(conn)
        ensure_schema(conn)
        if is_group:conn.commit()
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur, {'tenant_id': item['key'][0], 'telegram_id': item['key'][1]}, item['command']['lead_id'])
            from tenant_dialog_rollout import guard
            rollout = guard(cur, profile['tenant_id'])
            generation = rollout.generation if rollout.enforced and connection[0]=='connect' else 0
            cur.execute('SELECT route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid FOR UPDATE',item['key'])
            prepared = cur.fetchone() or {}
            if (prepared.get('route') or {}).get('lifecycle_generation',0) != generation:
                raise TenantLinearError('Режим диалога изменился. Подготовьте новую отправку.',409)
            route = {**route, 'lifecycle_generation':generation}
            if connection != item['connection']:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            if quote_target(cur,profile['tenant_id'],item['command'],connection)!=item.get('quote'):
                raise TenantLinearError('İstinad edilən mesaj dəyişib.',409)
            if integration.get('provider') == 'connect':
                credentials = integration['route']
                from tenant_dialog_lifecycle import ai_blocked, enabled as lifecycle_enabled
                managed=lifecycle_enabled(cur, profile['tenant_id']) and ai_blocked(cur,profile['tenant_id'],'connect',credentials['channel_id'],credentials['conversation_id'])
                if is_group and not managed:
                    stop_group(cur,profile['tenant_id'],credentials['conversation_id'],reason='verified_employee_reply',actor=profile['telegram_id'])
            else:
                tokens = _kommo_send_tokens(integration, profile['tenant_id'])
                credentials = {'account_domain': integration['account_domain'], 'access_token': tokens['access_token']}
            from tenant_connect_transport import quoted_once_scope
            if item.get('quote') and connection[0]=='connect' and (credentials.get('native') or quoted_once_scope(profile['tenant_id'],connection[1])):
                route={**route,'quote_protocol':True,'quote_message_id':item['command']['quote_message_id'],'quote_channel_id':connection[1],
                    'quote_conversation_id':connection[2],'quoted_external_message_id':item['quote']}
            from tenant_dialog_lifecycle import enabled as lifecycle_enabled, _row as lifecycle_row
            if connection[0]=='connect' and lifecycle_enabled(cur, profile['tenant_id']):
                episode=lifecycle_row(cur,profile['tenant_id'],connection[1],connection[2])
                route={**route,'lifecycle_episode_id':str(episode['id'])}
            cur.execute("UPDATE saas_chat_send_receipts SET state='sending',route=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state='preparing' RETURNING request_id", (json.dumps(route), *item['key']))
            if not cur.fetchone():
                raise TenantLinearError('Göndərmə artıq işlənir.', 409)
        conn.commit()
    return profile, credentials


def upload_credentials(item, live_deal=None):
    """Fresh authority before upload, without claiming message sending."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur,
                {'tenant_id': item['key'][0], 'telegram_id': item['key'][1]}, item['command']['lead_id'])
            if connection != item['connection']:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            if live_deal is not None and (int(live_deal.get('id') or 0) != item['command']['lead_id']
                    or not TenantPolicy(profile).can_read_kommo_chat({**live_deal, 'tenant_id': item['key'][0]})):
                raise TenantLinearError('Sövdələşməyə giriş dəyişib.', 403)
            tokens = _kommo_send_tokens(integration, profile['tenant_id'])
        conn.commit()
    return {'account_domain': integration['account_domain'], 'access_token': tokens['access_token']}


def finish(item, state, message_id=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_chat_send_receipts SET state=%s,message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state IN ('preparing','sending') RETURNING lead_id", (state, message_id, *item['key']))
            row = cur.fetchone()
            if row and state == 'accepted':
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'chat_message_accepted','lead',%s,%s::jsonb)''',
                  (uuid.uuid4(), item['key'][0], item['key'][1], str(row['lead_id']), json.dumps({'message_id': message_id})))
        conn.commit()


def quoted_status_credentials(item):
    """Fresh employee/tenant/source authority for a read-only receipt lookup."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile,connection,integration=authority(cur,{'tenant_id':item['key'][0],'telegram_id':item['key'][1]},item['command']['lead_id'])
            if connection!=item['connection'] or integration.get('provider')!='connect':
                raise TenantLinearError('İstinad edilən dialoq dəyişib.',409)
            if quote_target(cur,profile['tenant_id'],item['command'],connection)!=item.get('quote'):
                raise TenantLinearError('İstinad edilən mesaj dəyişib.',409)
            return {**integration['route'],**({'native_actor_id':item['key'][1]} if integration['route'].get('native') else {})}


def record_quoted_delivery(item,message_id,status):
    """Persist a scoped read receipt for history; never change send idempotency."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile,connection,_=authority(cur,{'tenant_id':item['key'][0],'telegram_id':item['key'][1]},item['command']['lead_id'])
            if connection!=item['connection'] or connection[0]!='connect':
                raise TenantLinearError('İstinad edilən dialoq dəyişib.',409)
            cur.execute("UPDATE saas_chat_send_receipts SET route=route||%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state='accepted' AND message_id=%s",
                (json.dumps(status),*item['key'],message_id))
            provider=status.get('provider_message_id')
            metadata={'status':status['delivery_state'],**({'externalMessageId':provider} if provider else {})}
            cur.execute('''UPDATE saas_crm_messages SET raw=COALESCE(raw,'{}'::jsonb)||%s::jsonb||jsonb_build_object('status',
              CASE WHEN raw->>'status'='read' THEN 'read'
                WHEN raw->>'status'='delivered' AND %s<>'read' THEN 'delivered'
                WHEN raw->>'status'='sent' AND %s IN ('pending','retrying','unknown') THEN 'sent' ELSE %s END)
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND external_id=%s AND direction='outgoing'
                AND (%s::text IS NULL OR raw->>'externalMessageId' IS NULL OR raw->>'externalMessageId'=%s)''',
              (json.dumps(metadata),status['delivery_state'],status['delivery_state'],status['delivery_state'],item['key'][0],item['command']['lead_id'],
               'connect:'+connection[1],'connect:'+message_id,provider,provider))
        conn.commit()


def accept_quoted_lookup(item,message_id):
    """Bind an already-existing Connect receipt; this function never dispatches."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile,connection,_=authority(cur,{'tenant_id':item['key'][0],'telegram_id':item['key'][1]},item['command']['lead_id'])
            if connection!=item['connection'] or quote_target(cur,profile['tenant_id'],item['command'],connection)!=item['quote']:
                raise TenantLinearError('İstinad edilən dialoq dəyişib.',409)
            cur.execute("UPDATE saas_chat_send_receipts SET state='accepted',message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state IN ('sending','unknown')",(message_id,*item['key']))
        conn.commit()


def read_quoted_receipt(session,lead_id,request_id=None):
    """Read an existing own receipt only. No reservation or send transition."""
    from tenant_connect_transport import quoted_once_scope
    lead_id=int(lead_id)
    if not 0<lead_id<2**63:raise TenantLinearError('Sorğu düzgün deyil.',400)
    if request_id:request_id=str(uuid.UUID(str(request_id)))
    with _connect() as conn:
        with conn.cursor() as cur:
            profile,connection,integration=authority(cur,session,lead_id)
            if connection[0]!='connect' or not (integration.get('route',{}).get('native') or quoted_once_scope(profile['tenant_id'],connection[1])):
                raise TenantLinearError('İstinad vəziyyəti bu çat üçün əlçatan deyil.',404)
            cur.execute("""SELECT request_id::text,lead_id,state,message_id,route FROM saas_chat_send_receipts
              WHERE tenant_id=%s::uuid AND actor_id=%s AND lead_id=%s """+
              ("AND request_id=%s::uuid" if request_id else "AND route->>'quote_protocol'='true'")+" ORDER BY created_at DESC,request_id DESC LIMIT 1",
              (profile['tenant_id'],profile['telegram_id'],lead_id,*([request_id] if request_id else [])))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Bu çatda əvvəlki istinad sorğusu tapılmadı.',404)
            route=row['route'] if isinstance(row['route'],dict) else {}
            if route.get('origin')!='connect':raise TenantLinearError('Sorğunun mənbəyi dəyişib.',409)
            if route.get('quote_channel_id') and [route['quote_channel_id'],route.get('quote_conversation_id')]!=connection[1:]:
                raise TenantLinearError('İstinad edilən dialoq dəyişib.',409)
            command={'lead_id':lead_id,'request_id':row['request_id'],**{key:route[key] for key in ('quote_message_id','quote_channel_id','quote_conversation_id') if key in route}}
            quote=quote_target(cur,profile['tenant_id'],command,connection) if command.get('quote_message_id') else None
            if quote and quote!=route.get('quoted_external_message_id'):raise TenantLinearError('İstinad edilən mesaj dəyişib.',409)
            return {'key':(profile['tenant_id'],profile['telegram_id'],row['request_id']),'command':command,'connection':connection,
                'quote':quote,'message_id':row['message_id'],'receipt_state':row['state'],'credentials':{**integration['route'],'tenant_id':profile['tenant_id'],**({'native_actor_id':profile['telegram_id']} if integration['route'].get('native') else {})}}


def dispatch_guarded(item, expected_credentials, callback, *args, **kwargs):
    """Final authority and source locks span the single provider operation.

    Scope revocation, employee deactivation, department reassignment and source
    unbinding serialize against this boundary. An operation already accepted
    by the provider cannot be recalled; receipts retain that ordering.
    """
    from tenant_dialog_lifecycle import enabled as lifecycle_enabled
    from tenant_scoped_access import scoped_access_enabled
    with _connect() as conn:
        with conn.cursor() as cur:
            from tenant_dialog_rollout import guard
            rollout=guard(cur,item['key'][0])
            # Preserve the original unscoped legacy final-dispatch behavior,
            # but never outside the shared activation barrier.
            if not rollout.enforced and not scoped_access_enabled() and item.get('connection',[None])[0]=='connect':
                from tenant_whatsapp_native_transport import guard_dispatch
                guard_dispatch(cur,item['key'][0],expected_credentials)
                dispatch_credentials={**expected_credentials,'_native_cursor':cur,'native_actor_id':item['key'][1]} if expected_credentials.get('native') else expected_credentials
                return callback(dispatch_credentials,*args,**kwargs)
            profile,connection,integration=authority(cur,
              {'tenant_id':item['key'][0],'telegram_id':item['key'][1]},item['command']['lead_id'])
            from tenant_dialog_rollout import guard
            rollout = guard(cur, profile['tenant_id'])
            generation = rollout.generation if rollout.enforced and connection[0]=='connect' else 0
            cur.execute('SELECT route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid',item['key'])
            saved = cur.fetchone() or {}
            if (saved.get('route') or {}).get('lifecycle_generation',0) != generation:
                raise TenantLinearError('Режим диалога изменился. Подготовьте новую отправку.',409)
            if connection!=item['connection']:
                raise TenantLinearError('Источник диалога изменился.',409)
            if connection[0]=='connect':
                current=integration['route']
                if (any(str(current.get(key))!=str(expected_credentials.get(key)) for key in ('channel_id','conversation_id','api_key'))
                        or current.get('native')!=expected_credentials.get('native')):
                    raise TenantLinearError('Источник диалога изменился.',409)
            if connection[0]=='connect' and lifecycle_enabled(cur, profile['tenant_id']):
                from tenant_dialog_lifecycle import _row as lifecycle_row
                current_episode=lifecycle_row(cur,profile['tenant_id'],connection[1],connection[2])
                cur.execute('SELECT route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid',item['key'])
                receipt=cur.fetchone() or {}
                if (receipt.get('route') or {}).get('lifecycle_episode_id')!=str((current_episode or {}).get('id')):
                    raise TenantLinearError('Обращение изменилось. Подготовьте новую отправку.',409)
            dispatch_credentials={**expected_credentials,'_native_cursor':cur,'native_actor_id':item['key'][1]} if expected_credentials.get('native') else expected_credentials
            result=callback(dispatch_credentials,*args,**kwargs)
            if connection[0]=='connect':
                from tenant_dialog_lifecycle import record_dispatch
                record_dispatch(cur,profile['tenant_id'],connection[1],connection[2],item['command']['request_id'],result)
            return result
