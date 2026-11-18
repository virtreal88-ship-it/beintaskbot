"""Explicit case-chat follow-ups through the established durable delivery ledger.

The recipient is immutable and read from the original delivery, never the client.
Unknown submissions are not retried. Incoming replies remain evidence-correlated.
"""
import hashlib
from tenant_linear_policy import TenantLinearError, identifier
import tenant_dialog_consultations as flow


def send(profile, key, data):
    if not isinstance(data, dict) or set(data) != {'request_id', 'text'}:
        raise TenantLinearError('Некорректное сообщение.', 400)
    key, request = identifier(key), identifier(data['request_id'])
    text = data['text']
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise TenantLinearError('Введите сообщение до 4000 символов.', 400)
    body = text.strip()
    with flow._connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy, item, _ = flow._consult(cur, profile, key, sending=True, validate_draft=False)
            if not item.get('case_id'):
                raise TenantLinearError('Чат доступен только для обращения.', 409)
            cur.execute('''SELECT * FROM saas_consultation_group_deliveries
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND is_chat_followup=FALSE
              ORDER BY created_at,request_id LIMIT 1 FOR UPDATE''', (tenant, key))
            original = cur.fetchone()
            if not original or original['state'] != 'sent':
                raise TenantLinearError('Сначала дождитесь подтверждения отправки первого запроса.', 409)
            flow.developer_target(cur, tenant, policy, str(original['channel_id']), original['conversation_id'])
            cur.execute('''SELECT * FROM saas_consultation_group_deliveries
              WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE''', (tenant, request))
            previous = cur.fetchone()
            if previous:
                if (str(previous['consultation_id']) != key or previous['created_by'] != actor
                    or not previous['is_chat_followup'] or previous['body'] != body):
                    raise TenantLinearError('Код запроса уже использован.', 409)
                if previous['state'] != 'ready':
                    return {'state': previous['state'], 'request_id': request,
                            'message_id': previous['connect_message_id']}
            else:
                # No new message can overtake an uncertain submission in this chat.
                cur.execute('''SELECT 1 FROM saas_consultation_group_deliveries
                  WHERE tenant_id=%s::uuid AND case_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
                    AND state IN ('ready','sending','pending','unknown') LIMIT 1''', (tenant,item['case_id'],original['channel_id'],original['conversation_id']))
                if cur.fetchone():
                    raise TenantLinearError('Дождитесь проверки предыдущей отправки.', 409)
                cur.execute('''SELECT 1 FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid
                  UNION ALL SELECT 1 FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid LIMIT 1''',
                  (tenant,request,tenant,request))
                if cur.fetchone():
                    raise TenantLinearError('Код запроса уже использован.',409)
                cur.execute("SELECT to_regclass('saas_chat_send_receipts') AS ready")
                if (cur.fetchone() or {}).get('ready'):
                    cur.execute('SELECT 1 FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND request_id=%s::uuid LIMIT 1',(tenant,request))
                    if cur.fetchone():raise TenantLinearError('Код запроса уже использован.',409)
                cur.execute('''INSERT INTO saas_consultation_group_deliveries
                  (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,is_chat_followup)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'ready',%s,TRUE)''',
                  (tenant,key,request,item['case_id'],original['channel_id'],original['conversation_id'],body,
                   hashlib.sha256(body.encode()).hexdigest(),actor))
        conn.commit()
    return {**flow.dispatch(profile,key,request), 'request_id':request}


def delivery_page(cur, tenant, key, before='', actor=None):
    """Bounded, stable, exact-ledger pagination; cursor cannot cross a chat."""
    boundary=''
    params=[tenant,key]
    if before:
        before=identifier(before)
        cur.execute('SELECT created_at,request_id FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND request_id=%s::uuid',(tenant,key,before))
        row=cur.fetchone()
        if not row:raise TenantLinearError('Страница переписки недоступна.',404)
        boundary=' AND (created_at,request_id)<(%s::timestamptz,%s::uuid)'
        params.extend([row['created_at'],before])
    cur.execute('''SELECT request_id::text,body,state,created_at,updated_at,connect_message_id,provider_message_id,is_chat_followup,media_metadata,channel_id::text,conversation_id
      FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid'''+boundary+' ORDER BY created_at DESC,request_id DESC LIMIT 201',params)
    rows=cur.fetchall()
    values=[{k:str(v) if hasattr(v,'isoformat') else v for k,v in dict(row).items()} for row in rows[:200]][::-1]
    if actor is not None:
        from urllib.parse import urlencode
        for value in values:
            metadata=value.get('media_metadata') or {}
            if metadata and value.get('connect_message_id'):
                try:external='connect:'+str(__import__('uuid').UUID(value['connect_message_id']))
                except (ValueError,TypeError):continue
                value['attachment_url']='/api/platform/crm/chat/attachment?'+urlencode({'lead_id':metadata['lead_id'],
                  'channel_id':value['channel_id'],'conversation_id':value['conversation_id'],'message_id':external,
                  'expected_tenant_id':tenant,'expected_user_id':actor})
    return {'deliveries':values,'has_earlier':len(rows)>200,'next_before':values[0]['request_id'] if len(rows)>200 else None}


def read(profile,key,before=''):
    key=identifier(key)
    with flow._connect() as conn:
        flow.require_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy,item,_=flow._consult(cur,profile,key)
            cur.execute('''SELECT channel_id::text,conversation_id FROM saas_consultation_group_deliveries
              WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND is_chat_followup=FALSE
              ORDER BY created_at,request_id LIMIT 1''',(tenant,key))
            original=cur.fetchone()
            if not original:raise TenantLinearError('Переписка недоступна.',404)
            flow.route(cur,tenant,policy,original['channel_id'],original['conversation_id'])
            return delivery_page(cur,tenant,key,before,actor=actor)


def can_send(cur,profile,row):
    """Read-only UI hint using existing update policy; send rechecks under locks."""
    if not row.get('case_id'):return True
    from tenant_support_access import policy_access
    from tenant_scoped_access_store import owned_resource_metadata
    from tenant_dialog_lifecycle import enabled,_row
    try:
        tenant,actor,policy=policy_access(cur,profile,'update')
        cur.execute('SELECT * FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,row['case_id']))
        case=cur.fetchone()
        if not case or case['status']=='resolved':return False
        if policy.action_scope('support','update') is not None and not policy.allows_resource('support','update',owned_resource_metadata(cur,tenant,case)):
            return False
        if enabled(cur,tenant):
            episode=_row(cur,tenant,row['source_channel'],row['source_conversation'])
            if not episode or episode['state']!='accepted' or int(episode['operator_id'])!=int(actor):return False
        return True
    except TenantLinearError:return False
