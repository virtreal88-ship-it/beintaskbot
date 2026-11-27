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
from tenant_chat_upload_policy import VIDEO_MAX_BYTES
from tenant_platform import _connect, TenantPlatformError
from tenant_linear_policy import TenantLinearError

NATIVE_KEY = 'native-route-requires-dispatch'
MEDIA_EVIDENCE_EVENTS = 4096
MEDIA_EVIDENCE_BYTES = 16 * 1024 * 1024
NATIVE_MEDIA_BYTES = 10 * 1024 * 1024


def message_actions_enabled():
    """Require the already active whatsmeow route and its full evidence path."""
    return all(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag) == '1'
               for flag in ('SEND', 'INBOX', 'PROJECTION'))


def _legacy(route):
    if route.get('native') or route.get('api_key') == NATIVE_KEY:
        raise TenantPlatformError('native_source_changed')
    # Never retain a stale read binding when the current source join is gone.
    return {k:v for k,v in route.items() if k!='native_media'}


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_whatsapp_native_routes (
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL, generation TEXT NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT FALSE,
      PRIMARY KEY(tenant_id,channel_id),
      FOREIGN KEY(tenant_id,channel_id,generation)
        REFERENCES saas_whatsapp_capture_generations(tenant_id,channel_id,generation))''')
    cur.execute('ALTER TABLE saas_whatsapp_native_routes ADD COLUMN IF NOT EXISTS legacy_fallback BOOLEAN NOT NULL DEFAULT FALSE')
    cur.execute('ALTER TABLE saas_whatsapp_native_routes ADD COLUMN IF NOT EXISTS paused BOOLEAN NOT NULL DEFAULT FALSE')
    cur.execute('''DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='native_route_single_sender') THEN
      ALTER TABLE saas_whatsapp_native_routes ADD CONSTRAINT native_route_single_sender CHECK(NOT(enabled AND legacy_fallback));
      END IF; END $$''')
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
    cur.execute('''SELECT r.enabled,r.legacy_fallback,r.generation,g.account_jid,s.gateway_id,
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
    # Only an explicit, audited operator rollback can set this. A disabled or
    # failed native route never becomes legacy automatically; stale native
    # requests are still rejected by _legacy rather than sent a second time.
    if row['legacy_fallback'] and not row['enabled'] and row['gateway_enabled'] and row['active']:
        legacy=_legacy(route)
        # Rollback changes the sender, not the provenance of retained media.
        # This separate binding never authorizes a native outgoing operation.
        if row['capture_enabled']:
            return {**legacy,'legacy_media_key':legacy['api_key'],'native_media':{'scope':{
                'tenant':str(route['tenant_id']),'source':str(route['channel_id']),
                'account':row['account_jid'],'generation':row['generation']},'gateway_id':str(row['gateway_id'])}}
        return legacy
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


def submit(route, request_id, *, text='', kind='text', mime='', name='', content=b'', quoted_message_id=None,
           reaction=None, edit=None, delete=None):
    if sum(value is not None for value in (reaction, edit, delete)) > 1:
        raise TenantPlatformError('native_message_invalid')
    request_id=str(uuid.UUID(str(request_id)))
    limit=VIDEO_MAX_BYTES if kind=='video' and mime=='video/mp4' else NATIVE_MEDIA_BYTES
    if not isinstance(content,bytes) or len(content)>limit:
        raise TenantPlatformError('native_media_invalid')
    from tenant_call_sources import service
    from tenant_whatsapp_native_projection import GROUP
    verify_group = route.get('verify_group_membership') or (
        kind == 'voice' and GROUP.fullmatch(route['conversation_id']))
    with dispatch_cursor(route) as cur:
        current=bind(cur,route)
        if not current.get('native') or current['native']!=route.get('native'):
            raise TenantPlatformError('native_source_changed')
        actor=route.get('native_actor_id',route.get('author_id'))
        operation=('employee:'+str(int(actor))+':' if actor is not None else 'service:')+request_id
        large_video=len(content)>NATIVE_MEDIA_BYTES
        payload={'scope':current['native']['scope'],'operation':operation,
            'chat':route['conversation_id'],'kind':kind,'text':text,'mime':mime,'name':name,
            'data':'' if large_video else base64.b64encode(content).decode('ascii')}
        if verify_group:
            payload['verify_group_membership'] = True
        if reaction is not None:
            payload['operation']='reaction:'+str(int(actor))+':'+request_id
            payload['reaction']=reaction
        if edit is not None:
            payload['operation']='edit:'+str(int(actor))+':'+request_id
            payload['edit']=edit
        if delete is not None:
            payload['operation']='delete:'+str(int(actor))+':'+request_id
            payload['delete']=delete
        if quoted_message_id is not None:
            try:
                payload.update(resolve_quote(cur, current, quoted_message_id))
            except TenantPlatformError as error:
                from tenant_connect_transport import ConnectPreflightError
                code = {'native_quote_scope_denied': 'quoted_source_scope_conflict',
                        'native_quote_evidence_missing': 'quoted_message_unavailable'}.get(str(error))
                if code:
                    raise ConnectPreflightError(code) from None
                raise
        if verify_group:
            from tenant_whatsapp_native_groups import require_membership
            # A failure here proves that no send request was submitted. The
            # engine rechecks immediately before dispatch, including after upload.
            require_membership(current, sending=True)
        url,token=service()
        # Keep the current source locked through dispatch. Caller also holds its
        # normal actor/dialogue authorization transaction. Never retry HTTP.
        try:
            headers={'Authorization':'Bearer '+token}
            path='/messages'
            if large_video:
                path+='/video'
                headers['Content-Type']='application/octet-stream'
                headers['X-Native-Video-Command']=base64.b64encode(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()).decode('ascii')
                options={'data':content,'timeout':(5,140)}
            else:
                options={'json':payload,'timeout':(5,40)}
            with requests.post(url+'/v1/sources/'+current['native']['gateway_id']+path,
                    headers=headers,allow_redirects=False,stream=True,**options) as response:
                body=bytearray()
                for chunk in response.iter_content(4096):
                    body.extend(chunk)
                    if len(body)>16384:raise ValueError()
                value=json.loads(body)
                if ((edit is not None or delete is not None) and response.status_code == 422
                        and value.get('confirmed') is False
                        and value.get('error') in {'native_edit_expired','native_delete_expired',
                                                   'native_action_original_unavailable'}):
                    from tenant_connect_transport import ConnectPreflightError
                    raise ConnectPreflightError(value['error'])
                if (quoted_message_id is not None and response.status_code == 422
                        and value.get('error') == 'native_quote_not_sent'
                        and value.get('confirmed') is False and 'result' not in value):
                    from tenant_connect_transport import ConnectPreflightError
                    raise ConnectPreflightError('quoted_source_scope_conflict')
                if (verify_group and response.status_code == 422
                        and value.get('confirmed') is False and value.get('delivery_phase')=='not_sent'
                        and 'result' not in value):
                    from tenant_whatsapp_native_groups import MEMBERSHIP_ERRORS, membership_error
                    if value.get('error') in MEMBERSHIP_ERRORS:
                        raise membership_error(value['error'], sending=True)
                result=value.get('result',{})
                provider=result.get('provider_id')
                if (response.status_code!=200 or value.get('confirmed') is not True or
                        result.get('state')!='accepted' or not isinstance(provider,str) or
                        not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider)):
                    raise ValueError()
        except TenantLinearError:
            raise
        except (requests.RequestException,ValueError,TypeError,AttributeError):
            raise TenantPlatformError('native_send_unconfirmed') from None
    if reaction is not None or edit is not None or delete is not None:
        return {'external_message_id':provider}
    from tenant_whatsapp_native_identity import message_id
    facts={'channelId':route['channel_id'],'conversationId':route['conversation_id'],
        'externalMessageId':provider,'fromMe':True,'status':'sent','_crm_native_send_receipt':True}
    if verify_group:
        facts['participantJid']=current['native']['scope']['account']
    return {'message_id':message_id(route['tenant_id'],route['channel_id'],route['conversation_id'],provider,True),
        'external_message_id':provider,'receipt':facts}


def text(route, value, request_id, *, quoted_message_id=None):
    return submit(route,request_id,text=value,quoted_message_id=quoted_message_id)


def resolve_quote(cur,route,provider_id):
    if not isinstance(provider_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider_id):
        raise TenantPlatformError('native_quote_scope_denied')
    cur.execute("""SELECT m.raw,m.body,m.direction,m.message_type FROM saas_crm_messages m
      JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
      WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.raw->>'externalMessageId'=%s
        AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s""",
      (route['tenant_id'],'connect:'+route['channel_id'],provider_id,route['channel_id'],route['conversation_id']))
    rows=cur.fetchall()
    if len(rows)!=1:raise TenantPlatformError('native_quote_scope_denied')
    facts=rows[0]['raw'] or {}
    event_id=facts.get('nativeEventId')
    if not isinstance(event_id,str) or not re.fullmatch(r'[0-9a-f]{64}',event_id):
        return legacy_quote(rows[0],route,provider_id)
    scope=route['native']['scope']
    cur.execute("""SELECT * FROM saas_whatsapp_native_inbox
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND event_id=%s""",
      (scope['tenant'],scope['source'],scope['generation'],event_id))
    row=cur.fetchone()
    if not row:raise TenantPlatformError('native_quote_evidence_missing')
    from tenant_whatsapp_native_projection import open_stored
    evidence=open_stored(row,scope)
    def verify_stored_alias(original, target):
        from tenant_connect_identity import conflicted
        from tenant_whatsapp_native_status import verified_receipt_chats
        tenant, channel = scope['tenant'], scope['source']
        return (facts.get('conversationIdentityConflict') is not True
                and original in verified_receipt_chats(cur, tenant, channel, target)
                and not conflicted(cur, tenant, channel, target))

    if (evidence.get('kind') != 'message' or evidence.get('variant')
            or evidence.get('provider_id') != provider_id
            or not quote_chat_matches(evidence, route['conversation_id'], facts, verify_stored_alias)):
        raise TenantPlatformError('native_quote_scope_denied')
    return {'quote_event':event_id,'quote_id':provider_id}


def legacy_quote(row,route,provider_id):
    # Historical rows were ingested through the old verified source boundary.
    # Never accept quote text/participant from the browser request.
    raw=row['raw'] or {}
    if row.get('message_type')!='text' or row.get('direction') not in {'incoming','outgoing'}:
        raise TenantPlatformError('native_quote_evidence_missing')
    chat=route['conversation_id'];account=route['native']['scope']['account']
    sender=account if row['direction']=='outgoing' else raw.get('participantJid') if chat.endswith('@g.us') else chat
    from tenant_whatsapp_native_projection import DIRECT
    body=row.get('body')
    if not isinstance(sender,str) or not DIRECT.fullmatch(sender) or not isinstance(body,str) or not 0<len(body)<=65536 or '\x00' in body:
        raise TenantPlatformError('native_quote_evidence_missing')
    return {'quote_id':provider_id,'legacy_quote':{'chat':chat,'provider_id':provider_id,'sender':sender,'text':body}}


def quote_chat_matches(evidence, conversation, facts, verify_alias=None):
    original=facts.get('_crm_provider_conversation_id') or facts.get('conversationId') or conversation
    if evidence.get('chat')!=original:return False
    if original==conversation:return True
    if original.endswith('@g.us') or conversation.endswith('@g.us'):return False
    alternate=evidence.get('recipient_alt') if evidence.get('from_me') else evidence.get('sender_alt')
    if not evidence.get('from_me') and evidence.get('sender')!=original:return False
    if {original.split('@')[-1], conversation.split('@')[-1]} != {'s.whatsapp.net', 'lid'}:
        return False
    if alternate:
        return alternate == conversation
    # Quote fallback only. The gateway independently checks its own device map.
    return verify_alias is not None and verify_alias(original, conversation)


def media_receipt(route,request_id,*,kind,mime,name='',caption='',content):
    result=submit(route,request_id,text=caption,kind=kind,mime=mime,name=name,content=content)
    return {'id':result['message_id'],'operationId':str(request_id),
        'channelId':route['channel_id'],'conversationId':route['conversation_id'],
        'clientMessageId':str(request_id),'authorId':str(route['author_id']),
        'crmConversationId':str(route['crm_conversation_id']),'inputHash':route.get('input_hash',''),
        'type':route.get('media_type','audio'),'status':'sent','providerAccepted':True,
        'externalMessageId':result['external_message_id'],'delivered':False,'retrySafe':False,
        **({'participantJid':result['receipt']['participantJid']} if 'participantJid' in result['receipt'] else {})}


def retained_media_evidence(cur,scope,facts,conversation,direction,kind):
    """Read-only recovery for legacy CRM rows predating native capture.

    The old CRM identity wins ingestion dedup and may lack nativeEventId. Never
    replay ingestion or change that row. Bound the encrypted search to this
    exact source/generation, 4096 events and 16 MiB (including ciphertext).
    Ordinary native messages keep the indexed event-ID lookup below.
    """
    from tenant_whatsapp_native_projection import open_stored
    provider=facts.get('externalMessageId')
    if not isinstance(provider,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider):
        raise TenantPlatformError('native_media_evidence_missing')
    if direction not in {'incoming','outgoing'}:
        raise TenantPlatformError('native_media_scope_denied')
    cur.execute('''WITH candidates AS MATERIALIZED (
      SELECT event_id,sequence,octet_length(encrypted_payload) AS bytes
      FROM saas_whatsapp_native_inbox WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
        AND generation=%s ORDER BY sequence DESC LIMIT %s
    ), bounded AS (
      SELECT event_id,SUM(bytes) OVER (ORDER BY sequence DESC) AS bytes FROM candidates
    ) SELECT i.* FROM saas_whatsapp_native_inbox i JOIN bounded b USING(event_id)
      WHERE i.tenant_id=%s::uuid AND i.channel_id=%s::uuid AND i.generation=%s
        AND b.bytes<=%s ORDER BY i.sequence DESC''',
      (scope['tenant'],scope['source'],scope['generation'],MEDIA_EVIDENCE_EVENTS,
       scope['tenant'],scope['source'],scope['generation'],MEDIA_EVIDENCE_BYTES))
    matches=[]
    for row in cur.fetchall():
        evidence=open_stored(row,scope)
        if (evidence.get('kind')=='message' and not evidence.get('variant')
                and evidence.get('provider_id')==provider
                and evidence.get('from_me') is (direction=='outgoing')
                and evidence.get('message_type')==kind
                and quote_chat_matches(evidence,conversation,facts)):
            matches.append(row)
    if len(matches)>1:raise TenantPlatformError('native_media_evidence_ambiguous')
    if not matches:raise TenantPlatformError('native_media_evidence_missing')
    return matches[0]


def download(route,message_id,*,max_bytes,allowed_types):
    from tenant_call_sources import service
    from tenant_whatsapp_native_projection import open_stored
    url,token=service()
    with _connect() as conn,conn.cursor() as cur:
        current=bind(cur,route)
        binding=current.get('native') or current.get('native_media')
        expected=route.get('native') or route.get('native_media')
        if not binding or binding!=expected:
            raise TenantPlatformError('native_source_changed')
        cur.execute('''SELECT m.raw,m.direction,m.message_type FROM saas_crm_messages m
          JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
          WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.external_id=%s
            AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s''',
          (route['tenant_id'],'connect:'+route['channel_id'],'connect:'+message_id,route['channel_id'],route['conversation_id']))
        rows=cur.fetchall()
        if len(rows)!=1:raise TenantPlatformError('native_media_scope_denied')
        facts=rows[0]['raw'] or {}
        event_id=facts.get('nativeEventId')
        scope=binding['scope']
        if event_id is None:
            row=retained_media_evidence(cur,scope,facts,route['conversation_id'],rows[0]['direction'],rows[0]['message_type'])
            event_id=row['event_id']
        else:
            if not isinstance(event_id,str) or not re.fullmatch(r'[0-9a-f]{64}',event_id):
                raise TenantPlatformError('native_media_scope_denied')
            cur.execute('''SELECT * FROM saas_whatsapp_native_inbox
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND generation=%s AND event_id=%s''',
              (scope['tenant'],scope['source'],scope['generation'],event_id))
            row=cur.fetchone()
        if not row:raise TenantPlatformError('native_media_evidence_missing')
        evidence=open_stored(row,scope)
        expected_chat=facts.get('_crm_provider_conversation_id') or facts.get('conversationId') or route['conversation_id']
        if (evidence.get('kind')!='message' or evidence.get('variant')
                or evidence.get('provider_id')!=facts.get('externalMessageId') or evidence.get('chat')!=expected_chat
                or rows[0]['direction'] not in {'incoming','outgoing'}
                or evidence.get('from_me') is not (rows[0]['direction']=='outgoing')
                or evidence.get('message_type')!=rows[0]['message_type']):
            raise TenantPlatformError('native_media_scope_denied')
        mime=str(evidence.get('media_mime','')).split(';')[0].lower().strip()
        size=evidence.get('media_size',0)
        if rows[0]['message_type']=='video':
            if mime not in allowed_types:
                raise TenantPlatformError('native_video_format')
            if type(size) is not int or size<=0:
                raise TenantPlatformError('native_video_size_unknown')
            if size>min(max_bytes,VIDEO_MAX_BYTES if mime=='video/mp4' else NATIVE_MEDIA_BYTES):
                raise TenantPlatformError('native_video_too_large')
        elif mime not in allowed_types or type(size) is not int or not 0<size<=min(max_bytes,10*1024*1024):
            raise TenantPlatformError('native_media_restricted')
        payload={'scope':scope,'event_id':event_id,'chat':evidence['chat']}
        try:
            headers={'Authorization':'Bearer '+token,'Accept-Encoding':'identity'}
            if rows[0]['message_type']=='video':
                headers['X-Native-Media-Kind']='video'
            with requests.post(url+'/v1/sources/'+binding['gateway_id']+'/messages/media',
                    headers=headers,json=payload,
                    timeout=(5,135 if size>NATIVE_MEDIA_BYTES else 40),allow_redirects=False,stream=True) as response:
                if rows[0]['message_type']=='video':
                    if response.status_code==404:
                        raise TenantPlatformError('native_video_expired')
                    if response.status_code==429:
                        from tenant_connect_media import retry_after_seconds
                        error=TenantPlatformError('native_video_busy')
                        error.retry_after=retry_after_seconds(response.headers.get('Retry-After'),default=5)
                        raise error
                if response.status_code!=200 or response.headers.get('Content-Encoding','identity')!='identity':raise ValueError()
                content=bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content)>size:raise ValueError()
                if len(content)!=size:raise ValueError()
        except (requests.RequestException,ValueError):
            raise TenantPlatformError('native_media_unavailable') from None
        return bytes(content),mime


def lookup(route,request_id,*,reaction=False):
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
                 'operation':('reaction:' if reaction else 'employee:')+str(int(actor))+':'+request_id}
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
        if reaction:
            return {'providerAccepted':accepted,'externalMessageId':provider}
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
