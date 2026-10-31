"""Durable, tenant/actor-specific receipts reserved before external writes."""
import json
import uuid
from tenant_platform import _connect, _read_tenant_workflow, _fernet
from tenant_chat_send_schema import ensure_schema
from tenant_chat_send_policy import fingerprint
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_connect_transport import connect_route


def authority(cur, session, lead_id):
    tenant, actor = str(uuid.UUID(str(session['tenant_id']))), int(session['telegram_id'])
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
    profile = cur.fetchone()
    if not profile or profile['active'] is not True or profile['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    profile = {**profile, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)}
    policy = TenantPolicy(profile)
    cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE', (tenant, lead_id))
    deal = cur.fetchone()
    if not policy.allows('dialogs') and (not policy.allows('deals') or not policy.can_access_deal(deal)):
        raise TenantLinearError('Mesaj göndərmək üçün icazəniz yoxdur.', 403)
    connect = connect_route(cur, tenant, lead_id)
    if connect:
        return profile, ['connect', connect['channel_id'], connect['conversation_id']], {'provider': 'connect', 'route': connect}
    if not policy.allows('deals') or not policy.can_access_deal(deal):
        raise TenantLinearError('Mesaj göndərmək üçün icazəniz yoxdur.', 403)
    cur.execute("SELECT account_domain,connected_at,status,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' FOR SHARE", (tenant,))
    integration = cur.fetchone()
    if not integration or integration['status'] != 'connected' or not integration['secrets']:
        raise TenantLinearError('Bu çat üçün Connect və ya Kommo bağlantısını yoxlayın.', 409)
    return profile, [integration['account_domain'], str(integration['connected_at'])], integration


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
            profile, connection, _ = authority(cur, session, command['lead_id'])
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
                    return {'cached_result':cached,**({'key':key,'command':command,'connection':connection,'profile':profile,'quote':quote} if command.get('quote_message_id') else {})}
                error = TenantLinearError('Bu sorğu təkrarlanmır. Göndərilmə vəziyyətini Kommo-da yoxlayın.', 409)
                error.keep_request = row['state'] != 'blocked'
                conn.commit()  # Persist stale receipt transitions before returning the refusal.
                raise error
            cur.execute("SELECT request_id FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND lead_id=%s AND state IN ('preparing','sending','unknown') LIMIT 1", (key[0], command['lead_id']))
            if cur.fetchone():
                conn.commit()
                raise TenantLinearError('Bu çatda başqa göndərmə yoxlanılır. Kommo-da vəziyyəti yoxlayın.', 409)
            cur.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state) VALUES(%s::uuid,%s,%s::uuid,%s,%s,'preparing')", (*key, command['lead_id'], fingerprint(command)))
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
            if connection != item['connection']:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            if quote_target(cur,profile['tenant_id'],item['command'],connection)!=item.get('quote'):
                raise TenantLinearError('İstinad edilən mesaj dəyişib.',409)
            if integration.get('provider') == 'connect':
                credentials = integration['route']
                if is_group:
                    stop_group(cur,profile['tenant_id'],credentials['conversation_id'],reason='verified_employee_reply',actor=profile['telegram_id'])
            else:
                tokens = json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode('utf-8'))
                if not isinstance(tokens, dict) or not tokens.get('access_token'):
                    raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409)
                credentials = {'account_domain': integration['account_domain'], 'access_token': tokens['access_token']}
            cur.execute("UPDATE saas_chat_send_receipts SET state='sending',route=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid AND state='preparing' RETURNING request_id", (json.dumps(route), *item['key']))
            if not cur.fetchone():
                raise TenantLinearError('Göndərmə artıq işlənir.', 409)
        conn.commit()
    return profile, credentials


def upload_credentials(item):
    """Fresh authority before upload, without claiming message sending."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur,
                {'tenant_id': item['key'][0], 'telegram_id': item['key'][1]}, item['command']['lead_id'])
            if connection != item['connection']:
                raise TenantLinearError('Kommo bağlantısı dəyişib.', 409)
            tokens = json.loads(_fernet().decrypt(bytes(integration['secrets'])))
            if not isinstance(tokens, dict) or not isinstance(tokens.get('access_token'), str) or not tokens['access_token']:
                raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409)
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
            return integration['route']


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
