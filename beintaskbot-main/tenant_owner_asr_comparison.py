"""One approved owner ASR comparison. Never writes production audio evidence.

The opaque scope is a one-case allowlist, not a general ASR/model-probe API.
No request may add a variant or supply a prompt, reference, URL or credential.
"""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
import requests
from tenant_platform import _connect, _json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_dialog_ai_context import resolve, binding
from tenant_operator_access import access
from tenant_dialog_consultations import route
from tenant_media_evidence import media_version
from tenant_chat_ai_schema import ensure_schema as chat_schema
from tenant_ai_spend_budget import ensure_schema as budget_schema, reserve_media, settle_media, limit_microusd
from tenant_ai_asr_model import quote
from tenant_chat_ai_settings import _company_key

APPROVED_SCOPE = '691129c0239935f2dc4d0c19740371c36615845e9b94aca4e862e776c6984ec2'
CAP = 60_000
VARIANTS = (('whisper-1', 'verbose_json'), ('gpt-4o-mini-transcribe', 'json'))
SOURCE_FIELDS = {'channel_id', 'conversation_id', 'message_id'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def scope_digest(tenant,data):
    return digest([tenant,data['channel_id'],data['conversation_id'],data['message_id']])


def schema(conn):
    chat_schema(conn);budget_schema(conn)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('owner-asr-comparison-schema',))
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_owner_asr_comparisons(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          scope_hash TEXT NOT NULL,run_id UUID NOT NULL,created_by BIGINT NOT NULL,
          source JSONB NOT NULL,version TEXT NOT NULL,agent_id UUID NOT NULL,
          byte_hash TEXT,upload_hash TEXT,upload_mime TEXT,upload_extension TEXT,duration_seconds NUMERIC,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,scope_hash),
          FOREIGN KEY(tenant_id,run_id) REFERENCES saas_chat_ai_runs(tenant_id,request_id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_owner_asr_variants(
          tenant_id UUID NOT NULL,scope_hash TEXT NOT NULL,model TEXT NOT NULL
            CHECK(model IN ('whisper-1','gpt-4o-mini-transcribe')),
          state TEXT NOT NULL CHECK(state IN ('reserved','sending','completed','unknown','skipped')),
          call_key TEXT NOT NULL,reserve_microusd BIGINT NOT NULL CHECK(reserve_microusd>0),
          parameters JSONB NOT NULL DEFAULT '{}'::jsonb,result JSONB,code TEXT NOT NULL DEFAULT '',dispatched_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,scope_hash,model),
          FOREIGN KEY(tenant_id,scope_hash) REFERENCES saas_owner_asr_comparisons(tenant_id,scope_hash))''')
    conn.commit()


def _source(data):
    if not isinstance(data,dict) or set(data)!=SOURCE_FIELDS:
        raise TenantLinearError('Некорректный источник сравнения.',400)
    value={**data,'channel_id':identifier(data['channel_id'])}
    if not isinstance(value['conversation_id'],str) or not isinstance(value['message_id'],str):
        raise TenantLinearError('Некорректный источник сравнения.',400)
    return value


def _authority(cur,profile,data):
    tenant,actor,policy=access(cur,profile,'dialogs')
    if policy.profile.get('role')!='owner' or not policy.can_use_chat_ai():
        raise TenantLinearError('Требуется активный владелец компании.',403)
    found=route(cur,tenant,policy,data['channel_id'],data['conversation_id'])
    if scope_digest(tenant,data)!=APPROVED_SCOPE:
        raise TenantLinearError('Сравнение недоступно для этого источника.',404)
    context=resolve(cur,tenant,data)
    cur.execute('''SELECT id,external_id,message_type,direction,raw FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s AND external_id=%s FOR SHARE''',
      (tenant,'connect:'+data['channel_id'],found['lead_id'],data['message_id']))
    row=cur.fetchone()
    if not row or row['direction']!='incoming' or row['message_type'] not in {'audio','voice','voice_message','ptt'}:
        raise TenantLinearError('Исходное аудио недоступно.',409)
    cur.execute("SELECT metadata,updated_at,secrets IS NOT NULL AS configured FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR SHARE",(tenant,))
    config=cur.fetchone() or {};meta=config.get('metadata') or {}
    if not config.get('configured') or any(meta.get(flag) is not True for flag in ('enabled','consent','audio_enabled')):
        raise TenantLinearError('Нужны сохранённый ключ, согласие и доступ к обработке аудио.',409)
    cur.execute('SELECT emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE',(tenant,))
    settings=cur.fetchone() or {}
    if settings.get('emergency_stop') is True:
        raise TenantLinearError('AI компании остановлен.',409)
    version=digest([str(row['id']),row['message_type'],media_version(row.get('raw')),binding(context),str(config['updated_at'])])
    return tenant,actor,found,context,meta,version


def _existing(cur,tenant,actor,current_version):
    cur.execute('SELECT * FROM saas_owner_asr_comparisons WHERE tenant_id=%s::uuid AND scope_hash=%s',(tenant,APPROVED_SCOPE))
    row=cur.fetchone()
    if not row:return None
    if row['created_by']!=actor:raise TenantLinearError('Сравнение связано с другим владельцем.',403)
    cur.execute('''SELECT v.model,v.state,v.parameters,v.result,v.code,v.reserve_microusd,s.state AS accounting_state,
      s.actual_microusd FROM saas_owner_asr_variants v LEFT JOIN saas_ai_spend_reservations s
      ON s.tenant_id=v.tenant_id AND s.call_key=v.call_key
      WHERE v.tenant_id=%s::uuid AND v.scope_hash=%s ORDER BY v.model''',(tenant,APPROVED_SCOPE))
    variants=[dict(v) for v in cur.fetchall()]
    # A dispatched request with no durable result is uncertain, never retryable.
    for item in variants:
        if item['state']=='sending':item['state']='unknown'
    return {'admitted':True,'version':row['version'],'context_current':row['version']==current_version,'historical':row['version']!=current_version,'byte_hash':row.get('byte_hash'),'upload_hash':row.get('upload_hash'),'upload_mime':row.get('upload_mime'),'upload_extension':row.get('upload_extension'),
        'duration_seconds':float(row['duration_seconds']) if row.get('duration_seconds') is not None else None,
        'max_calls':2,'max_cost_microusd':CAP,'variants':variants,'can_execute':False}


def _cap(tenant):
    # Missing/zero caps retain ordinary non-pilot semantics. An explicitly
    # malformed registry is not silently reinterpreted as an uncapped grant.
    try:
        registry=json.loads(os.environ.get('AI_TENANT_SPEND_LIMITS_MICROUSD','{}'))
        if not isinstance(registry,dict):raise ValueError()
        if tenant in registry:
            value=registry[tenant]
            if type(value) not in (int,str) or int(value)<0:raise ValueError()
    except (ValueError,TypeError):
        raise TenantLinearError('comparison_budget_configuration_invalid',409) from None
    return limit_microusd(tenant)


def _quotes():
    values=[quote(model) for model,_ in VARIANTS]
    if sum(int(item['reserve_microusd']) for item in values)>CAP:
        raise TenantLinearError('comparison_cost_cap_exceeded',409)
    return values


def preview(profile,data):
    data=_source(data)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,_,_,_,version=_authority(cur,profile,data)
            old=_existing(cur,tenant,actor,version)
            if old:return old
            try:
                _cap(tenant)
                quotes=_quotes()
            except TenantLinearError as error:
                code=str(error) if str(error) in {'comparison_budget_configuration_invalid','comparison_cost_cap_exceeded'} else 'comparison_quote_unavailable'
                return {'admitted':False,'can_execute':False,'version':version,'max_calls':2,
                    'max_cost_microusd':CAP,'blocked':True,'code':code}
            return {'admitted':False,'can_execute':True,'version':version,'max_calls':2,'max_cost_microusd':CAP,
                'variants':[{'model':m,'language':'az','response_format':f,'reserve_microusd':q['reserve_microusd']}
                    for (m,f),q in zip(VARIANTS,quotes)]}


def _admit(profile,data,expected):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,found,context,meta,version=_authority(cur,profile,data)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('owner-asr:'+tenant+':'+APPROVED_SCOPE,))
            old=_existing(cur,tenant,actor,version)
            if old:return None,old
            if version!=expected:raise TenantLinearError('Источник или настройки изменились. Обновите предпросмотр.',409)
            _cap(tenant)
            quotes=_quotes()
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+tenant,))
            cur.execute('''SELECT
              (SELECT COALESCE(sum(reserved_calls),0) FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date) AS manual,
              (SELECT COALESCE(sum(reserved_calls),0) FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND provider='connect' AND usage_day=(now() AT TIME ZONE 'UTC')::date) AS connect,
              (SELECT COALESCE(sum(reserved_calls),0) FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'
                AND (state IN ('generating','completed','unknown') OR reserved_calls>1 OR generated_at IS NOT NULL)) AS automatic''',(tenant,tenant,tenant))
            used=cur.fetchone()
            if int(used['manual'])+int(used['automatic'])+2>int(meta.get('daily_limit') or 50) or int(used['connect'])+2>int(meta.get('copilot_daily_limit') or 50):
                raise TenantLinearError('comparison_daily_call_limit',429)
            keys=['owner-asr:'+APPROVED_SCOPE+':'+model for model,_ in VARIANTS]
            cur.execute('SELECT state FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=ANY(%s::text[]) FOR UPDATE',(tenant,keys))
            if cur.fetchone():raise TenantLinearError('comparison_existing_accounting_without_case',409)
            run=str(uuid.uuid4())
            for (model,_),q in zip(VARIANTS,quotes):
                reserve_media(cur,tenant,'owner-asr:'+APPROVED_SCOPE+':'+model,'owner_asr_comparison',model,'asr',1,track_uncapped=True)
            cur.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'sending',2,'connect',%s,%s)''',
              (tenant,run,actor,found['lead_id'],APPROVED_SCOPE,version,data['channel_id'],data['conversation_id']))
            cur.execute('''INSERT INTO saas_owner_asr_comparisons(tenant_id,scope_hash,run_id,created_by,source,version,agent_id)
              VALUES(%s::uuid,%s,%s::uuid,%s,%s::jsonb,%s,%s::uuid)''',
              (tenant,APPROVED_SCOPE,run,actor,_json(data),version,context['agent_id']))
            for (model,_),q in zip(VARIANTS,quotes):
                cur.execute('''INSERT INTO saas_owner_asr_variants(tenant_id,scope_hash,model,state,call_key,reserve_microusd,parameters)
                  VALUES(%s::uuid,%s,%s,'reserved',%s,%s,%s::jsonb)''',(tenant,APPROVED_SCOPE,model,'owner-asr:'+APPROVED_SCOPE+':'+model,q['reserve_microusd'],
                  _json({'language':'az','response_format':dict(VARIANTS)[model],'prompt_supplied':False,**({'include':['logprobs']} if model=='gpt-4o-mini-transcribe' else {})})))
        conn.commit()
    return {'tenant':tenant,'actor':actor,'lead_id':found['lead_id'],'version':version,'source':data},None


def _download(profile,item):
    from tenant_chat_attachment_store import load
    from tenant_connect_media import download_media
    attachment=load(profile,item['lead_id'],item['source']['message_id'],channel_id=item['source']['channel_id'],conversation_id=item['source']['conversation_id'])
    async def bounded():return await asyncio.wait_for(download_media(attachment,max_bytes=20*1024*1024),timeout=15)
    return asyncio.run(bounded())


def _audio(content,mime):
    from tenant_connect_voice import validate_audio, FORMATS
    extension=FORMATS.get(mime)
    if not extension:raise ValueError('unsupported_audio')
    validated,extension,mime=validate_audio(content,extension,mime,max_seconds=10)
    with tempfile.TemporaryDirectory(prefix='owner-asr-duration-') as directory:
        file=Path(directory)/('audio.'+extension);file.write_bytes(validated)
        measured=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-xerror',
            '-protocol_whitelist','file,pipe','-format_whitelist','ogg,mp3,wav,mov,matroska,webm,flac,aac',
            '-i',str(file),'-map','0:a:0','-vn','-sn','-dn','-t','11','-ac','1','-ar','16000','-f','s16le','pipe:1'],
            capture_output=True,timeout=20,check=False)
    duration=len(measured.stdout)/32000
    if measured.returncode or not 8<=duration<=10:raise ValueError('unexpected_audio_duration')
    return validated,extension,mime,duration


def _transcribe(key,model,format_,content,extension,mime):
    # Exact two approved ASR requests only. No prompt/reference/previous text.
    data={'model':model,'language':'az','response_format':format_}
    if model=='gpt-4o-mini-transcribe':data['include[]']='logprobs'
    with requests.post('https://api.openai.com/v1/audio/transcriptions',headers={'Authorization':'Bearer '+key},
        data=data,files={'file':('comparison.'+extension,content,mime)},timeout=(5,45),allow_redirects=False,stream=True) as response:
        if response.status_code!=200:raise ValueError('provider_outcome_unknown')
        chunks=[];size=0
        for chunk in response.iter_content(16384):
            size+=len(chunk)
            if size>512*1024:raise ValueError('provider_response_bound')
            chunks.append(chunk)
    value=json.loads(b''.join(chunks));text=value.get('text')
    if not isinstance(text,str) or len(text)>16000:raise ValueError('provider_response_invalid')
    usage=value.get('usage') if isinstance(value.get('usage'),dict) else {}
    safe_usage={}
    for name in ('input_tokens','output_tokens','total_tokens','seconds'):
        val=usage.get(name)
        if type(val) in (int,float) and math.isfinite(val) and val>=0:safe_usage[name]=val
    if model=='gpt-4o-mini-transcribe' and (safe_usage.get('input_tokens',0)>16000 or safe_usage.get('output_tokens',0)>2000):
        raise ValueError('provider_usage_bound')
    if model=='whisper-1':
        segments=value.get('segments') or []
        if not isinstance(segments,list) or len(segments)>128:raise ValueError('provider_response_invalid')
        diagnostics={'language':str(value.get('language') or '')[:40],
            'segments':[{k:segment[k] for k in ('start','end','no_speech_prob','avg_logprob','compression_ratio')
                if type(segment.get(k)) in (int,float) and math.isfinite(segment[k])} for segment in segments if isinstance(segment,dict)]}
        if type(value.get('duration')) in (int,float) and math.isfinite(value['duration']):safe_usage['seconds']=value['duration']
    else:
        probabilities=value.get('logprobs') or []
        if not isinstance(probabilities,list) or len(probabilities)>2000:raise ValueError('provider_response_invalid')
        diagnostics={'logprobs':[{'token':str(p.get('token') or '')[:100],'logprob':p['logprob']} for p in probabilities
            if isinstance(p,dict) and type(p.get('logprob')) in (int,float) and math.isfinite(p['logprob'])]}
    return {'transcript':text,'usage':safe_usage,'diagnostics':diagnostics}


def _mark(profile,item,model,byte_hash,duration,*,upload_hash=None,upload_mime=None,upload_extension=None):
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,_,_,_,version=_authority(cur,profile,item['source'])
            _cap(tenant)
            if tenant!=item['tenant'] or actor!=item['actor'] or version!=item['version']:
                raise TenantLinearError('comparison_authority_changed',409)
            cur.execute('''UPDATE saas_owner_asr_comparisons SET byte_hash=COALESCE(byte_hash,%s),duration_seconds=COALESCE(duration_seconds,%s),
              upload_hash=COALESCE(upload_hash,%s),upload_mime=COALESCE(upload_mime,%s),upload_extension=COALESCE(upload_extension,%s)
              WHERE tenant_id=%s::uuid AND scope_hash=%s AND created_by=%s AND version=%s AND (byte_hash IS NULL OR byte_hash=%s) AND (upload_hash IS NULL OR upload_hash=%s) RETURNING run_id''',
              (byte_hash,duration,upload_hash,upload_mime,upload_extension,tenant,APPROVED_SCOPE,actor,version,byte_hash,upload_hash))
            if not cur.fetchone():raise TenantLinearError('comparison_source_changed',409)
            cur.execute("UPDATE saas_owner_asr_variants SET state='sending',dispatched_at=now() WHERE tenant_id=%s::uuid AND scope_hash=%s AND model=%s AND state='reserved' RETURNING model",(tenant,APPROVED_SCOPE,model))
            if not cur.fetchone():raise TenantLinearError('comparison_already_claimed',409)
        conn.commit()


def _outcome(item,model,state,result=None,code=''):
    if state not in {'completed','unknown','skipped'}:raise ValueError('Invalid outcome')
    expected='reserved' if state=='skipped' else 'sending'
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_owner_asr_variants SET state=%s,result=%s::jsonb,code=%s
              WHERE tenant_id=%s::uuid AND scope_hash=%s AND model=%s AND state=%s RETURNING model''',
              (state,_json(result),code,item['tenant'],APPROVED_SCOPE,model,expected))
            if not cur.fetchone():return
            cur.execute('SELECT model,state,result FROM saas_owner_asr_variants WHERE tenant_id=%s::uuid AND scope_hash=%s',(item['tenant'],APPROVED_SCOPE))
            outcomes=[dict(v) for v in cur.fetchall()]
            status=('unknown' if any(v['state'] in {'sending','unknown'} for v in outcomes) else
                    'sending' if any(v['state']=='reserved' for v in outcomes) else
                    'completed' if any(v['state']=='completed' for v in outcomes) else 'skipped')
            calls=sum(v['state']!='skipped' for v in outcomes)
            accounting={'owner_asr_comparison':True,'variants':[{'model':v['model'],'state':v['state'],
                'usage':(v.get('result') or {}).get('usage',{})} for v in outcomes]}
            cur.execute('''UPDATE saas_chat_ai_runs SET status=%s,reserved_calls=%s,result=%s::jsonb,updated_at=now()
              WHERE tenant_id=%s::uuid AND request_id=(SELECT run_id FROM saas_owner_asr_comparisons WHERE tenant_id=%s::uuid AND scope_hash=%s)''',
              (status,calls,_json(accounting),item['tenant'],item['tenant'],APPROVED_SCOPE))
        conn.commit()
    key='owner-asr:'+APPROVED_SCOPE+':'+model
    settle_media(item['tenant'],key,operation='asr',attempted_units=0 if state=='skipped' else 1,reserved_units=1,unknown=state=='unknown')


def execute(profile,data):
    required=SOURCE_FIELDS|{'version','confirmed','max_calls','max_cost_microusd'}
    if not isinstance(data,dict) or set(data)!=required or data['confirmed'] is not True or type(data['max_calls']) is not int or data['max_calls']!=2 or type(data['max_cost_microusd']) is not int or data['max_cost_microusd']!=CAP:
        raise TenantLinearError('Подтвердите ровно два вызова и общий предел $0.06.',400)
    source=_source({k:data[k] for k in SOURCE_FIELDS})
    # Read/download/validate before consuming the one-case admission. A transient
    # pre-provider media failure remains retryable; no ledger is reset or reused.
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,found,_,_,version=_authority(cur,profile,source)
            old=_existing(cur,tenant,actor,version)
            if old:return old
            if version!=data['version']:raise TenantLinearError('Источник или настройки изменились. Обновите предпросмотр.',409)
            _cap(tenant);_quotes()
    initial={'tenant':tenant,'actor':actor,'lead_id':found['lead_id'],'version':version,'source':source}
    try:
        key_tenant,meta,key=_company_key(profile)
        if key_tenant!=tenant or meta.get('audio_enabled') is not True:raise ValueError('authority')
        original,mime=_download(profile,initial)
        byte_hash=hashlib.sha256(original).hexdigest()
        content,extension,mime,duration=_audio(original,mime)
    except Exception:
        raise TenantLinearError('comparison_pre_dispatch_failed',409) from None
    # Revalidate the frozen preview after the read, then reserve both calls in
    # one transaction. Concurrent requests can download, but only one can admit.
    item,old=_admit(profile,source,data['version'])
    if old:return old
    for model,format_ in VARIANTS:
        try:_mark(profile,item,model,byte_hash,duration,upload_hash=hashlib.sha256(content).hexdigest(),upload_mime=mime,upload_extension=extension)
        except TenantLinearError:
            _outcome(item,model,'skipped',code='comparison_authority_changed');continue
        try:result=_transcribe(key,model,format_,content,extension,mime)
        except Exception:_outcome(item,model,'unknown',code='provider_outcome_unknown')
        else:_outcome(item,model,'completed',result)
    # A subsequent POST only reads the existing ledger; no new request ID can retry it.
    return preview(profile,source)
