"""Explicit one-message ASR; no text generation, sends or implicit retry."""
import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime,timezone,timedelta
from cryptography.fernet import InvalidToken
from tenant_platform import _connect,_fernet,_json
from tenant_linear_policy import TenantLinearError,identifier
from tenant_operator_access import access
from tenant_dialog_consultations import route
from tenant_dialog_ai_context import resolve,binding
from tenant_episode_media import ManualExecution,reference
from tenant_media_evidence import source_hash,media_version
import tenant_dialog_audio_store as store

SOURCE={'channel_id','conversation_id','message_id','message_row_id'}
POST=SOURCE|{'source_version','action','cost_token','idempotency_key'}

class AudioError(TenantLinearError):
    def __init__(self,code,message,status=409):
        self.code=code;super().__init__(message,status)


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def source(data,*,post=False):
    allowed=POST if post else SOURCE|{'history_before','idempotency_key'}
    if not isinstance(data,dict) or not SOURCE<=set(data) or set(data)-allowed:
        raise AudioError('invalid_request','Некорректный запрос.',400)
    if post and set(data)!=POST:raise AudioError('invalid_request','Некорректный запрос.',400)
    value={k:data[k] for k in SOURCE}
    value['channel_id']=identifier(value['channel_id']);value['message_row_id']=identifier(value['message_row_id'])
    if not isinstance(value['conversation_id'],str) or not 0<len(value['conversation_id'])<=200:
        raise AudioError('invalid_request','Некорректный диалог.',400)
    if not isinstance(value['message_id'],str) or not value['message_id'].startswith('connect:'):
        raise AudioError('invalid_request','Некорректное аудио.',400)
    identifier(value['message_id'].removeprefix('connect:'))
    return value


def token(payload):return _fernet().encrypt(json.dumps(payload,sort_keys=True,default=str).encode()).decode()


def untoken(value,purpose):
    try:
        if not isinstance(value,str) or len(value)>6000:raise ValueError()
        data=json.loads(_fernet().decrypt(value.encode(),ttl=600))
        if data.get('purpose')!=purpose:raise ValueError()
        return data
    except (InvalidToken,ValueError,TypeError,KeyError):
        raise AudioError('stale_cost_token','Обновите источник и подтверждение стоимости.') from None


def _authority(cur,profile,data,*,require_ai=False):
    tenant,actor,policy=access(cur,profile,'dialogs')
    if not policy.can_use_chat_ai():raise AudioError('chat_ai_permission_required','Нет доступа к Chat AI.',403)
    view=route(cur,tenant,policy,data['channel_id'],data['conversation_id'])
    vc=str(view['connect_channel_id']);conversation=view['conversation_id']
    cur.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,data['message_row_id']))
    row=cur.fetchone()
    if not row or row['external_id']!=data['message_id'] or not str(row['channel']).startswith('connect:'):
        raise AudioError('source_unavailable','Аудио недоступно в выбранном диалоге.',404)
    selection=dict(row)
    row=dict(row);sc=str(row['channel']).removeprefix('connect:');lead=int(row['kommo_lead_id'])
    from tenant_ai_group_coordination import group_jid
    if group_jid(conversation):
        from tenant_connect_group_history import page
        normalized=page(cur,tenant,conversation,selected_row_id=str(selection['id']),limit=1)
        if not normalized or str(normalized[0]['id'])!=str(selection['id']):
            raise AudioError('source_outside_view','Обновите выбранное сообщение.',403)
        copies=[copy for copy in normalized[0].get('sources',[]) if str(copy.get('channel_id'))==vc and int(copy.get('lead_id') or 0)==int(view['lead_id'])]
        if len(copies)!=1:raise AudioError('source_outside_view','Аудио не захвачено текущим каналом.',403)
        cur.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s AND external_id=%s FOR SHARE",(tenant,'connect:'+vc,int(view['lead_id']),copies[0]['external_id']))
        captured=cur.fetchall()
        if len(captured)!=1:raise AudioError('source_outside_view','Источник аудио неоднозначен.',403)
        row=dict(captured[0]);sc=vc;lead=int(view['lead_id']);source_conversation=conversation
        aliases=[{'channel_id':str(c['channel_id']),'external_id':str(c['external_id']),'lead_id':int(c['lead_id'])} for c in normalized[0].get('sources',[])]
    else:
        from tenant_connect_dialog_equivalence import members
        history=members(cur,tenant,vc,conversation)
        reverse={int(v):k for k,v in history.items()}
        if sc!=vc or lead not in reverse:raise AudioError('source_outside_view','Аудио не принадлежит истории этого диалога.',403)
        source_conversation=reverse[lead]
        aliases=[{'channel_id':sc,'external_id':row['external_id'],'lead_id':lead}]
    if row.get('message_type') not in {'audio','voice','voice_message','ptt'} or row.get('direction') not in {'incoming','outgoing'}:
        raise AudioError('audio_required','Выберите аудиосообщение.',415)
    ref=reference(row,sc,lead);version=source_hash(ref)
    cur.execute("SELECT metadata,updated_at,secrets IS NOT NULL AS configured FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR SHARE",(tenant,))
    connection=cur.fetchone() or {};config=connection.get('metadata') or {}
    auth={'tenant':tenant,'actor':actor,'row':row,'ref':ref,'source_version':version,'source_channel':sc,'source_conversation':source_conversation,'source_lead':lead,'selection':selection,
          'selection_version':source_hash(reference(selection,str(selection['channel']).removeprefix('connect:'),int(selection['kommo_lead_id']))),
          'aliases':sorted(aliases,key=lambda a:(a['channel_id'],a['external_id'])),
          'view_channel':vc,'view_conversation':conversation,'view_lead':int(view['lead_id']),'config':config,'config_version':str(connection.get('updated_at') or ''),'model':'','quote':None,'ai_error':None,'context':{}}
    try:
        if not connection.get('configured') or any(config.get(k) is not True for k in ('enabled','consent','audio_enabled')) or config.get('copilot_enabled',True) is not True:
            raise AudioError('audio_ai_disabled','Включите Chat AI, аудио и согласие на обработку данных.')
        cur.execute('SELECT emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE',(tenant,))
        if (cur.fetchone() or {}).get('emergency_stop') is True:raise AudioError('company_ai_stopped','AI компании остановлен.')
        try:
            context=resolve(cur,tenant,{'channel_id':vc,'conversation_id':conversation})
        except TenantLinearError as error:
            raise AudioError('agent_assignment_required',str(error),error.status) from None
        from tenant_operator_copilot import _MODEL_ID
        if not _MODEL_ID.fullmatch(str(context.get('model') or '')):raise AudioError('assigned_agent_model_required','Настройте модель назначенного агента.')
        auth['context']=context;auth['agent_binding']=binding(context)
        from tenant_connect_voice import transcription_model
        from tenant_ai_asr_model import shared_audio_model,scoped_strong_quote,quote,STRONG_MODEL
        raw=transcription_model({'config':config,'credential_source':'company'})
        model=shared_audio_model(raw,tenant=tenant,credential_source='company',context='manual_helper')
        scoped=(model==STRONG_MODEL and raw=='whisper-1')
        auth.update(model=model,scoped=scoped)
        try:
            auth['quote']=scoped_strong_quote(tenant) if scoped else quote(model)
        except TenantLinearError:
            raise AudioError('price_unavailable','Не настроен допустимый тариф ASR для выбранной модели.') from None
    except TenantLinearError as error:
        auth['ai_error']=error
        if require_ai:raise
    return auth


def _aliases(auth):
    return sorted(auth['aliases'],key=lambda a:(a['channel_id'],a['external_id']))


def _scope(auth):
    return {'tenant':auth['tenant'],'actor':auth['actor'],'channel_id':auth['view_channel'],'conversation_id':auth['view_conversation'],
            'message_id':auth['selection']['external_id'],'message_row_id':str(auth['selection']['id']),'source_version':auth['source_version'],
            'selection_version':auth['selection_version'],'physical_source_row_id':str(auth['row']['id']),'source_aliases_digest':digest(_aliases(auth)),
            'agent_binding':auth.get('agent_binding'),'config_version':auth['config_version'],'model':auth['model'],'quote':auth['quote'],
            'request_policy':{'model':auth['model'],'response_format':'json','language_policy':'auto'}}


def _redact(text):
    from tenant_knowledge_candidates_policy import redact
    return redact(text,max_chars=60000).strip()[:60000]


def legacy(cur,auth):
    # Archive both the captured physical source and the normalized selected DTO.
    # Other-channel historical evidence remains previous/unverified for reuse.
    rows=[];sources=[(auth['row'],auth['source_channel'],auth['ref'],True)]
    if str(auth['selection']['id'])!=str(auth['row']['id']):
        selected=auth['selection'];channel=str(selected['channel']).removeprefix('connect:')
        sources.append((selected,channel,reference(selected,channel,int(selected['kommo_lead_id'])),False))
    cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
    evidence=bool((cur.fetchone() or {}).get('relation'))
    for source_row,channel,ref,is_current in sources:
        version=source_hash(ref);old=(source_row.get('raw') or {}).get('voice_transcription') or {}
        if old.get('status')=='completed' and str(source_row.get('body') or '').strip():
            rows.append({'attempt_id':None,'origin':'legacy_body','source_version':version,
                'source_current':is_current and old.get('media_version')==ref['media_version'],
                'model':old.get('model'),'text':_redact(source_row['body']),'created_at':None})
        if evidence:
            cur.execute('''SELECT result,updated_at FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid
              AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s AND status IN ('processed','partial')''',
              (auth['tenant'],channel,source_row['external_id'],version))
            for row in cur.fetchall():
                result=row.get('result') or {};text=result.get('transcript')
                if isinstance(text,str) and text.strip():rows.insert(0,{'attempt_id':None,'origin':'evidence','source_version':version,'source_current':is_current,'model':result.get('model'),'text':_redact(text),'created_at':str(row['updated_at'])})
    return rows


def _attempt(row,version,selection_version=None):
    if not row:return None
    return {'id':str(row['id']),'attempt_id':str(row['id']),'generation':int(row['generation']),'origin':row['origin'],'state':row['state'],
        'source_version':row['source_version'],'source_current':row['source_version']==version and row['source_verified'] and (selection_version is None or row['selection_version']==selection_version),
        'model':row['model'] or None,'text':row['transcript'],'code':row['code'],'published':row['published'],
        'reserve_microusd':int(row['quote_microusd']),'created_at':str(row['created_at']),'usage':row.get('usage') or {}}



def _related_guard(cur,auth,*,hashes=(),fingerprints=()):
    # Aliases come only from the existing signed-event/participant group
    # normalization. A shared JID or a browser-supplied alias is never enough.
    for alias in _aliases(auth):
        if alias['channel_id']==auth['source_channel'] and alias['external_id']==auth['row']['external_id']:
            related=auth
        else:
            cur.execute("SELECT id,external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s AND external_id=%s",(auth['tenant'],'connect:'+alias['channel_id'],alias['lead_id'],alias['external_id']))
            rows=cur.fetchall()
            if len(rows)!=1:raise AudioError('source_changed','Обновите выбранное сообщение.')
            related={**auth,'row':dict(rows[0]),'source_channel':alias['channel_id']}
        state=store.legacy_guards(cur,related,hashes=hashes,fingerprints=fingerprints)
        if state:return state
    return None

def _read(cur,auth,query,requested=None):
    tenant=auth['tenant'];row_id=str(auth['row']['id']);version=auth['source_version'];head=store.head(cur,tenant,row_id)
    older=legacy(cur,auth);history=[];current=None
    if store.exists(cur):
        before=None
        if query.get('history_before'):
            before=untoken(query['history_before'],'audio_history_v1')
            if any(before.get(k)!=v for k,v in {'tenant':tenant,'actor':auth['actor'],'row':row_id,'view_channel':auth['view_channel'],'view_conversation':auth['view_conversation']}.items()):raise AudioError('history_scope_changed','Обновите историю.')
        cur.execute('''SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid
          AND (%s::timestamptz IS NULL OR (created_at,id)<(%s::timestamptz,%s::uuid)) ORDER BY created_at DESC,id DESC LIMIT 21''',
          (tenant,row_id,before.get('at') if before else None,before.get('at') if before else None,before.get('id') if before else None))
        history=[dict(r) for r in cur.fetchall()]
        cur.execute('''SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid
          AND generation=%s AND origin IN ('manual','reuse') ORDER BY created_at DESC LIMIT 1''',(tenant,row_id,int(head['generation'])))
        current=cur.fetchone()
        if requested is None and query.get('idempotency_key'):
            cur.execute('SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND idempotency_key=%s::uuid',(tenant,identifier(query['idempotency_key'])))
            requested=cur.fetchone()
            if requested and (requested['actor_id']!=auth['actor'] or str(requested['selection_row_id'])!=str(auth['selection']['id'])):raise AudioError('idempotency_scope_mismatch','Код запроса относится к другой операции.')
    success=store.current(cur,tenant,row_id,version,selection_version=auth['selection_version'])
    latest=_attempt(success,version,auth['selection_version']) if success else (older[0] if older else None)
    if latest and 'text' not in latest:latest['text']=latest.get('transcript','')
    blocked=_related_guard(cur,auth)
    state=blocked or ('blocked' if auth['ai_error'] else 'completed' if latest else 'ready')
    allowed=[] if state in {'pending','unknown','blocked'} else ['retranscribe' if latest else 'transcribe']
    cost=None
    if allowed:cost=token({'purpose':'audio_cost_v1',**_scope(auth),'generation':int(head['generation']),'actions':allowed})
    cursor=None
    if len(history)>20:
        last=history[19];cursor=token({'purpose':'audio_history_v1','tenant':tenant,'actor':auth['actor'],'row':row_id,'view_channel':auth['view_channel'],'view_conversation':auth['view_conversation'],'at':str(last['created_at']),'id':str(last['id'])})
    from tenant_operator_dialogs import _message
    message={**auth['selection'],'source_channel_id':str(auth['selection']['channel']).removeprefix('connect:'),'source_lead_id':int(auth['selection']['kommo_lead_id'])}
    if success:message['_explicit_audio_transcript']=success['transcript']
    price=auth['quote'] or {}
    return {'source':{k:_scope(auth)[k] for k in SOURCE|{'source_version'}},'state':state,'generation':int(head['generation']),
        'effective_model':auth['model'] or None,'price':{'currency':'USD','reserve_microusd':int(price.get('reserve_microusd') or 0),'max_duration_seconds':300,'accounting':'fixed_quote'},
        'allowed_actions':allowed,'blocked_reason':('provider_outcome_unknown' if blocked=='unknown' else 'source_pending' if blocked else getattr(auth['ai_error'],'code','ai_unavailable') if auth['ai_error'] else None),
        'blocked_message':('Предыдущий результат ASR неизвестен; повторный платный запрос заблокирован.' if blocked=='unknown' else 'Обработка этого аудио ещё выполняется.' if blocked else str(auth['ai_error']) if auth['ai_error'] else None),
        'cost_token':cost,'cost_token_expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat() if cost else None,
        'current_attempt':_attempt(current,version,auth['selection_version']),'requested_attempt':_attempt(requested,version,auth['selection_version']),
        'latest_success':latest,'history':[_attempt(r,version,auth['selection_version']) for r in history[:20]]+([] if history else older),'history_next_cursor':cursor,
        'message':_message(message,group=auth['view_conversation'].endswith('@g.us'))}


def preview(profile,data):
    clean=source(data)
    with _connect() as conn:
        with conn.cursor() as cur:
            auth=_authority(cur,profile,clean)
            return _read(cur,auth,data)


class AudioExecution(ManualExecution):
    """Current-view agent, exact selected source; deliberately no latest-draft fence."""
    def __init__(self,profile,auth,request_id):
        command={'channel_id':auth['view_channel'],'conversation_id':auth['view_conversation'],'lead_id':auth['view_lead'],
            'request_id':request_id,'_agent_id':str(auth['context']['agent_id']),'_agent_binding':auth['agent_binding']}
        super().__init__(auth['tenant'],auth['actor'],command,auth['config'],auth['config_version'],'',auth['context']['model'],auth['context'].get('language') or 'auto',[])
        self.profile=profile;self.auth=auth;self.prepaid_calls=1

    def check(self):
        with _connect() as conn:
            with conn.cursor() as cur:
                now=_authority(cur,self.profile,{k:_scope(self.auth)[k] for k in SOURCE},require_ai=True)
                if _scope(now)!=_scope(self.auth):raise AudioError('source_or_assignment_changed','Источник, назначение или настройки изменились.')

    def validate(self,ref):self.check()

    def load(self,profile,lead,external):
        self.check()
        from tenant_chat_attachment_store import load
        item=load(self.profile,self.auth['source_lead'],self.auth['row']['external_id'],channel_id=self.auth['source_channel'],conversation_id=self.auth['source_conversation'])
        if str(item['row']['id'])!=str(self.auth['row']['id']):raise AudioError('source_changed','Обновите выбранное аудио.')
        return item


def _download(execution):
    from tenant_connect_media import download_media
    return asyncio.run(download_media(execution.load({},execution.auth['source_lead'],execution.auth['row']['external_id']),max_bytes=10*1024*1024))


def _fingerprints(cur,auth,media):
    from tenant_knowledge_media_cache import transcription_fingerprint
    from tenant_connect_voice import FORMATS
    models={'whisper-1','gpt-4o-mini-transcribe','gpt-transcribe',auth['model']}
    cur.execute("SELECT to_regclass('public.saas_ai_spend_reservations') AS relation")
    if (cur.fetchone() or {}).get('relation'):
        cur.execute("SELECT DISTINCT model FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND (media_operation='asr' OR kind='knowledge_asr') LIMIT 30",(auth['tenant'],))
        models.update(r['model'] for r in cur.fetchall())
    # These are historical server-validated fingerprint options, NOT ASR language hints.
    languages={'','auto','az','ru','en','tr'}
    keys=set()
    for model in models:
        for mime in FORMATS:
            variant=(media[0],media[1],mime)
            keys.add(transcription_fingerprint({},model,variant))
            for language in languages:keys.add(transcription_fingerprint({'route':{'language':language}},model,variant,return_diagnostics=True))
    return list(keys)


def _daily(cur,auth,request_id,input_hash):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+auth['tenant'],))
    # Shared company count, including automatic runs. A prepaid ASR consumes exactly one slot.
    cur.execute('''SELECT (SELECT COALESCE(sum(reserved_calls),0) FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date)
      +(SELECT COALESCE(sum(reserved_calls),0) FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'
        AND (state IN ('generating','completed','unknown') OR reserved_calls>1 OR generated_at IS NOT NULL)) AS used''',(auth['tenant'],auth['tenant']))
    if int(cur.fetchone()['used'])>=int(auth['config'].get('daily_limit') or 50):raise AudioError('daily_limit','Дневной лимит AI компании исчерпан.',429)
    cur.execute("SELECT COALESCE(sum(reserved_calls),0) AS used FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND provider='connect' AND usage_day=(now() AT TIME ZONE 'UTC')::date",(auth['tenant'],))
    if int(cur.fetchone()['used'])>=int(auth['config'].get('copilot_daily_limit') or 50):raise AudioError('copilot_daily_limit','Дневной лимит Chat AI исчерпан.',429)
    cur.execute("SELECT count(*) AS used FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND requested_by=%s AND provider='connect' AND channel_id=%s AND conversation_id=%s AND usage_day=(now() AT TIME ZONE 'UTC')::date",(auth['tenant'],auth['actor'],auth['view_channel'],auth['view_conversation']))
    if int(cur.fetchone()['used'])>=20:raise AudioError('dialog_daily_limit','Дневной лимит AI этого диалога исчерпан.',429)
    cur.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'sending',1,'connect',%s,%s)''',
      (auth['tenant'],request_id,auth['actor'],auth['view_lead'],input_hash,auth['config_version'],auth['view_channel'],auth['view_conversation']))


def _admit(profile,data,media,original_hash):
    clean=source(data,post=True);request_id=identifier(data['idempotency_key']);input_hash=digest(data)
    upload_hash=hashlib.sha256(media[0]).hexdigest()
    with _connect() as conn:
        store.schema(conn)
        with conn.cursor() as cur:
            auth=_authority(cur,profile,clean)
            store.lock_bytes(cur,auth['tenant'],upload_hash)
            for alias in _aliases(auth):store.lock_source(cur,auth['tenant'],alias['channel_id'],alias['external_id'])
            cur.execute('SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND (idempotency_key=%s::uuid OR id=%s::uuid) FOR UPDATE',(auth['tenant'],request_id,request_id))
            existing=cur.fetchone()
            if existing:
                if existing['actor_id']!=auth['actor'] or existing['input_hash']!=input_hash:raise AudioError('idempotency_conflict','Код запроса уже относится к другой операции.')
                return auth,dict(existing),False
            if auth['ai_error']:raise auth['ai_error']
            cost=untoken(data['cost_token'],'audio_cost_v1')
            if any(cost.get(k)!=v for k,v in _scope(auth).items()) or data['source_version']!=auth['source_version'] or data['action'] not in {'transcribe','retranscribe'}:
                raise AudioError('preview_changed','Источник, назначение или стоимость изменились. Обновите подтверждение.')
            head=store.head(cur,auth['tenant'],str(auth['row']['id']))
            if int(cost.get('generation',-1))!=int(head['generation']):raise AudioError('stale_generation','Предыдущая попытка уже изменилась. Обновите состояние.')
            state=_related_guard(cur,auth,hashes=[original_hash,upload_hash],fingerprints=_fingerprints(cur,auth,media))
            if state:raise AudioError('provider_outcome_unknown' if state=='unknown' else 'source_pending','Предыдущая обработка ещё не завершена; новый ASR-вызов не разрешён.')
            old=legacy(cur,auth);saved=store.known_success(cur,auth['tenant'],str(auth['row']['id']),auth['source_version'],auth['selection_version'])
            if data['action']=='retranscribe' and not (saved or old):raise AudioError('no_previous_result','Сначала расшифруйте выбранное аудио.')
            if data['action'] not in cost.get('actions',[]) and not (data['action']=='transcribe' and (saved or old)):
                raise AudioError('preview_changed','Обновите подтверждение действия.')
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+auth['tenant'],))
            cur.execute('SELECT 1 FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE',(auth['tenant'],request_id))
            if cur.fetchone():raise AudioError('idempotency_conflict','Код запроса уже относится к другой AI-операции.')
            for item in old:store.import_success(cur,auth,item)
            reuse=(data['action']=='transcribe' and (saved or next((r for r in old if r['source_current']),None)))
            generation=int(head['generation'])+1
            cur.execute('''INSERT INTO saas_dialog_audio_sources(tenant_id,message_row_id,channel_id,external_id)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s) ON CONFLICT DO NOTHING''',
              (auth['tenant'],str(auth['row']['id']),auth['source_channel'],auth['row']['external_id']))
            call_key='dialog-audio:'+request_id
            cur.execute('SELECT 1 FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s FOR UPDATE',(auth['tenant'],call_key))
            if cur.fetchone():raise AudioError('attempt_state_unavailable','Состояние предыдущего запроса требует проверки; повторный ASR не разрешён.')
            if not reuse:
                _daily(cur,auth,request_id,input_hash)
                from tenant_ai_spend_budget import reserve_media
                opts={'_shared_dialog_asr':True} if auth.get('scoped') else {}
                reserve_media(cur,auth['tenant'],call_key,'dialog_audio_asr',auth['model'],'asr',1,**opts)
            text=(reuse.get('transcript',reuse.get('text','')) if reuse else '')
            cur.execute('''INSERT INTO saas_dialog_audio_attempts
              (tenant_id,id,message_row_id,selection_row_id,selection_version,channel_id,external_id,source_version,view_channel_id,view_conversation_id,source_aliases,generation,origin,actor_id,
               idempotency_key,input_hash,cost_token_hash,agent_id,agent_binding,config_version,model,request_policy,quote_microusd,call_key,state,
               original_sha256,upload_sha256,upload_mime,upload_extension,transcript,published,completed_at)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s,%s::jsonb,%s,%s,%s,%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                CASE WHEN %s THEN now() ELSE NULL END) RETURNING *''',
              (auth['tenant'],request_id,str(auth['row']['id']),str(auth['selection']['id']),auth['selection_version'],auth['source_channel'],auth['row']['external_id'],auth['source_version'],auth['view_channel'],auth['view_conversation'],_json(auth['aliases']),generation,
               'reuse' if reuse else 'manual',auth['actor'],request_id,input_hash,digest(data['cost_token']),str(auth['context']['agent_id']),auth['agent_binding'],auth['config_version'],
               (reuse.get('model') or '') if reuse else auth['model'],_json((reuse.get('request_policy') or {}) if reuse else _scope(auth)['request_policy']),0 if reuse else auth['quote']['reserve_microusd'],call_key,'completed' if reuse else 'reserved',
               original_hash,upload_hash,media[2],media[1],text,bool(reuse),bool(reuse)))
            attempt=dict(cur.fetchone())
            cur.execute('''UPDATE saas_dialog_audio_sources SET generation=%s,active_attempt_id=%s::uuid,
              current_success_id=CASE WHEN %s THEN %s::uuid ELSE current_success_id END
              WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid''',
              (generation,None if reuse else request_id,bool(reuse),request_id,auth['tenant'],str(auth['row']['id'])))
        conn.commit()
    return auth,attempt,not bool(reuse)


def _before_dispatch(execution,attempt):
    execution.check()
    auth=execution.auth
    with _connect() as conn:
        with conn.cursor() as cur:
            now=_authority(cur,execution.profile,{k:_scope(auth)[k] for k in SOURCE},require_ai=True)
            if _scope(now)!=_scope(auth):raise AudioError('source_or_assignment_changed','Источник или назначение изменились.')
            for alias in _aliases(auth):store.lock_source(cur,auth['tenant'],alias['channel_id'],alias['external_id'])
            cur.execute("UPDATE saas_dialog_audio_attempts SET state='sending',dispatched_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='reserved' RETURNING id",(auth['tenant'],str(attempt['id'])))
            if not cur.fetchone():raise AudioError('attempt_not_reserved','Попытка уже началась; повторного вызова не будет.')
        conn.commit()


def _finish(profile,auth,attempt,*,text='',usage=None,error=None):
    from tenant_ai_spend_budget import settle_media
    published=False
    with _connect() as conn:
        with conn.cursor() as cur:
            if error is None:
                try:
                    now=_authority(cur,profile,{k:_scope(auth)[k] for k in SOURCE},require_ai=True)
                    published=(_scope(now)==_scope(auth))
                except TenantLinearError:
                    published=False
            for alias in _aliases(auth):store.lock_source(cur,auth['tenant'],alias['channel_id'],alias['external_id'])
            cur.execute('SELECT state FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(auth['tenant'],str(attempt['id'])))
            row=cur.fetchone()
            if not row or row['state'] not in {'reserved','sending'}:return
            sent=row['state']=='sending'
            state='completed' if error is None and sent else 'unknown' if sent else 'failed_safe'
            published=published and state=='completed'
            code='' if state=='completed' and published else 'context_changed' if state=='completed' else 'provider_outcome_unknown' if sent else 'pre_dispatch_stopped'
            cur.execute('''UPDATE saas_dialog_audio_attempts SET state=%s,code=%s,transcript=%s,usage=%s::jsonb,published=%s,completed_at=now()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND state=%s RETURNING id''',
              (state,code,text if state=='completed' else '',_json(usage or {}),published,auth['tenant'],str(attempt['id']),row['state']))
            if not cur.fetchone():return
            cur.execute('''UPDATE saas_dialog_audio_sources SET active_attempt_id=%s::uuid,
              current_success_id=CASE WHEN %s THEN %s::uuid ELSE current_success_id END
              WHERE tenant_id=%s::uuid AND message_row_id=%s::uuid''',
              (str(attempt['id']) if state=='unknown' else None,published,str(attempt['id']),auth['tenant'],str(auth['row']['id'])))
            cur.execute('''UPDATE saas_chat_ai_runs SET status=%s,result=%s::jsonb WHERE tenant_id=%s::uuid AND request_id=%s::uuid''',
              ('completed' if state=='completed' else 'unknown' if state=='unknown' else 'skipped',_json({'audio_attempt_id':str(attempt['id']),'state':state}),auth['tenant'],str(attempt['id'])))
        conn.commit()
    settle_media(auth['tenant'],attempt['call_key'],operation='asr',attempted_units=int(sent),reserved_units=1,unknown=state=='unknown')


def execute(profile,data):
    clean=source(data,post=True);request_id=identifier(data['idempotency_key']);input_hash=digest(data)
    # Idempotent replay is resolved BEFORE expiry/download/config admission.
    with _connect() as conn:
        with conn.cursor() as cur:
            auth=_authority(cur,profile,clean)
            if store.exists(cur):
                cur.execute('SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND (idempotency_key=%s::uuid OR id=%s::uuid)',(auth['tenant'],request_id,request_id))
                old=cur.fetchone()
                if old:
                    if old['actor_id']!=auth['actor'] or old['input_hash']!=input_hash:raise AudioError('idempotency_conflict','Код запроса уже относится к другой операции.')
                    return _read(cur,auth,{},requested=old)
            if auth['ai_error']:raise auth['ai_error']
            cost=untoken(data['cost_token'],'audio_cost_v1')
            if any(cost.get(k)!=v for k,v in _scope(auth).items()) or data['source_version']!=auth['source_version']:
                raise AudioError('preview_changed','Обновите выбранное аудио и подтверждение стоимости.')
    execution=AudioExecution(profile,auth,request_id)
    content,mime=_download(execution)
    original_hash=hashlib.sha256(content).hexdigest()
    from tenant_connect_voice import validate_audio,FORMATS
    if mime not in FORMATS:raise AudioError('unsupported_audio','Формат аудио не поддерживается.',415)
    opts={'prove_upload_duration':True} if auth.get('scoped') else {}
    media=validate_audio(content,FORMATS[mime],mime,max_seconds=300,**opts)
    auth,attempt,dispatch=_admit(profile,data,media,original_hash)
    if dispatch:
        execution=AudioExecution(profile,auth,request_id)
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(auth['tenant'],))
                    secret=json.loads(_fernet().decrypt(bytes(cur.fetchone()['secrets'])))
                    if secret.get('tenant_id')!=auth['tenant'] or secret.get('provider')!='chat_ai' or not secret.get('api_key'):raise ValueError('key unavailable')
            from tenant_chat_ai_provider import transcribe
            callback=lambda:_before_dispatch(execution,attempt)
            if auth.get('scoped'):
                text,usage=transcribe(secret['api_key'],auth['model'],*media,with_usage=True,bounded_asr=True,asr_tenant=auth['tenant'],before_dispatch=callback)
            else:
                execution.check();callback()
                text,usage=transcribe(secret['api_key'],auth['model'],*media,with_usage=True)
            _finish(profile,auth,attempt,text=_redact(text),usage=usage)
        except Exception as error:
            _finish(profile,auth,attempt,error=error)
    with _connect() as conn:
        with conn.cursor() as cur:
            current=_authority(cur,profile,clean)
            cur.execute('SELECT * FROM saas_dialog_audio_attempts WHERE tenant_id=%s::uuid AND id=%s::uuid',(auth['tenant'],str(attempt['id'])))
            return _read(cur,current,{},requested=cur.fetchone())
