"""Customer media sends: scoped receipts, one provider submission, safe recovery."""
import asyncio
import base64
import hashlib
import json
import re
import uuid
from urllib.parse import urlencode

from tenant_chat_send_store import dispatch_guarded, authority, finish, mark_sending, reserve
from tenant_connect_transport import _request
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _json


def _checked_receipt(value, route, request_id):
    try:
        message_id = str(uuid.UUID(str(value['id'])))
        operation_id = str(uuid.UUID(str(value['operationId'])))
        request_id = str(uuid.UUID(str(request_id)))
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid receipt') from None
    if (value.get('channelId') != route['channel_id'] or value.get('conversationId') != route['conversation_id']
            or value.get('clientMessageId') != request_id or value.get('retrySafe') is not False
            or str(value.get('authorId')) != str(route.get('author_id'))
            or str(value.get('crmConversationId')) != str(route.get('crm_conversation_id'))
            or value.get('status') not in {'unknown', 'sent', 'delivered', 'read', 'failed'}):
        raise ValueError('receipt scope mismatch')
    if (value.get('inputHash') != route.get('input_hash') or not re.fullmatch(r'[0-9a-f]{64}', str(route.get('input_hash', '')))):
        raise ValueError('receipt input mismatch')
    if value.get('type') != route.get('media_type'):
        raise ValueError('receipt media type mismatch')
    status = value['status']
    provider_id = value.get('externalMessageId')
    accepted = value.get('providerAccepted') is True and isinstance(provider_id, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{5,200}', provider_id))
    if not accepted:
        return {'state': 'unknown', 'delivery_state': 'unknown', 'provider_accepted': False,
                'delivered': False, 'retry_safe': False, 'message_id': message_id,'operation_id':operation_id}
    if status in {'delivered', 'read'} and value.get('delivered') is not True:
        status = 'sent'
    if status not in {'sent', 'delivered', 'read', 'failed'}:
        raise ValueError('invalid accepted receipt')
    return {'state': 'accepted', 'delivery_state': status, 'provider_accepted': True,
            'delivered': status in {'delivered', 'read'}, 'retry_safe': False,
            'message_id': message_id, 'operation_id':operation_id,'provider_message_id': provider_id,'media_type':value.get('type'),
            **({'participant_jid':value['participantJid']} if route['conversation_id'].endswith('@g.us') and isinstance(value.get('participantJid'),str) and re.fullmatch(r'[0-9]{5,22}@s\.whatsapp\.net',value['participantJid']) else {})}


def _connect_receipt(route, request_id):
    value = _request(route['api_key'], 'GET', '/v1/channels/' + route['channel_id'] + '/customer-media/requests/' + request_id + '?' +
                     urlencode({'conversationId': route['conversation_id'],'authorId':str(route['author_id']),'crmConversationId':str(route['crm_conversation_id'])}))
    return _checked_receipt(value, route, request_id)


def _submit(route, request_id, file, caption, content):
    value = _request(route['api_key'], 'POST', '/v1/channels/' + route['channel_id'] + '/customer-media',
        {'conversationId':route['conversation_id'],'clientMessageId':request_id,
         'authorId':str(route['author_id']),'crmConversationId':str(route['crm_conversation_id']),
         'type':{'picture':'image','file':'document','video':'video'}[file['type']],
         'mime':file['mime'],'fileName':file['name'],'caption':caption,
         'mediaBase64':base64.b64encode(content).decode('ascii')})
    return _checked_receipt(value, route, request_id)


def own_receipt(session, lead_id, request_id):
    try:
        lead_id = int(lead_id)
        request_id = str(uuid.UUID(str(request_id)))
        if not 0 < lead_id < 2**63:
            raise ValueError()
    except (ValueError, TypeError):
        raise TenantLinearError('Sorğu düzgün deyil.', 400) from None
    with _connect() as conn:
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur, session, lead_id)
            cur.execute('''SELECT state,message_id,route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid
              AND actor_id=%s AND lead_id=%s AND request_id=%s::uuid''',
                        (profile['tenant_id'], profile['telegram_id'], lead_id, request_id))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Fayl sorğusu tapılmadı.', 404)
            route = row.get('route') if isinstance(row.get('route'), dict) else {}
            if (connection[0] != 'connect' or route.get('media_protocol') is not True
                    or [route.get('channel_id'), route.get('conversation_id')] != connection[1:]):
                raise TenantLinearError('Fayl sorğusunun dialoqu dəyişib.', 409)
            return {'key': (profile['tenant_id'], int(profile['telegram_id']), request_id), 'lead_id': lead_id,
                    'connection': connection,
                    'credentials': {**integration['route'],'author_id':int(profile['telegram_id']),'crm_conversation_id':lead_id,'media_type':route.get('media_type'),'input_hash':route.get('input_hash')},
                    'receipt_state': row['state'],
                    'metadata':route}


def record_acceptance(session, item, receipt):
    """Recheck current access and atomically reconcile the local history row."""
    if receipt.get('provider_accepted') is not True:
        raise ValueError('provider acceptance required')
    from tenant_ai_agents_schema import ensure_schema as ensure_ai_schema
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_chat_send_schema import ensure_schema
    from tenant_connect_transport import _schema as ensure_connect_schema
    from tenant_ai_agent_connect import mark_operator_reply
    tenant, actor, request_id = item['key']
    kind=receipt.get('media_type')
    if kind not in {'image','video','document'}:raise ValueError('invalid media type')
    with _connect() as conn:
        ensure_ai_schema(conn); ensure_schema(conn); ensure_connect_schema(conn)
        conn.commit()
        with conn.cursor() as cur:
            profile, connection, _ = authority(cur, session, item['lead_id'])
            if connection != item['connection'] or connection[0] != 'connect':
                raise TenantLinearError('Dialoqa giriş dəyişib.', 409)
            lock_dialog(cur, tenant, 'connect', connection[1], connection[2])
            cur.execute('''SELECT state,route,message_id FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s
              AND request_id=%s::uuid AND lead_id=%s FOR UPDATE''', (tenant, actor, request_id, item['lead_id']))
            saved = cur.fetchone()
            route = (saved or {}).get('route') or {}
            if (not saved or saved['state'] not in {'sending', 'unknown', 'accepted'} or route.get('media_protocol') is not True
                    or [route.get('channel_id'), route.get('conversation_id')] != connection[1:] or route.get('media_type')!=kind
                    or (saved['state'] == 'accepted' and saved.get('message_id') != receipt['message_id'])):
                raise TenantLinearError('Fayl sorğusunun mənbəyi dəyişib.', 409)
            external = 'connect:' + receipt['message_id']
            cur.execute('''SELECT id,kommo_lead_id,channel,direction,message_type,raw FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND external_id=%s FOR UPDATE''', (tenant, external))
            existing = cur.fetchone()
            raw = (existing or {}).get('raw') or {}
            if existing and (existing['kommo_lead_id'] != item['lead_id'] or existing['channel'] != 'connect:' + connection[1]
                             or existing['direction'] != 'outgoing' or existing['message_type'] != kind or not isinstance(raw, dict)
                             or raw.get('externalMessageId') != receipt['provider_message_id']):
                raise TenantLinearError('Fayl tarixçəsinin mənbəyi təsdiqlənmədi.', 409)
            already = bool(existing and raw.get('media_receipt_request_id') == request_id)
            # An early webhook row can carry a generic or unrelated status.
            # Only this flow's already-confirmed receipt can preserve it.
            previous = raw.get('status') if already else None
            ranks = {'unknown': 0, 'pending': 0, 'sent': 1, 'failed': 2, 'delivered': 3, 'read': 4}
            status = receipt['delivery_state']
            if ranks.get(previous, 0) > ranks.get(status, 0):
                status = previous
            facts = {'source': 'connect', 'id': receipt['message_id'],
                     'externalMessageId': receipt['provider_message_id'],
                     'originClientMessageId': request_id, 'verified_employee_id': actor,
                     'media_receipt_request_id': request_id, 'fromMe': True, 'status': status,
                     'channelId':connection[1], 'conversationId':connection[2], 'type':kind,
                     'mime':route.get('mime'), 'fileName':route.get('file_name'), 'size_bytes':route.get('size_bytes')}
            if connection[2].endswith('@g.us'):
                facts['_crm_group_send_receipt']=True
            if receipt.get('participant_jid'):
                facts['participantJid']=receipt['participant_jid']
                from tenant_connect_transport import _keep_verified_group_participant
                facts=_keep_verified_group_participant(raw,facts)
            # This media protocol requires its durable operation journal ID.
            operation_id=receipt.get('operation_id')
            if isinstance(operation_id,str):
                try:facts['connect_operation_id']=str(uuid.UUID(operation_id))
                except ValueError:pass
            author = str(profile.get('display_name') or 'Operator')[:120]
            if existing:
                cur.execute('''UPDATE saas_crm_messages SET author_name=%s,message_type=%s,raw=COALESCE(raw,'{}'::jsonb)||%s::jsonb
                  WHERE tenant_id=%s::uuid AND id=%s::uuid''', (author, kind, _json(facts), tenant, str(existing['id'])))
            else:
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
                  VALUES(%s,%s::uuid,%s,%s,'outgoing',%s,%s,%s,%s,'',now(),%s::jsonb)''',
                            (uuid.uuid4(), tenant, item['lead_id'], external, 'connect:' + connection[1], author, route.get('caption',''), kind, _json(facts)))
            if not already:
                mark_operator_reply(tenant, item['lead_id'], connection[1], connection[2], connection=conn, actor=actor)
            if saved['state'] != 'accepted':
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'chat_message_accepted','lead',%s,%s::jsonb)''',
                            (uuid.uuid4(),tenant,actor,str(item['lead_id']),_json({'message_id':receipt['message_id'],'type':kind})))
            cur.execute('''UPDATE saas_chat_send_receipts SET state='accepted',message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', (receipt['message_id'], tenant, actor, request_id))
        conn.commit()


def input_hash(file, caption, content, actor, lead_id):
    # Match JSON.stringify field order/UTF-8 in Connect exactly.
    fields=[{'picture':'image','file':'document','video':'video'}[file['type']],file['mime'],file['name'],caption,str(actor),str(lead_id)]
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\0'+content).hexdigest()


async def run(session, command, content, logger):
    # Revalidate even for non-HTTP callers, before any durable send reservation.
    from tenant_chat_upload_policy import prepare
    file=command.get('file') or {}
    if file.get('type') not in {'picture','video','file'} or command.get('quote_message_id') or len(command.get('text',''))>4096:
        raise TenantLinearError('JPEG, PNG, WebP, MP4 və PDF; mətn maksimum 4096 simvol.',400)
    validated=prepare(command,file.get('name'),file.get('mime'),content)
    if validated['file']!=file or validated['text']!=command.get('text'):
        raise TenantLinearError('Fayl məlumatı dəyişib.',400)
    item = await asyncio.to_thread(reserve, session, command)
    if 'cached_result' in item:
        return await check(session, command['lead_id'], command['request_id'])
    if item['connection'][0] != 'connect':
        await asyncio.to_thread(finish, item, 'blocked')
        raise TenantLinearError('Fayl yalnız Connect dialoqunda göndərilə bilər.', 409)
    started = False
    try:
        channel, conversation = item['connection'][1:]
        digest=input_hash(command['file'],command['text'],content,item['key'][1],command['lead_id'])
        profile, route = await asyncio.to_thread(mark_sending, item, {'origin':'connect','media_protocol':True,
                                                                      'channel_id':channel,'conversation_id':conversation,
            'media_type':{'picture':'image','file':'document','video':'video'}[command['file']['type']], 'caption':command['text'],
            'mime':command['file']['mime'],'file_name':command['file']['name'],'size_bytes':command['file']['size'],'input_hash':digest})
        route = {**route,'input_hash':digest,'author_id':item['key'][1],'crm_conversation_id':command['lead_id'],'media_type':{'picture':'image','file':'document','video':'video'}[command['file']['type']]}
        started = True
        receipt = await asyncio.to_thread(dispatch_guarded, item, route, _submit, command['request_id'], command['file'],command['text'], content)
        if receipt['provider_accepted']:
            await asyncio.to_thread(record_acceptance, session, {**item, 'lead_id': command['lead_id']}, receipt)
        else:
            await asyncio.to_thread(finish, item, 'unknown')
        return receipt
    except Exception as error:
        try:
            await asyncio.to_thread(finish, item, 'unknown' if started else 'blocked')
        except Exception:
            logger.error('Customer media receipt persistence unavailable')
        if isinstance(error, TenantLinearError) and not started:
            raise
        result = TenantLinearError('Göndərmə nəticəsi məlum deyil. Təkrar göndərməyin; vəziyyəti yoxlayın.' if started
                                   else 'Fayl göndərilmədi. Dialoq icazəsini yoxlayın.', 503)
        result.keep_request = started
        raise result from None


async def check(session, lead_id, request_id):
    item = await asyncio.to_thread(own_receipt, session, lead_id, request_id)
    receipt = await asyncio.to_thread(_connect_receipt, item['credentials'], item['key'][2])
    if receipt['provider_accepted']:
        await asyncio.to_thread(record_acceptance, session, item, receipt)
    else:
        fresh = await asyncio.to_thread(own_receipt, session, lead_id, request_id)
        if fresh['connection'] != item['connection']:
            raise TenantLinearError('Dialoqa giriş dəyişib.', 409)
    return receipt


def capability(session, lead_id):
    try:
        lead_id=int(lead_id)
        if not 0<lead_id<2**63:raise ValueError()
    except (TypeError,ValueError):raise TenantLinearError('Sorğu düzgün deyil.',400) from None
    with _connect() as conn:
        with conn.cursor() as cur:
            _,connection,integration=authority(cur,session,lead_id)
    if connection[0]!='connect':return {'enabled':False}
    route=integration['route']
    result=_request(route['api_key'],'GET','/v1/channels/'+route['channel_id']+'/customer-media/capability')
    with _connect() as conn:
        with conn.cursor() as cur:
            _,fresh,_=authority(cur,session,lead_id)
    if fresh!=connection:raise TenantLinearError('Dialoqa giriş dəyişib.',409)
    return {'enabled':result.get('enabled') is True,'max_bytes':5*1024*1024,
            'mime_types':['image/jpeg','image/png','image/webp','video/mp4','application/pdf']}


