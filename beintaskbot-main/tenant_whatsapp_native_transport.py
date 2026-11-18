"""Source-bound Worker adapter. Existing callers retain user/lifecycle guards.

There is deliberately no automatic activation or fallback after a native send.
Cutover tooling must fence the old protocol owner before inserting a route.
"""
import base64
import json
import os
import re
import uuid
import requests
from contextlib import contextmanager
from tenant_platform import _connect, TenantPlatformError

NATIVE_KEY = 'native-route-requires-dispatch'


def _legacy(route):
    if route.get('native') or route.get('api_key') == NATIVE_KEY:
        raise TenantPlatformError('native_source_changed')
    return route


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_whatsapp_native_routes (
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL, generation TEXT NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT FALSE,
      PRIMARY KEY(tenant_id,channel_id),
      FOREIGN KEY(tenant_id,channel_id,generation)
        REFERENCES saas_whatsapp_capture_generations(tenant_id,channel_id,generation))''')
    from tenant_whatsapp_native_status import migrate as status_migrate
    status_migrate(cur)


def bind(cur, route):
    # Cutover must acquire this same key exclusively before changing a mode.
    # A shared lock also protects the ABSENCE of a native route during sends.
    cur.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',
        ('whatsapp-source-mode:'+str(route['tenant_id'])+':'+str(route['channel_id']),))
    cur.execute("SELECT to_regclass('saas_whatsapp_native_routes') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:
        return _legacy(route)
    cur.execute('''SELECT r.enabled,r.generation,g.account_jid,s.gateway_id,
       g.enabled AS capture_enabled,s.enabled AS gateway_enabled,c.active
      FROM saas_whatsapp_native_routes r
      JOIN saas_whatsapp_capture_generations g USING(tenant_id,channel_id,generation)
      JOIN saas_browser_call_sources s ON s.tenant_id=r.tenant_id AND s.channel_id=r.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid
      JOIN saas_connect_channels c ON c.tenant_id=r.tenant_id AND c.connect_channel_id=r.channel_id
      WHERE r.tenant_id=%s::uuid AND r.channel_id=%s::uuid FOR SHARE OF r,g,s,c''',
      (route['tenant_id'],route['channel_id']))
    row=cur.fetchone()
    if not row:
        # A retained native marker with a broken source join is NOT legacy.
        cur.execute('SELECT 1 FROM saas_whatsapp_native_routes WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',
          (route['tenant_id'],route['channel_id']))
        if cur.fetchone():raise TenantPlatformError('native_source_unavailable')
        return _legacy(route)
    if (os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_SEND')!='1' or
            not all(row[x] for x in ('enabled','capture_enabled','gateway_enabled','active'))):
        raise TenantPlatformError('native_source_unavailable')
    return {**route,'api_key':NATIVE_KEY,'legacy_media_key':route.get('legacy_media_key') or route.get('api_key'), 'native':{'scope':{
        'tenant':str(route['tenant_id']),'source':str(route['channel_id']),
        'account':row['account_jid'],'generation':row['generation']},'gateway_id':str(row['gateway_id'])}}


def guard_dispatch(cur,tenant,expected):
    current=bind(cur,{**expected,'tenant_id':str(tenant),'native':None} if not expected.get('native') else expected)
    if current.get('native')!=expected.get('native'):
        raise TenantPlatformError('native_source_changed')


@contextmanager
def dispatch_cursor(route):
    if route.get('_native_cursor') is not None:
        yield route['_native_cursor']
    else:
        with _connect() as conn,conn.cursor() as cur:
            yield cur


def submit(route, request_id, *, text='', kind='text', mime='', name='', content=b'', quoted_message_id=None):
    request_id=str(uuid.UUID(str(request_id)))
    if not isinstance(content,bytes) or len(content)>10*1024*1024:
        raise TenantPlatformError('native_media_invalid')
    from tenant_call_sources import service
    url,token=service()
    with dispatch_cursor(route) as cur:
        current=bind(cur,route)
        if not current.get('native') or current['native']!=route.get('native'):
            raise TenantPlatformError('native_source_changed')
        actor=route.get('native_actor_id',route.get('author_id'))
        operation=('employee:'+str(int(actor))+':' if actor is not None else 'service:')+request_id
        payload={'scope':current['native']['scope'],'operation':operation,
            'chat':route['conversation_id'],'kind':kind,'text':text,'mime':mime,'name':name,
            'data':base64.b64encode(content).decode('ascii')}
        if quoted_message_id is not None:
            payload.update(resolve_quote(cur,current,quoted_message_id))
        # Keep the current source locked through dispatch. Caller also holds its
        # normal actor/dialogue authorization transaction. Never retry HTTP.
        try:
            with requests.post(url+'/v1/sources/'+current['native']['gateway_id']+'/messages',
                    headers={'Authorization':'Bearer '+token},json=payload,
                    timeout=(5,40),allow_redirects=False,stream=True) as response:
                body=bytearray()
                for chunk in response.iter_content(4096):
                    body.extend(chunk)
                    if len(body)>16384:raise ValueError()
                value=json.loads(body)
                result=value.get('result',{})
                provider=result.get('provider_id')
                if (response.status_code!=200 or value.get('confirmed') is not True or
                        result.get('state')!='accepted' or not isinstance(provider,str) or
                        not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider)):
                    raise ValueError()
        except (requests.RequestException,ValueError,TypeError,AttributeError):
            raise TenantPlatformError('native_send_unconfirmed') from None
    from tenant_whatsapp_native_identity import message_id
    facts={'channelId':route['channel_id'],'conversationId':route['conversation_id'],
        'externalMessageId':provider,'fromMe':True,'status':'sent','_crm_native_send_receipt':True}
    return {'message_id':message_id(route['tenant_id'],route['channel_id'],route['conversation_id'],provider,True),
        'external_message_id':provider,'receipt':facts}


def text(route, value, request_id, *, quoted_message_id=None):
    return submit(route,request_id,text=value,quoted_message_id=quoted_message_id)


def resolve_quote(cur,route,provider_id):
    if not isinstance(provider_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider_id):
        raise TenantPlatformError('native_quote_scope_denied')
    cur.execute("""SELECT m.raw FROM saas_crm_messages m
      JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
      WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.raw->>'externalMessageId'=%s
        AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s""",
      (route['tenant_id'],'connect:'+route['channel_id'],provider_id,route['channel_id'],route['conversation_id']))
    rows=cur.fetchall()
    if len(rows)!=1:raise TenantPlatformError('native_quote_scope_denied')
    facts=rows[0]['raw'] or {}
    event_id=facts.get('nativeEventId')
    if not isinstance(event_id,str) or not re.fullmatch(r'[0-9a-f]{64}',event_id):
        raise TenantPlatformError('native_quote_evidence_missing')
    scope=route['native']['scope']
    cur.execute("""SELECT * FROM saas_whatsapp_native_inbox
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND event_id=%s""",
      (scope['tenant'],scope['source'],scope['generation'],event_id))
    row=cur.fetchone()
    if not row:raise TenantPlatformError('native_quote_evidence_missing')
    from tenant_whatsapp_native_projection import open_stored
    evidence=open_stored(row,scope)
    if (evidence.get('kind')!='message' or evidence.get('variant') or
            evidence.get('provider_id')!=provider_id or not quote_chat_matches(evidence,route['conversation_id'],facts)):
        raise TenantPlatformError('native_quote_scope_denied')
    return {'quote_event':event_id,'quote_id':provider_id}


def quote_chat_matches(evidence,conversation,facts):
    original=facts.get('_crm_provider_conversation_id') or facts.get('conversationId') or conversation
    if evidence.get('chat')!=original:return False
    if original==conversation:return True
    if original.endswith('@g.us') or conversation.endswith('@g.us'):return False
    alternate=evidence.get('recipient_alt') if evidence.get('from_me') else evidence.get('sender_alt')
    if not evidence.get('from_me') and evidence.get('sender')!=original:return False
    return alternate==conversation and {original.split('@')[-1],conversation.split('@')[-1]}=={'s.whatsapp.net','lid'}


def media_receipt(route,request_id,*,kind,mime,name='',caption='',content):
    result=submit(route,request_id,text=caption,kind=kind,mime=mime,name=name,content=content)
    return {'id':result['message_id'],'operationId':str(request_id),
        'channelId':route['channel_id'],'conversationId':route['conversation_id'],
        'clientMessageId':str(request_id),'authorId':str(route['author_id']),
        'crmConversationId':str(route['crm_conversation_id']),'inputHash':route.get('input_hash',''),
        'type':route.get('media_type','audio'),'status':'sent','providerAccepted':True,
        'externalMessageId':result['external_message_id'],'delivered':False,'retrySafe':False}


def download(route,message_id,*,max_bytes,allowed_types):
    from tenant_call_sources import service
    from tenant_whatsapp_native_projection import open_stored
    url,token=service()
    with _connect() as conn,conn.cursor() as cur:
        current=bind(cur,route)
        if not current.get('native') or current['native']!=route.get('native'):
            raise TenantPlatformError('native_source_changed')
        cur.execute('''SELECT m.raw FROM saas_crm_messages m
          JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
          WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.external_id=%s
            AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s''',
          (route['tenant_id'],'connect:'+route['channel_id'],'connect:'+message_id,route['channel_id'],route['conversation_id']))
        rows=cur.fetchall()
        if len(rows)!=1:raise TenantPlatformError('native_media_scope_denied')
        event_id=(rows[0]['raw'] or {}).get('nativeEventId')
        if not isinstance(event_id,str) or not re.fullmatch(r'[0-9a-f]{64}',event_id):
            raise TenantPlatformError('native_media_evidence_missing')
        scope=current['native']['scope']
        cur.execute('''SELECT * FROM saas_whatsapp_native_inbox
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND event_id=%s''',
          (scope['tenant'],scope['source'],scope['generation'],event_id))
        row=cur.fetchone()
        if not row:raise TenantPlatformError('native_media_evidence_missing')
        evidence=open_stored(row,scope)
        facts=rows[0]['raw'] or {}
        expected_chat=facts.get('_crm_provider_conversation_id') or facts.get('conversationId') or route['conversation_id']
        if evidence.get('provider_id')!=facts.get('externalMessageId') or evidence.get('chat')!=expected_chat:
            raise TenantPlatformError('native_media_scope_denied')
        mime=str(evidence.get('media_mime','')).split(';')[0].lower().strip()
        size=evidence.get('media_size',0)
        if mime not in allowed_types or type(size) is not int or not 0<size<=min(max_bytes,10*1024*1024):
            raise TenantPlatformError('native_media_restricted')
        payload={'scope':scope,'event_id':event_id,'chat':evidence['chat']}
        try:
            with requests.post(url+'/v1/sources/'+current['native']['gateway_id']+'/messages/media',
                    headers={'Authorization':'Bearer '+token,'Accept-Encoding':'identity'},json=payload,
                    timeout=(5,40),allow_redirects=False,stream=True) as response:
                if response.status_code!=200 or response.headers.get('Content-Encoding','identity')!='identity':raise ValueError()
                content=bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content)>size:raise ValueError()
                if len(content)!=size:raise ValueError()
        except (requests.RequestException,ValueError):
            raise TenantPlatformError('native_media_unavailable') from None
        return bytes(content),mime


def lookup(route,request_id):
    """Private read of this actor's exact operation. Never resend on uncertainty."""
    request_id=str(uuid.UUID(str(request_id)))
    actor=route.get('native_actor_id',route.get('author_id'))
    if actor is None:raise TenantPlatformError('native_receipt_actor_required')
    from tenant_call_sources import service
    from tenant_whatsapp_native_identity import message_id
    url,token=service()
    with dispatch_cursor(route) as cur:
        current=bind(cur,route)
        if not current.get('native') or current['native']!=route.get('native'):
            raise TenantPlatformError('native_source_changed')
        payload={'scope':current['native']['scope'],'chat':route['conversation_id'],
                 'operation':'employee:'+str(int(actor))+':'+request_id}
        try:
            with requests.post(url+'/v1/sources/'+current['native']['gateway_id']+'/messages/status',
                  headers={'Authorization':'Bearer '+token},json=payload,timeout=(5,15),allow_redirects=False,stream=True) as response:
                body=bytearray()
                for chunk in response.iter_content(4096):
                    body.extend(chunk)
                    if len(body)>16384:raise ValueError()
                value=json.loads(body);result=value.get('result',{});provider=result.get('provider_id')
                if (response.status_code!=200 or result.get('state') not in {'unknown','accepted'} or
                        not isinstance(provider,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider) or
                        value.get('confirmed') is not (result['state']=='accepted')):raise ValueError()
        except (requests.RequestException,ValueError,TypeError,AttributeError):
            raise TenantPlatformError('native_receipt_unavailable') from None
        accepted=result['state']=='accepted'
        status='sent' if accepted else 'unknown'
        if accepted and not route['conversation_id'].endswith('@g.us'):
            cur.execute("""SELECT MAX(rank) AS rank FROM saas_whatsapp_native_status
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND chat=%s AND provider_id=%s""",
              (route['tenant_id'],route['channel_id'],route['conversation_id'],provider))
            rank=cur.fetchone()['rank']
            status={2:'delivered',3:'read'}.get(rank,status)
    return {'id':message_id(route['tenant_id'],route['channel_id'],route['conversation_id'],provider,True),
        'operationId':request_id,'channelId':route['channel_id'],'conversationId':route['conversation_id'],
        'clientMessageId':request_id,'authorId':str(actor),'crmConversationId':str(route.get('crm_conversation_id','')),
        'inputHash':route.get('input_hash',''),'type':route.get('media_type','audio'),
        'status':status,'providerAccepted':accepted,'externalMessageId':provider,
        'delivered':status in {'delivered','read'},'retrySafe':False}
