"""Source-scoped creation and first send for a new direct WhatsApp chat.

The endpoint only creates a route after an exact tenant/channel/phone lookup.
An existing route is returned for opening in the ordinary dialog; the caller
must not send the composed text from the new-chat form in that case.
"""
import asyncio
import json
import re
import uuid

from aiohttp import web
from worker_crm_capacity import session_io

from tenant_ai_agents_api import _expected
from tenant_chat_send_policy import command as send_command, fingerprint
from tenant_chat_send_service import run as send_run
from tenant_dialog_access import require_source
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_platform import _connect
from tenant_request_origin import require_json_post


def normalize_international_number(value):
    """Accept explicit international E.164 only; never infer a country code."""
    if not isinstance(value, str):
        raise TenantLinearError('Введите номер в международном формате, например +994501234567.')
    number=value.strip()
    if not re.fullmatch(r'\+[1-9][0-9]{5,14}',number):
        raise TenantLinearError('Введите номер в международном формате: знак + и от 6 до 15 цифр.')
    return number[1:]


def _channel_options(profile):
    from tenant_connect_transport import _schema
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=access(cur,profile,'dialogs',action='update')
            cur.execute('''SELECT ch.connect_channel_id::text AS channel_id,ch.name
              FROM saas_connect_channels ch JOIN saas_tenants t ON t.id=ch.tenant_id AND t.status='active'
              JOIN saas_tenant_integrations i ON i.tenant_id=t.id AND i.provider='connect' AND i.status='connected'
              WHERE ch.tenant_id=%s::uuid AND ch.active AND ch.status NOT IN ('disabled','error')
              ORDER BY ch.name,ch.connect_channel_id''',(tenant,))
            result=[]
            for row in cur.fetchall():
                try:
                    require_source(cur,tenant,policy,'connect',row['channel_id'],'update')
                except TenantLinearError:
                    continue
                result.append({'channel_id':row['channel_id'],'name':row['name'] or 'WhatsApp'})
            return {'sources':result}


def _validate_payload(profile,payload):
    if not isinstance(payload,dict):
        raise TenantLinearError('Некорректный запрос.',400)
    _expected(payload,profile)
    try:
        channel=str(uuid.UUID(str(payload.get('channel_id') or '')))
        request_id=str(uuid.UUID(str(payload.get('request_id') or '')))
    except (TypeError,ValueError):
        raise TenantLinearError('Выберите WhatsApp-источник и повторите отправку.',400) from None
    phone=normalize_international_number(payload.get('phone'))
    text=payload.get('text')
    if not isinstance(text,str) or not text.strip() or len(text.strip())>8000:
        raise TenantLinearError('Введите сообщение длиной до 8000 символов.',400)
    return {'channel_id':channel,'request_id':request_id,'phone':phone,'text':text.strip()}


def _display_route(row):
    return {'provider':'connect','lead_id':int(row['lead_id']),
        'channel_id':str(row['connect_channel_id']),'conversation_id':str(row['conversation_id']),
        'channel_name':row.get('channel_name') or 'WhatsApp',
        'customer_name':(row.get('manual_display_name') or row.get('contact_name') or
                         row.get('display_name') or ('+'+str(row.get('phone') or ''))),
        'conversation_kind':'contact'}


def _find_exact_routes(cur,tenant,channel,phone):
    jid=phone+'@s.whatsapp.net'
    legacy=phone+'@c.us'
    cur.execute('''SELECT c.*,ch.name AS channel_name
      FROM saas_connect_conversations c
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
        AND c.conversation_id NOT LIKE '%%@g.us'
        AND c.conversation_kind IN ('contact','')
        AND (regexp_replace(COALESCE(c.phone,''),'[^0-9]','','g')=%s
          OR c.conversation_id=ANY(%s::text[])
          OR EXISTS(SELECT 1 FROM saas_connect_direct_identities i
            WHERE i.tenant_id=c.tenant_id AND i.connect_channel_id=c.connect_channel_id
              AND i.canonical_conversation_id=c.conversation_id AND i.identity_key=ANY(%s::text[])))
      ORDER BY c.lead_id LIMIT 3 FOR UPDATE OF c''',(tenant,channel,phone,[jid,legacy],[jid,legacy]))
    return cur.fetchall()


def _prepare(profile,payload):
    from tenant_connect_transport import _schema,connect_route
    from tenant_connect_identity import conflicted
    from tenant_connect_direct_identity import lock_identities, verified_phone_jid
    from tenant_dialog_lifecycle import enabled as lifecycle_enabled
    from tenant_dialog_rollout import guard
    from tenant_dialog_presence import is_online

    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs',action='update')
            require_source(cur,tenant,policy,'connect',payload['channel_id'],'update')
            cur.execute('''SELECT ch.name FROM saas_connect_channels ch JOIN saas_tenants t ON t.id=ch.tenant_id
              JOIN saas_tenant_integrations i ON i.tenant_id=t.id AND i.provider='connect' AND i.status='connected'
              WHERE ch.tenant_id=%s::uuid AND ch.connect_channel_id=%s::uuid AND ch.active
                AND ch.status NOT IN ('disabled','error') AND t.status='active' FOR SHARE OF ch,t,i''',
                (tenant,payload['channel_id']))
            channel_row=cur.fetchone()
            if not channel_row:raise TenantLinearError('WhatsApp-источник отключён или недоступен.',409)

            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                (f'new-direct-chat:{tenant}:{payload["channel_id"]}:{payload["phone"]}',))
            phone_jid=payload['phone']+'@s.whatsapp.net'
            lock_identities(cur,tenant,payload['channel_id'],[phone_jid, payload['phone']+'@c.us'])
            routes=_find_exact_routes(cur,tenant,payload['channel_id'],payload['phone'])
            if len(routes)>1:
                raise TenantLinearError('Для этого номера найдено несколько переписок в выбранном источнике. Попросите владельца проверить их.',409)
            if routes:
                row=routes[0]
                if (verified_phone_jid(cur,tenant,payload['channel_id'],row['conversation_id']) != phone_jid
                        or conflicted(cur,tenant,payload['channel_id'],str(row['conversation_id']))):
                    raise TenantLinearError('Номер связан с конфликтующими WhatsApp-идентификаторами. Откройте существующий диалог через список.',409)
                from tenant_dialog_lifecycle import require_access
                require_access(cur,tenant,actor,'connect',payload['channel_id'],row['conversation_id'],'view',owner=policy.profile.get('role')=='owner')
                route=_display_route(row)
                # An identical prior attempt may only be recovered through the
                # existing idempotent sender; no retry is dispatched here.
                from tenant_chat_send_schema import ensure_schema
                ensure_schema(conn)
                command=send_command({'lead_id':route['lead_id'],'request_id':payload['request_id'],'text':payload['text']})
                cur.execute('''SELECT lead_id,input_hash,state FROM saas_chat_send_receipts
                  WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',
                  (tenant,actor,payload['request_id']))
                receipt=cur.fetchone()
                recover=bool(receipt and int(receipt['lead_id'])==route['lead_id']
                             and receipt['input_hash']==fingerprint(command))
                conn.commit()
                return {'route':route,'existing':True,'recover':recover,'command':command if recover else None}

            lifecycle=lifecycle_enabled(cur,tenant)
            rollout=guard(cur,tenant) if lifecycle else None
            if lifecycle and not rollout.admission_enabled:
                raise TenantLinearError('Приём новых диалогов сейчас приостановлен.',409)
            if lifecycle and policy.profile.get('role')!='owner' and not is_online(cur,tenant,actor,lock=True,source_channel=payload['channel_id']):
                raise TenantLinearError('Установите статус «Онлайн», чтобы начать новый диалог.',403)

            conversation=payload['phone']+'@s.whatsapp.net'
            cur.execute('''INSERT INTO saas_connect_conversations
              (tenant_id,connect_channel_id,conversation_id,contact_name,phone,display_name,conversation_kind,last_message_at)
              VALUES(%s::uuid,%s::uuid,%s,'',%s,'','contact',now())
              ON CONFLICT(tenant_id,connect_channel_id,conversation_id) DO NOTHING
              RETURNING lead_id,connect_channel_id,conversation_id,contact_name,phone,display_name,manual_display_name''',
              (tenant,payload['channel_id'],conversation,payload['phone']))
            row=cur.fetchone()
            if not row:
                routes=_find_exact_routes(cur,tenant,payload['channel_id'],payload['phone'])
                if len(routes)!=1:raise TenantLinearError('Переписка с этим номером уже создаётся. Обновите список диалогов.',409)
                row=routes[0]
                from tenant_dialog_lifecycle import require_access
                require_access(cur,tenant,actor,'connect',payload['channel_id'],row['conversation_id'],'view',owner=policy.profile.get('role')=='owner')
                return {'route':_display_route(row),'existing':True,'recover':False}
            row={**row,'channel_name':channel_row['name']}
            route=_display_route(row)
            if conflicted(cur,tenant,payload['channel_id'],conversation):
                raise TenantLinearError('Номер связан с конфликтующими WhatsApp-идентификаторами.',409)
            # Bind the actual selected source before committing a new route.
            if not connect_route(cur,tenant,route['lead_id']):
                raise TenantLinearError('Выбранный WhatsApp-источник не подтвердил маршрут отправки.',409)
        conn.commit()
    return {'route':route,'existing':False,'recover':False,'lifecycle':lifecycle}


def handler(member_from_request,canonical_origin,request_provider,wait_slot,logger):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=await session_io.run(member_from_request, request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            if request.method=='GET':
                _expected(request.query,profile)
                result=await asyncio.to_thread(_channel_options,profile)
                return web.json_response({'success':True,'result':result},headers=headers)
            require_json_post(request,canonical_origin)
            if request.content_length is not None and request.content_length>12000:
                raise TenantLinearError('Слишком большой запрос.',413)
            payload=_validate_payload(profile,await request.json())
            prepared=await asyncio.to_thread(_prepare,profile,payload)
            command=prepared.get('command') or send_command({
                'lead_id':prepared['route']['lead_id'],'request_id':payload['request_id'],'text':payload['text']})
            if prepared['existing'] and not prepared['recover']:
                return web.json_response({'success':True,'result':{'existing':True,'route':prepared['route']}},headers=headers)
            if not prepared['existing'] and prepared.get('lifecycle'):
                from tenant_dialog_lifecycle import mutate
                accept_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"new-direct-chat:{profile['tenant_id']}:{profile['telegram_id']}:{payload['request_id']}"))
                await asyncio.to_thread(mutate,profile,{'action':'accept','provider':'connect',
                    'channel_id':prepared['route']['channel_id'],'conversation_id':prepared['route']['conversation_id'],
                    'lead_id':prepared['route']['lead_id'],'expected_revision':0,'request_id':accept_id})
            result=await send_run(profile,command,request_provider,wait_slot,logger)
            return web.json_response({'success':True,'result':{'existing':False,'route':prepared['route'],**result}},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),
                **({'keep_request':True} if getattr(error,'keep_request',False) else {})},status=error.status,headers=headers)
        except (TypeError,ValueError,KeyError):
            return web.json_response({'success':False,'error':'Проверьте номер, источник и текст сообщения.'},status=400,headers=headers)
        except Exception:
            logger.exception('New direct dialogue send failed')
            return web.json_response({'success':False,'error':'Результат отправки неизвестен. Не создавайте новый запрос; откройте этот источник и проверьте диалог.','keep_request':True},status=503,headers=headers)
    return handle
