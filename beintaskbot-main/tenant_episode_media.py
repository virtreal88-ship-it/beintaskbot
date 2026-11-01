"""Current-episode evidence, shared by real manual and automatic admissions.

No sends, learning candidates or synthetic worker leases. A committed flight is
never reclaimed after a crash: an unknown paid outcome requires explicit repair.
"""
import time
import uuid
from datetime import datetime, timedelta

from tenant_platform import _connect, _json
from tenant_linear_policy import TenantLinearError
from tenant_media_evidence import (source_hash, media_version, _body_hash, reusable,
                                  apply_reusable, persist, create_schema)

KINDS = {'audio', 'voice', 'image', 'video', 'document'}


def episode(rows, *, latest_request=False):
    """Latest inbound burst; manual may explicitly revisit an answered request."""
    if latest_request:
        # Manual regeneration addresses the latest customer's request even
        # after an employee replied. Auto keeps its unanswered/resume fence.
        index=next((i for i in range(len(rows)-1,-1,-1)
            if rows[i].get('direction')=='incoming'
            and (rows[i].get('raw') if isinstance(rows[i].get('raw'),dict) else {}).get('verified_employee_id') is None
            and (rows[i].get('raw') if isinstance(rows[i].get('raw'),dict) else {}).get('ai_authored') is not True),None)
        if index is None:return []
        rows=rows[:index+1]
    selected=[];end=None
    for row in reversed(rows):
        raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
        if row.get('direction')=='outgoing' or raw.get('verified_employee_id') is not None or (latest_request and raw.get('ai_authored') is True) or (not latest_request and row.get('voice_eligible') is False):
            break
        try: stamp=datetime.fromisoformat(str(row.get('happened_at') or '').replace('Z','+00:00'))
        except ValueError: stamp=None
        if end and stamp and end-stamp>timedelta(minutes=10): break
        if end is None and stamp:end=stamp
        if row.get('direction')=='incoming': selected.append(row)
    return list(reversed(selected))


def reference(row, channel, lead):
    external=str(row.get('external_id') or '')
    if not external.startswith('connect:') and not str(row.get('channel') or '').startswith('connect:'):
        external='connect:'+external
    return {'id':external,'external_id':external,'channel_id':str(channel),
            'lead_id':int(lead),'role':'client','happened_at':(row['happened_at'].isoformat()
                if hasattr(row.get('happened_at'),'isoformat') else str(row.get('happened_at') or '')),
            'body_hash':_body_hash(row.get('body')),
            'media_kind':str(row.get('message_type') or '').lower(),
            'media_version':media_version(row.get('raw'))}


def _schema(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('episode-media-schema',))
    create_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_episode_media_flights(
          tenant_id UUID NOT NULL,channel_id UUID NOT NULL,external_id TEXT NOT NULL,
          source_hash TEXT NOT NULL,token UUID NOT NULL,state TEXT NOT NULL
          CHECK(state IN ('running','completed','unknown')),result JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,external_id,source_hash))''')


def _flight(authority, ref):
    authority.check()
    token=str(uuid.uuid4());key=(authority.tenant,ref['channel_id'],ref['external_id'],source_hash(ref))
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            if ref.get('media_kind') in {'voice','audio'}:
                # Serialize with the retained legacy ASR worker during rollout.
                # Its old message-only receipt cannot certify a media version.
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                  ('voice-source:'+authority.tenant+':'+ref['channel_id']+':'+ref['external_id'],))
                cur.execute("SELECT to_regclass('public.saas_connect_voice_receipts') AS relation")
                if (cur.fetchone() or {}).get('relation'):
                    cur.execute('''SELECT v.state,v.error_code,m.body,m.raw FROM saas_connect_voice_receipts v
                      JOIN saas_crm_messages m ON m.tenant_id=v.tenant_id AND m.external_id=v.external_id
                        AND m.channel='connect:'||v.channel_id::text
                      WHERE v.tenant_id=%s::uuid AND v.channel_id=%s::uuid AND v.external_id=%s''',key[:3])
                    legacy=cur.fetchone()
                    if legacy:
                        raw=legacy.get('raw') if isinstance(legacy.get('raw'),dict) else {}
                        transcription=raw.get('voice_transcription') if isinstance(raw.get('voice_transcription'),dict) else {}
                        version=transcription.get('media_version')
                        if legacy['state']=='completed' and version==ref['media_version'] and legacy.get('body'):
                            conn.commit()
                            return key,None,{'status':'processed','code':'legacy_exact_transcript',
                                'transcript':str(legacy['body'])[:4000],'coverage':{'complete':True}}
                        safe_pre_dispatch={'voice_media_unavailable','voice_invalid_audio','voice_unsupported',
                            'voice_duration_limit','voice_size_limit','voice_decoder_unavailable','voice_decode_timeout'}
                        may_process=(legacy['state']=='failed' and legacy.get('error_code') in safe_pre_dispatch)
                        changed=(version and version!=ref['media_version'] and legacy['state']!='processing')
                        if not may_process and not changed:
                            conn.commit()
                            return key,None,{'status':'unavailable','code':'legacy_voice_version_unverified'}
            cur.execute('''INSERT INTO saas_episode_media_flights
              (tenant_id,channel_id,external_id,source_hash,token,state)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s::uuid,'running')
              ON CONFLICT DO NOTHING RETURNING token''',(*key,token))
            fresh=bool(cur.fetchone())
        conn.commit()
    if fresh:return key,token,None
    deadline=time.monotonic()+55
    while True:
        authority.check()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT state,result FROM saas_episode_media_flights WHERE
                  tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s''',key)
                prior=cur.fetchone() or {}
        if prior.get('state')=='completed':return key,None,prior['result']
        if not prior:
            return key,None,{'status':'unavailable','code':'pre_dispatch_retryable'}
        if prior.get('state')!='running':
            return key,None,prior.get('result') or {'status':'unknown','code':'media_result_unknown'}
        if time.monotonic()>=deadline:
            error=TenantLinearError('Attachment analysis is still running; no second analysis was started.',409)
            error.keep_request=True
            raise error
        time.sleep(.25)


def _finish(authority, ref, key, token, receipt, *, retryable=False):
    # Complete the already-admitted claim even when its reply has been
    # superseded. This grants no new dispatch/read/publication authority.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT token FROM saas_episode_media_flights WHERE
              tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s
              AND source_hash=%s AND token=%s::uuid AND state='running' FOR UPDATE''',(*key,token))
            if not cur.fetchone():return
            if retryable:
                cur.execute('''DELETE FROM saas_episode_media_flights WHERE tenant_id=%s::uuid
                  AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s AND token=%s::uuid''',(*key,token))
            else:
                # The frozen ref is the version validated before dispatch,
                # not a reconstruction from a possibly replaced attachment.
                saved=receipt
                if receipt.get('status') in {'unknown','error'} and any(receipt.get(k) for k in ('transcript','observation','observations')):
                    # Preserve known successful components even if a later
                    # component was interrupted or has an unknown paid result.
                    saved={**receipt,'status':'partial'}
                persist(cur,authority.tenant,[ref],[saved],processor='conversation_episode')
                cur.execute('''UPDATE saas_episode_media_flights SET state=%s,result=%s::jsonb
                  WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s
                  AND source_hash=%s AND token=%s::uuid AND state='running' ''',
                  ('unknown' if receipt.get('status')=='unknown' else 'completed',_json(receipt),*key,token))
        conn.commit()


def prepare(authority, rows):
    """Process every selected episode attachment before draft generation."""
    from tenant_knowledge_media import collect
    profile={'tenant_id':authority.tenant}
    manual=getattr(authority,'latest_request',False)
    selected=episode(rows,latest_request=manual)
    if manual:
        for row in selected:row['_manual_request_source']=True
    authority.source_refs=([reference(rows[-1],authority.channel,authority.lead)] if rows else [])
    if manual and selected and selected[-1] is not rows[-1]:authority.source_refs.append(reference(selected[-1],authority.channel,authority.lead))
    selected_ids={str(row.get('external_id')) for row in selected}
    for row in rows:
        if str(row.get('message_type') or '').lower() not in KINDS:continue
        # Normalize once: do not combine old legacy-body claims with receipts.
        row['_episode_channel']=authority.channel
        row['_copilot_context_body']=''
        row['_copilot_attachment_text']=''
        historical_context=row.pop('media_evidence_context',None)
        row['_copilot_media_notice']='[Historical attachment; not evidence for the current request]'
        if str(row.get('external_id')) not in selected_ids:
            # Previously saved exact-version evidence remains useful history;
            # it must not be confused with analysis of a new attachment.
            if historical_context:
                row['_copilot_attachment_text']=historical_context
                row['_historical_media_status']=(row.get('media_evidence') or {}).get('status')
                partial=row['_historical_media_status']=='partial'
                row['_copilot_media_notice']=('[Historical attachment; saved partial evidence; incomplete, do not infer omitted contents; not the latest attachment]'
                    if partial else '[Historical attachment; saved evidence from an earlier turn, not the latest attachment]')
            continue
        authority.check()
        ref=reference(row,authority.channel,authority.lead)
        if not any(source_hash(saved)==source_hash(ref) and saved['external_id']==ref['external_id'] for saved in authority.source_refs):authority.source_refs.append(ref)
        authority.validate(ref)
        prior=reusable(profile,ref)
        message={'text':'','ref':ref['id']}
        if prior:
            receipt={'id':ref['id'],'kind':ref['media_kind'],**apply_reusable(message,prior)}
        else:
            key,token,receipt=_flight(authority,ref)
            if token:
                item=authority.item(ref,message)
                authority.media_attempts=0
                try:
                    _,receipts=collect(profile,item,execution=authority)
                    receipt=receipts[0]
                    receipt['coverage']={'kind':ref['media_kind'],'complete':receipt.get('status')=='processed' and ref['media_kind']!='video'}
                    if ref['media_kind']=='video':receipt['coverage'].update(audio=receipt.get('audio_status','not_requested'),sampled_frames=receipt.get('visuals',[]))
                    retryable=(not authority.media_attempts and receipt.get('status') not in {'processed','partial'})
                    _finish(authority,ref,key,token,receipt,retryable=retryable)
                except Exception:
                    # Known pre-dispatch stops release the claim. Any paid
                    # ambiguity stays terminal unknown and is never reclaimed.
                    _finish(authority,ref,key,token,{'id':ref['id'],
                        'status':'unknown' if authority.media_attempts else 'unavailable',
                        'code':'media_result_unknown' if authority.media_attempts else 'pre_dispatch_retryable'},
                        retryable=not authority.media_attempts)
                    raise
            elif receipt:
                saved={'status':receipt.get('status'),'code':receipt.get('code'),
                       'coverage':receipt.get('coverage',{}),'result':receipt}
                apply_reusable(message,saved)
        status=receipt.get('status','unknown');code=receipt.get('code','media_result_unknown')
        row['_copilot_media_notice']='[Attachment '+status+': '+code+'; never infer unread contents]'
        row['_copilot_attachment_text']=message['text']
        row['_episode_receipt']=receipt
    return rows


class Execution:
    """Processing hooks for collect; all rights remain in the concrete adapter."""
    def item(self,ref,message):
        self.current_ref=ref
        config=self.config
        route={'channel_id':self.channel,'conversation_id':self.conversation,
               'allow_external_media':config.get('consent') is True or self.credential_source=='platform',
               'allow_paid_asr':config.get('audio_enabled') is True or self.credential_source=='platform',
               'allow_vision':True,'language':self.language}
        if self.credential_source!='platform' and config.get('photo_model'):
            route['campaign_photo_model']=config['photo_model']
        model=self.model
        if ref['media_kind']=='video':
            if config.get('video_enabled') is not True or not self.video_model or not getattr(self,'video_key',self.key):
                route['allow_external_media']=False
            route['video_model']=self.video_model
            from tenant_ai_spend_budget import limit_microusd
            if limit_microusd(self.tenant)<=0:route['allow_external_media']=False
        return {'tenant':self.tenant,'key':getattr(self,'video_key',self.key) if ref['media_kind']=='video' else self.key,'model':model,'config':config,
                'credential_source':self.credential_source,'route':route,
                'refs':[ref],'messages':[message]}

    def validate(self,ref):
        self.check()
        current=self.load({},ref['lead_id'],ref['external_id'])
        row=current['row']
        # Loader returns the persisted body as well as the raw media identity.
        if source_hash(reference(row,self.channel,self.lead))!=source_hash(ref):
            raise TenantLinearError('Attachment changed during analysis.',409)

    def check_sources(self):
        self.check()
        with _connect() as conn:
            with conn.cursor() as cur:revalidate_sources(cur,self.tenant,getattr(self,'source_refs',[]))

    def dispatch(self,model,operation,invoke):
        from tenant_media_admission import mark
        step='authority'
        call_key='episode:'+str(uuid.uuid4())
        from tenant_ai_spend_budget import ensure_schema, reserve_media, settle_media
        try:
            self.validate(self.current_ref)
            with _connect() as conn:
                ensure_schema(conn)
                with conn.cursor() as cur:
                    step='calls'
                    self.admit_call(cur)
                    step='budget'
                    reserve_media(cur,self.tenant,call_key,'episode_media',model,operation,1)
                conn.commit()
        except TenantLinearError as error:
            if operation=='asr':mark(error,step)
            raise
        attempted=False
        try:
            try:
                self.validate(self.current_ref)
            except TenantLinearError as error:
                if operation=='asr':mark(error,'authority')
                raise
            attempted=True
            self.media_attempts=getattr(self,'media_attempts',0)+1
            result=invoke()
            self.record_usage(result[1] if isinstance(result,tuple) and len(result)>1 else {})
            settle_media(self.tenant,call_key,operation=operation,attempted_units=1,reserved_units=1)
            return result
        except Exception as error:
            error.media_outcome_unknown=attempted
            settle_media(self.tenant,call_key,operation=operation,attempted_units=int(attempted),reserved_units=1,unknown=attempted)
            raise

    def observe_once(self,profile,item,ref,content,mime,origin,invoke,check_before_ai=None):
        from tenant_knowledge_media_cache import OBSERVATION_PROMPT
        text,usage=self.dispatch(item['model'],'vision_low',lambda:invoke(
            item['key'],item['model'],'media_observation',[],'',instructions=OBSERVATION_PROMPT,
            images=[{'content':content,'content_type':mime,'detail':'low'}],
            with_usage=True,max_output_tokens=600,request_timeout=25))
        if not isinstance(text,str) or not text.strip():raise ValueError('empty_observation')
        return text[:2400],usage,True

    def transcribe_once(self,profile,item,model,media,invoke,check_before_ai=None,*,return_diagnostics=False):
        response=self.dispatch(model,'asr',lambda:invoke(item['key'],model,*media,with_usage=True,request_timeout=45))
        if return_diagnostics:
            text,usage,diagnostics=response
            details={'speech_state':diagnostics.get('speech_state','uncertain'),
                     'speech_language':diagnostics.get('language',''),'speech_reason':diagnostics.get('reason','')}
            return text,usage,True,details
        text,usage=response
        return text,usage,True

    def admit_call(self,cur):
        if getattr(self,'prepaid_calls',0)>0:
            self.prepaid_calls-=1
            return
        # Same company-wide lock and daily count as manual and agent admission.
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+self.tenant,))
        cur.execute('''SELECT
          (SELECT COALESCE(sum(reserved_calls),0) FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid
            AND usage_day=(now() AT TIME ZONE 'UTC')::date) +
          (SELECT COALESCE(sum(reserved_calls),0) FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid
            AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'
            AND (state IN ('generating','completed','unknown') OR reserved_calls>1 OR generated_at IS NOT NULL)) AS used''',
          (self.tenant,self.tenant))
        if int((cur.fetchone() or {}).get('used') or 0)>=int(self.config.get('daily_limit') or 50):
            raise TenantLinearError('Company daily AI limit exhausted.',429)
        self.increment_calls(cur)

    def record_usage(self,usage):pass


class ManualExecution(Execution):
    latest_request=True
    def __init__(self,tenant,actor,command,config,version,key,model,language,rows):
        self.tenant,self.actor,self.command=tenant,actor,command
        self.channel,self.conversation,self.lead=command['channel_id'],command['conversation_id'],command['lead_id']
        self.config,self.version,self.key,self.model=config,str(version),key,model
        self.video_model=str(config.get('video_model') or '')
        self.language=language;self.credential_source='company'
        self.latest=str(rows[-1].get('external_id') or '') if rows else ''

    def check(self):
        from tenant_operator_access import access,assert_route
        from tenant_operator_copilot import _assigned_context,_agent_binding
        with _connect() as conn:
            with conn.cursor() as cur:
                tenant,actor,policy=access(cur,{'tenant_id':self.tenant,'telegram_id':self.actor},'dialogs')
                assert_route(cur,tenant,policy,'connect',self.conversation,self.channel,self.lead)
                if not policy.can_use_chat_ai() or _agent_binding(_assigned_context(cur,tenant,self.command))!=self.command['_agent_binding']:
                    raise TenantLinearError('Agent assignment or access changed.',409)
                cur.execute("SELECT metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(self.tenant,))
                current=cur.fetchone() or {};metadata=current.get('metadata') or {}
                if (str(current.get('updated_at'))!=self.version or metadata.get('enabled') is not True
                    or metadata.get('consent') is not True or metadata.get('copilot_enabled',True) is not True):
                    raise TenantLinearError('AI settings changed.',409)
                cur.execute('''SELECT external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid
                  AND kommo_lead_id=%s AND channel=%s ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 1''',
                  (self.tenant,self.lead,'connect:'+self.channel))
                if str((cur.fetchone() or {}).get('external_id') or '')!=self.latest:
                    raise TenantLinearError('Conversation changed. Request a fresh draft.',409)

    def load(self,profile,lead,external):
        from tenant_chat_attachment_store import load
        self.check()
        return load({'tenant_id':self.tenant,'telegram_id':self.actor},lead,external,
                    channel_id=self.channel,conversation_id=self.conversation)

    def increment_calls(self,cur):
        cur.execute('''SELECT COALESCE(sum(reserved_calls),0) AS used FROM saas_chat_ai_runs
          WHERE tenant_id=%s::uuid AND provider='connect' AND usage_day=(now() AT TIME ZONE 'UTC')::date''',(self.tenant,))
        if int((cur.fetchone() or {}).get('used') or 0)>=int(self.config.get('copilot_daily_limit') or 50):
            raise TenantLinearError('Company Copilot daily limit exhausted.',429)
        cur.execute('''UPDATE saas_chat_ai_runs SET reserved_calls=reserved_calls+1
          WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND status='sending' RETURNING request_id''',
          (self.tenant,self.command['request_id']))
        if not cur.fetchone():raise TenantLinearError('Manual request is no longer active.',409)


class AutoExecution(Execution):
    def __init__(self,job,reservation):
        self.job,self.reservation=job,reservation
        self.tenant,self.channel,self.conversation,self.lead=str(job['tenant_id']),str(job['channel_id']),str(job['conversation_id']),int(job['lead_id'])
        self.prepaid_calls=max(0,int(reservation.get('reserved_calls') or 1)-1)
        self.config=reservation.get('config') or {};self.model=reservation['model']
        self.key=reservation['api_key'];self.video_model=reservation.get('video_model') or ''
        self.video_key=reservation.get('video_api_key') or ''
        self.language=reservation.get('agent_language') or 'auto'
        self.credential_source=reservation.get('credential_source')

    def check(self):
        from tenant_connect_images import live
        from tenant_connect_voice import generation_budget_available
        if not live(self.job,self.reservation) or not generation_budget_available(self.job,self.reservation):
            raise TenantLinearError('Automatic job is no longer admitted.',409)

    def load(self,profile,lead,external):
        from tenant_connect_transport import connect_route
        self.check()
        with _connect() as conn:
            with conn.cursor() as cur:
                route=connect_route(cur,self.tenant,self.lead)
                if not route or str(route['channel_id'])!=self.channel or route['conversation_id']!=self.conversation:
                    raise TenantLinearError('Attachment route changed.',409)
                cur.execute('''SELECT external_id,body,raw,message_type,direction,channel FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND external_id=%s
                    AND direction='incoming' ''',(self.tenant,self.lead,'connect:'+self.channel,external))
                row=cur.fetchone()
                if not row or row.get('message_type') not in KINDS:raise TenantLinearError('Attachment not in this conversation.',404)
        return {'provider':'connect','route':route,'row':dict(row),'message_id':external.removeprefix('connect:')}

    def increment_calls(self,cur):
        cur.execute('''UPDATE saas_ai_agent_runs SET reserved_calls=reserved_calls+1
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating' RETURNING id''',
          (self.tenant,self.reservation['run_id']))
        if not cur.fetchone():raise TenantLinearError('Automatic run is no longer active.',409)

    def record_usage(self,usage):
        from tenant_ai_agent_run_store import record_token_usage
        total=self.reservation.setdefault('episode_media_usage',{})
        for key in ('input_tokens','output_tokens','total_tokens'):
            total[key]=int(total.get(key) or 0)+int((usage or {}).get(key) or 0)
        record_token_usage(self.job,self.reservation,total,cumulative=True)


def revalidate_sources(cur,tenant,refs):
    """Read-only publication fence for the frozen, previously authorized refs."""
    for ref in refs or []:
        cur.execute('''SELECT external_id,body,message_type,raw,channel FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND channel=%s AND kommo_lead_id=%s AND external_id=%s FOR SHARE''',
          (tenant,'connect:'+ref['channel_id'],ref['lead_id'],ref['external_id']))
        current=cur.fetchone()
        if not current or source_hash(reference(current,ref['channel_id'],ref['lead_id']))!=source_hash(ref):
            raise TenantLinearError('Attachment or message version changed. Request a fresh draft.',409)


def evidence_query(rows,source_id,*,max_chars=1000):
    """Bound KB lookup to current-episode text/evidence, with exact attribution."""
    selected=episode(rows)
    source_id=str(source_id or '').removeprefix('connect:')
    selected.sort(key=lambda row:str(row.get('external_id') or '').removeprefix('connect:')==source_id)
    parts=[];used=0
    for row in reversed(selected):
        kind=str(row.get('message_type') or 'text')
        # Voice body can be a legacy transcript for replaced/unread bytes.
        # Only exact-version prepared evidence may describe current speech.
        body='' if kind in {'voice','audio'} else str(row.get('_copilot_context_body',row.get('body') or '') or '').strip()
        receipt=row.get('_episode_receipt') or {}
        evidence=str(row.get('_copilot_attachment_text') or '').strip() if receipt.get('status') in {'processed','partial'} else ''
        content=(evidence if body and body in evidence else (body+'\n'+evidence).strip())
        if not content:continue
        # Put actual evidence first, preserving its ID/type in the same bound.
        attribution=' [source '+str(row.get('external_id') or '')+'; '+kind+']'
        room=max_chars-used
        if room<=len(attribution):break
        part=content[:min(700,room-len(attribution))]+attribution
        parts.append(part);used+=len(part)+1
    return '\n'.join(parts)


def context_lines(context,local_rows):
    """One normalized evidence representation, with latest/historical provenance."""
    own={str(r.get('external_id')):r for r in local_rows}
    lines=[];missing=0;used=0
    for row in reversed(context):
        source=str(row.get('external_id') or '')
        local=own.get(source,{})
        channel=str(row.get('channel') or '').removeprefix('connect:')
        if channel and local.get('_episode_channel') and channel!=local['_episode_channel']:local={}
        if not local:
            local=own.get(str(row.get('photo_message_id') or row.get('document_message_id') or ''),{})
        kind=str(row.get('message_type') or 'text')
        receipt=local.get('_episode_receipt') or {}
        body=str(row.get('body') or '')[:2000]
        if kind in KINDS:
            body=local.get('_copilot_media_notice') or '[Historical or other-channel attachment; not analyzed for this request]'
            body+='\n'+str(local.get('_copilot_attachment_text') or '')[:10000]
            if receipt.get('status')!='processed' and local.get('_historical_media_status')!='processed':missing+=1
        if used>=24000:break
        available=min(4000,24000-used)
        if len(body)>available:body=body[:max(0,available-70)]+'\n[Evidence shortened; do not infer omitted contents]'
        used+=len(body)
        lines.append({'speaker':'CLIENT' if row.get('direction')=='incoming' else 'SALES MANAGER',
                      'text':body,'at':str(row.get('happened_at') or ''),'source_id':source,
                      'media_kind':kind,'evidence_scope':'current_episode' if receipt else 'historical',
                      **({'sender':row.get('author_name',''),'source_channels':row['source_channels']} if 'source_channels' in row else {})})
    lines.reverse()
    return lines,missing


def extend_episode(cur,tenant,leads,channel,rows,history_limit,*,latest_request=False):
    """Keep prompt history bounded while loading all attachments in this episode.

    Only needed when the history page is full. This query cannot reach older
    dialogs or media before the last employee reply/ten-minute episode fence.
    """
    if len(rows)<history_limit:return rows
    if latest_request:
        cur.execute('''WITH latest_customer AS (
          SELECT COALESCE(happened_at,created_at) AS at,external_id FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s
            AND direction='incoming' AND raw->>'verified_employee_id' IS NULL
            AND raw->>'ai_authored' IS DISTINCT FROM 'true'
          ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 1
        ), previous_employee AS (
          SELECT COALESCE(happened_at,created_at) AS at,external_id FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s
            AND (direction='outgoing' OR raw->>'verified_employee_id' IS NOT NULL OR raw->>'ai_authored'='true')
            AND (COALESCE(happened_at,created_at),external_id)<(SELECT at,external_id FROM latest_customer)
          ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 1
        ) SELECT m.external_id,direction,body,message_type,COALESCE(happened_at,created_at) AS happened_at,
            created_at,channel,raw FROM saas_crm_messages m,latest_customer latest
          WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s
            AND direction='incoming' AND raw->>'verified_employee_id' IS NULL
            AND raw->>'ai_authored' IS DISTINCT FROM 'true'
            AND COALESCE(happened_at,created_at) BETWEEN latest.at-interval '10 minutes' AND latest.at
            AND (COALESCE(happened_at,created_at),m.external_id)<=(latest.at,latest.external_id)
            AND (NOT EXISTS(SELECT 1 FROM previous_employee) OR
              (COALESCE(happened_at,created_at),m.external_id)>(SELECT at,external_id FROM previous_employee))
          ORDER BY COALESCE(happened_at,created_at),m.external_id''',
          (tenant,leads,'connect:'+channel,tenant,leads,'connect:'+channel,tenant,leads,'connect:'+channel))
        by_id={str(row['external_id']):row for row in rows}
        for row in cur.fetchall():by_id.setdefault(str(row['external_id']),row)
        return sorted(by_id.values(),key=lambda row:(str(row.get('happened_at') or row.get('created_at') or ''),str(row['external_id'])))
    cur.execute('''SELECT external_id,direction,body,message_type,happened_at,created_at,channel,raw,
        NOT EXISTS(SELECT 1 FROM saas_connect_conversations c JOIN saas_ai_conversation_state cs
          ON cs.tenant_id=c.tenant_id AND cs.provider='connect' AND cs.conversation_id=c.conversation_id
            AND cs.channel_id=c.connect_channel_id::text
          WHERE c.tenant_id=m.tenant_id AND 'connect:'||c.connect_channel_id::text=m.channel
            AND c.lead_id=m.kommo_lead_id AND m.created_at<=cs.resumed_at) AS voice_eligible
      FROM saas_crm_messages m WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[])
        AND channel=%s AND direction='incoming'
        AND COALESCE(happened_at,created_at)>=(SELECT max(COALESCE(happened_at,created_at))-interval '10 minutes'
          FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s)
        AND COALESCE(happened_at,created_at)>COALESCE((SELECT max(COALESCE(happened_at,created_at))
          FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s
          AND (direction='outgoing' OR raw->>'verified_employee_id' IS NOT NULL)),'-infinity'::timestamptz)
      ORDER BY COALESCE(happened_at,created_at),external_id''',
      (tenant,leads,'connect:'+channel,tenant,leads,'connect:'+channel,tenant,leads,'connect:'+channel))
    by_id={str(row['external_id']):row for row in rows}
    for row in cur.fetchall():by_id.setdefault(str(row['external_id']),row)
    return sorted(by_id.values(),key=lambda row:(str(row.get('happened_at') or row.get('created_at') or ''),str(row['external_id'])))
