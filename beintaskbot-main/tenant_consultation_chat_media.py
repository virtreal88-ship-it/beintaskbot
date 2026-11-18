"""Optional case binding inside the established media/voice receipt lifecycle.

No provider I/O here. A durable case delivery is reserved in the same transaction
as the chat receipt; the last authorization check spans the actual submission.
"""
import hashlib
import json
import os
from tenant_linear_policy import TenantLinearError, identifier


def enabled():
    return os.environ.get('CASE_CHAT_MEDIA_ENABLED','').lower()=='true'


def authorize(cur,session,command,*,sending=True):
    key=command.get('consultation_id')
    if not key:return None
    if sending and not enabled():raise TenantLinearError('Вложения в чате обращения пока недоступны.',409)
    import tenant_dialog_consultations as flow
    if not flow.schema_ready(cur):raise TenantLinearError('Требуется обновление схемы консультаций.',503)
    if sending:
        from tenant_consultation_chat_lock import lock_consultation
        lock_consultation(cur,session,key)
    tenant,actor,policy,item,_=flow._consult(cur,session,identifier(key),sending=sending,validate_draft=False)
    if not item.get('case_id'):raise TenantLinearError('Вложения доступны только в обращении.',409)
    cur.execute('''SELECT channel_id::text,conversation_id,state FROM saas_consultation_group_deliveries
      WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid AND is_chat_followup=FALSE
      ORDER BY created_at,request_id LIMIT 1''',(tenant,key))
    original=cur.fetchone()
    if not original or original['state']!='sent':raise TenantLinearError('Первоначальный запрос ещё не отправлен.',409)
    target=flow.developer_target(cur,tenant,policy,original['channel_id'],original['conversation_id']) if sending else flow.route(cur,tenant,policy,original['channel_id'],original['conversation_id'])
    if int(target['lead_id'])!=int(command['lead_id']):raise TenantLinearError('Получатель обращения изменился.',409)
    return {'consultation_id':str(item['id']),'case_id':str(item['case_id']),'channel_id':original['channel_id'],
            'conversation_id':original['conversation_id'],'tenant_id':tenant,'actor':actor,'lead_id':int(target['lead_id'])}


def reserve(cur,session,command,connection,binding):
    if not binding:return
    if connection!=['connect',binding['channel_id'],binding['conversation_id']]:raise TenantLinearError('Получатель обращения изменился.',409)
    file=command.get('file') or {}
    if file.get('type') not in {'picture','file','video','audio'} or not file.get('sha256') or command.get('quote_message_id'):
        raise TenantLinearError('Ожидается проверенное вложение обращения.',400)
    cur.execute('''SELECT 1 FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND case_id=%s::uuid
      AND channel_id=%s::uuid AND conversation_id=%s AND state IN ('ready','sending','pending','unknown') LIMIT 1''',
      (binding['tenant_id'],binding['case_id'],binding['channel_id'],binding['conversation_id']))
    if cur.fetchone():raise TenantLinearError('Дождитесь проверки предыдущей отправки.',409)
    cur.execute('''SELECT 1 FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid
      UNION ALL SELECT 1 FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid LIMIT 1''',
      (binding['tenant_id'],command['request_id'],binding['tenant_id'],command['request_id']))
    if cur.fetchone():raise TenantLinearError('Код запроса уже использован.',409)
    body=command.get('text') or file.get('name') or ''
    metadata={**file,'lead_id':binding['lead_id'],'delivery_state':'ready'}
    cur.execute('''INSERT INTO saas_consultation_group_deliveries
      (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,is_chat_followup,media_metadata)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,'ready',%s,TRUE,%s::jsonb)''',
      (binding['tenant_id'],binding['consultation_id'],command['request_id'],binding['case_id'],binding['channel_id'],binding['conversation_id'],body,
       hashlib.sha256(body.encode()).hexdigest(),binding['actor'],json.dumps(metadata)))


def guard(cur,item,saved_route,*,sending=True):
    expected=item.get('command',{}).get('consultation_id')
    saved=saved_route.get('consultation_id')
    if expected!=saved and (expected or saved):raise TenantLinearError('Привязка обращения изменилась.',409)
    if not saved:return None
    binding=authorize(cur,{'tenant_id':item['key'][0],'telegram_id':item['key'][1]},item['command'],sending=sending)
    cur.execute('''SELECT consultation_id::text,case_id::text,channel_id::text,conversation_id,created_by,media_metadata
      FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE''',(item['key'][0],item['key'][2]))
    row=cur.fetchone()
    if not row or not row['media_metadata'] or row['created_by']!=item['key'][1] or any(row[k]!=binding[k] for k in ('consultation_id','case_id','channel_id','conversation_id')):
        raise TenantLinearError('Привязка вложения изменилась.',409)
    return binding


def state(cur,item,value):
    if not item.get('command',{}).get('consultation_id'):return
    cur.execute('''UPDATE saas_consultation_group_deliveries SET state=%s,updated_at=now()
      WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND created_by=%s
        AND consultation_id=%s::uuid AND media_metadata<>'{}'::jsonb AND state IN ('ready','sending','unknown')''',
      (value,item['key'][0],item['key'][2],item['key'][1],item['command']['consultation_id']))


def reconcile(cur,tenant,actor,request,saved_route,receipt):
    if not saved_route.get('consultation_id'):return
    cur.execute('''SELECT * FROM saas_consultation_group_deliveries
      WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND consultation_id=%s::uuid FOR UPDATE''',
      (tenant,request,saved_route['consultation_id']))
    row=cur.fetchone()
    if (not row or row['created_by']!=actor or not row['media_metadata']
        or str(row['channel_id'])!=saved_route.get('channel_id') or row['conversation_id']!=saved_route.get('conversation_id')
        or (row.get('provider_message_id') and row['provider_message_id']!=receipt['provider_message_id'])):
        raise TenantLinearError('Квитанция вложения не соответствует обращению.',409)
    cur.execute('''UPDATE saas_consultation_group_deliveries SET state='sent',connect_message_id=%s,provider_message_id=%s,
      media_metadata=jsonb_set(media_metadata,'{delivery_state}',to_jsonb(%s::text)),updated_at=now()
      WHERE tenant_id=%s::uuid AND request_id=%s::uuid''',
      (receipt['message_id'],receipt['provider_message_id'],receipt.get('delivery_state','sent'),tenant,request))


def validate_read(session,lead_id,consultation_id,request_id=None):
    """Optional case-scoped capability/recovery guard, in addition to target ACL."""
    if not consultation_id:return
    import tenant_dialog_consultations as flow
    key=identifier(consultation_id)
    with flow._connect() as conn:
        with conn.cursor() as cur:
            authorize(cur,session,{'consultation_id':key,'lead_id':lead_id},sending=request_id is None)
            if request_id:
                cur.execute('''SELECT route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s
                  AND request_id=%s::uuid AND lead_id=%s''',(session['tenant_id'],session['telegram_id'],identifier(request_id),int(lead_id)))
                saved=cur.fetchone()
                if not saved or (saved.get('route') or {}).get('consultation_id')!=key:
                    raise TenantLinearError('Квитанция не принадлежит этому обращению.',404)
