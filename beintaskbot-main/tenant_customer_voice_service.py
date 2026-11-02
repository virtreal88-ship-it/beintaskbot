"""Customer voice sends: scoped receipts, one provider submission, safe recovery."""
import asyncio
import base64
import hashlib
import json
import re
import uuid
from urllib.parse import urlencode

from tenant_chat_send_policy import command as text_command
from tenant_chat_send_store import authority, finish, mark_sending, reserve
from tenant_connect_transport import _request
from tenant_customer_voice import validate_recording
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _json


def prepare(data, mime, content):
    if (not isinstance(data, dict) or set(data) != {'request_id', 'lead_id', 'expected_tenant_id', 'expected_user_id'}):
        raise TenantLinearError('Sorğu düzgün deyil.', 400)
    try:
        content, extension, mime = validate_recording(content, mime)
    except ValueError:
        raise TenantLinearError('Səs faylı düzgün deyil və ya limitdən böyükdür.', 400) from None
    value = text_command({**data, 'text': 'voice'})
    value['text'] = ''
    value['file'] = {'name': 'voice.' + extension, 'mime': mime, 'size': len(content),
                     'sha256': hashlib.sha256(content).hexdigest(), 'type': 'audio'}
    return value, content


def _checked_receipt(value, route, request_id):
    try:
        message_id = str(uuid.UUID(str(value['id'])))
        request_id = str(uuid.UUID(str(request_id)))
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid receipt') from None
    if (value.get('channelId') != route['channel_id'] or value.get('conversationId') != route['conversation_id']
            or value.get('clientMessageId') != request_id or value.get('retrySafe') is not False
            or value.get('status') not in {'unknown', 'sent', 'delivered', 'read', 'failed'}):
        raise ValueError('receipt scope mismatch')
    status = value['status']
    provider_id = value.get('externalMessageId')
    accepted = value.get('providerAccepted') is True and isinstance(provider_id, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{5,200}', provider_id))
    if not accepted:
        return {'state': 'unknown', 'delivery_state': 'unknown', 'provider_accepted': False,
                'delivered': False, 'retry_safe': False, 'message_id': message_id}
    if status in {'delivered', 'read'} and value.get('delivered') is not True:
        status = 'sent'
    if status not in {'sent', 'delivered', 'read', 'failed'}:
        raise ValueError('invalid accepted receipt')
    return {'state': 'accepted', 'delivery_state': status, 'provider_accepted': True,
            'delivered': status in {'delivered', 'read'}, 'retry_safe': False,
            'message_id': message_id, 'provider_message_id': provider_id}


def _connect_receipt(route, request_id):
    value = _request(route['api_key'], 'GET', '/v1/channels/' + route['channel_id'] + '/customer-voice/requests/' + request_id + '?' +
                     urlencode({'conversationId': route['conversation_id']}))
    return _checked_receipt(value, route, request_id)


def _submit(route, request_id, mime, content):
    value = _request(route['api_key'], 'POST', '/v1/channels/' + route['channel_id'] + '/customer-voice',
                     {'conversationId': route['conversation_id'], 'clientMessageId': request_id,
                      'mime': mime, 'audioBase64': base64.b64encode(content).decode('ascii')})
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
                raise TenantLinearError('Səs sorğusu tapılmadı.', 404)
            route = row.get('route') if isinstance(row.get('route'), dict) else {}
            if (connection[0] != 'connect' or route.get('voice_protocol') is not True
                    or [route.get('channel_id'), route.get('conversation_id')] != connection[1:]):
                raise TenantLinearError('Səs sorğusunun dialoqu dəyişib.', 409)
            return {'key': (profile['tenant_id'], int(profile['telegram_id']), request_id), 'lead_id': lead_id,
                    'connection': connection, 'credentials': integration['route'], 'receipt_state': row['state']}


def can_send(session, lead_id):
    try:
        lead_id = int(lead_id)
        if not 0 < lead_id < 2**63:
            raise ValueError()
    except (ValueError, TypeError):
        raise TenantLinearError('Sorğu düzgün deyil.', 400) from None
    with _connect() as conn:
        with conn.cursor() as cur:
            _, connection, _ = authority(cur, session, lead_id)
            return connection[0] == 'connect' and bool(re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|lid)',connection[2]))


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
            if (not saved or saved['state'] not in {'sending', 'unknown', 'accepted'} or route.get('voice_protocol') is not True
                    or [route.get('channel_id'), route.get('conversation_id')] != connection[1:]
                    or (saved['state'] == 'accepted' and saved.get('message_id') != receipt['message_id'])):
                raise TenantLinearError('Səs sorğusunun mənbəyi dəyişib.', 409)
            external = 'connect:' + receipt['message_id']
            cur.execute('''SELECT id,kommo_lead_id,channel,direction,raw FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND external_id=%s FOR UPDATE''', (tenant, external))
            existing = cur.fetchone()
            raw = (existing or {}).get('raw') or {}
            if existing and (existing['kommo_lead_id'] != item['lead_id'] or existing['channel'] != 'connect:' + connection[1]
                             or existing['direction'] != 'outgoing' or not isinstance(raw, dict)
                             or raw.get('externalMessageId') != receipt['provider_message_id']):
                raise TenantLinearError('Səs tarixçəsinin mənbəyi təsdiqlənmədi.', 409)
            already = bool(existing and raw.get('voice_receipt_request_id') == request_id)
            # An early webhook row can carry a generic or unrelated status.
            # Only this flow's already-confirmed receipt can preserve it.
            previous = raw.get('status') if already else None
            ranks = {'unknown': 0, 'pending': 0, 'sent': 1, 'failed': 2, 'delivered': 3, 'read': 4}
            status = receipt['delivery_state']
            if ranks.get(previous, 0) > ranks.get(status, 0):
                status = previous
            facts = {'source': 'connect', 'id': receipt['message_id'], 'externalMessageId': receipt['provider_message_id'],
                     'originClientMessageId': request_id, 'verified_employee_id': actor,
                     'voice_receipt_request_id': request_id, 'fromMe': True, 'status': status}
            author = str(profile.get('display_name') or 'Operator')[:120]
            if existing:
                cur.execute('''UPDATE saas_crm_messages SET author_name=%s,message_type='audio',raw=COALESCE(raw,'{}'::jsonb)||%s::jsonb
                  WHERE tenant_id=%s::uuid AND id=%s::uuid''', (author, _json(facts), tenant, str(existing['id'])))
            else:
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
                  VALUES(%s,%s::uuid,%s,%s,'outgoing',%s,%s,'','audio','',now(),%s::jsonb)''',
                            (uuid.uuid4(), tenant, item['lead_id'], external, 'connect:' + connection[1], author, _json(facts)))
            if not already:
                mark_operator_reply(tenant, item['lead_id'], connection[1], connection[2], connection=conn, actor=actor)
            if saved['state'] != 'accepted':
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'chat_message_accepted','lead',%s,%s::jsonb)''',
                            (uuid.uuid4(),tenant,actor,str(item['lead_id']),_json({'message_id':receipt['message_id'],'type':'audio'})))
            cur.execute('''UPDATE saas_chat_send_receipts SET state='accepted',message_id=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''', (receipt['message_id'], tenant, actor, request_id))
        conn.commit()


async def run(session, command, content, logger):
    item = await asyncio.to_thread(reserve, session, command)
    if 'cached_result' in item:
        return await check(session, command['lead_id'], command['request_id'])
    if item['connection'][0] != 'connect':
        await asyncio.to_thread(finish, item, 'blocked')
        raise TenantLinearError('Səs yalnız Connect dialoqunda göndərilə bilər.', 409)
    if not re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|lid)',item['connection'][2]):
        await asyncio.to_thread(finish, item, 'blocked')
        raise TenantLinearError('Qrup dialoqlarında səs göndərilməsi hələ əlçatan deyil.', 409)
    started = False
    try:
        channel, conversation = item['connection'][1:]
        profile, route = await asyncio.to_thread(mark_sending, item, {'origin':'connect','voice_protocol':True,
                                                                      'channel_id':channel,'conversation_id':conversation})
        started = True
        receipt = await asyncio.to_thread(_submit, route, command['request_id'], command['file']['mime'], content)
        if receipt['provider_accepted']:
            await asyncio.to_thread(record_acceptance, session, {**item, 'lead_id': command['lead_id']}, receipt)
        else:
            await asyncio.to_thread(finish, item, 'unknown')
        return receipt
    except Exception as error:
        try:
            await asyncio.to_thread(finish, item, 'unknown' if started else 'blocked')
        except Exception:
            logger.error('Customer voice receipt persistence unavailable')
        if isinstance(error, TenantLinearError) and not started:
            raise
        result = TenantLinearError('Göndərmə nəticəsi məlum deyil. Təkrar göndərməyin; vəziyyəti yoxlayın.' if started
                                   else 'Səs göndərilmədi. Dialoq icazəsini yoxlayın.', 503)
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


def visible_receipt(session, lead_id, row_id):
    """Allow an authorised dialog viewer to check an already-accepted voice row."""
    try:
        lead_id = int(lead_id); row_id = str(uuid.UUID(str(row_id)))
        if not 0 < lead_id < 2**63: raise ValueError()
    except (ValueError, TypeError):
        raise TenantLinearError('Sorğu düzgün deyil.', 400) from None
    with _connect() as conn:
        with conn.cursor() as cur:
            profile, connection, integration = authority(cur, session, lead_id)
            if connection[0] != 'connect': raise TenantLinearError('Səs vəziyyəti əlçatan deyil.', 404)
            cur.execute('''SELECT external_id,raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND kommo_lead_id=%s AND channel=%s AND direction='outgoing' AND message_type='audio' ''',
                        (profile['tenant_id'],row_id,lead_id,'connect:'+connection[1]))
            row=cur.fetchone(); raw=(row or {}).get('raw') or {}
            try:
                request_id=str(uuid.UUID(str(raw['voice_receipt_request_id'])))
                sender=int(raw['verified_employee_id'])
                message_id=str(uuid.UUID(str(row['external_id']).removeprefix('connect:')))
                provider_id=raw['externalMessageId']
            except (ValueError,TypeError,KeyError):
                raise TenantLinearError('Səs vəziyyəti əlçatan deyil.', 404) from None
            if row['external_id']!='connect:'+message_id or not isinstance(provider_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider_id):
                raise TenantLinearError('Səs vəziyyəti əlçatan deyil.', 404)
            cur.execute('''SELECT route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid
              AND lead_id=%s AND state='accepted' AND message_id=%s''', (profile['tenant_id'],sender,request_id,lead_id,message_id))
            saved=cur.fetchone(); route=(saved or {}).get('route') or {}
            if (not saved or route.get('voice_protocol') is not True or
                    [route.get('channel_id'),route.get('conversation_id')]!=connection[1:]):
                raise TenantLinearError('Səs vəziyyəti əlçatan deyil.', 404)
            return {'tenant':profile['tenant_id'],'lead_id':lead_id,'row_id':row_id,'request_id':request_id,
                    'message_id':message_id,'provider_id':provider_id,'connection':connection,'credentials':integration['route']}


def save_visible_status(session, item, receipt):
    """Refresh only a previously verified row; never replay an employee handoff."""
    if (receipt.get('provider_accepted') is not True or receipt.get('message_id') != item['message_id'] or
            receipt.get('provider_message_id') != item['provider_id']):
        raise TenantLinearError('Səs vəziyyəti təsdiqlənmədi.', 409)
    fresh=visible_receipt(session,item['lead_id'],item['row_id'])
    if any(fresh[key]!=item[key] for key in ('tenant','connection','request_id','message_id','provider_id')):
        raise TenantLinearError('Dialoqa giriş dəyişib.', 409)
    with _connect() as conn:
        with conn.cursor() as cur:
            profile, connection, _=authority(cur,session,item['lead_id'])
            if profile['tenant_id']!=item['tenant'] or connection!=item['connection']:
                raise TenantLinearError('Dialoqa giriş dəyişib.', 409)
            cur.execute('''SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND kommo_lead_id=%s AND channel=%s AND direction='outgoing' FOR UPDATE''',
                        (item['tenant'],item['row_id'],item['lead_id'],'connect:'+connection[1]))
            row=cur.fetchone(); raw=(row or {}).get('raw') or {}
            if (not isinstance(raw,dict) or raw.get('voice_receipt_request_id')!=item['request_id'] or
                    raw.get('externalMessageId')!=item['provider_id']):
                raise TenantLinearError('Səs vəziyyəti dəyişib.', 409)
            ranks={'unknown':0,'pending':0,'sent':1,'failed':2,'delivered':3,'read':4}
            status=receipt['delivery_state']; old=raw.get('status')
            if ranks.get(old,0)>ranks.get(status,0):status=old
            cur.execute("UPDATE saas_crm_messages SET raw=COALESCE(raw,'{}'::jsonb)||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
                        (_json({'status':status}),item['tenant'],item['row_id']))
        conn.commit()
    return status


async def check_visible(session, lead_id, row_id):
    item=await asyncio.to_thread(visible_receipt,session,lead_id,row_id)
    receipt=await asyncio.to_thread(_connect_receipt,item['credentials'],item['request_id'])
    status=await asyncio.to_thread(save_visible_status,session,item,receipt)
    return {'delivery_state':status}
