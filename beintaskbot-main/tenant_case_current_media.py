"""Owner-session byte verification for one exact existing case attachment."""
import hashlib
import threading
from tenant_linear_policy import TenantLinearError,identifier
from tenant_platform import _connect
import tenant_knowledge_candidates_store as cases
import tenant_case_source_inventory as inventory

_READ_SLOTS=threading.BoundedSemaphore(3)


def _authorized_snapshot(cur,profile,campaign_id,campaign_version,case_id,message_id,case_version):
    tenant,actor,policy=cases.authorize(cur,profile)
    if policy.profile.get('role')!='owner':raise TenantLinearError('Только владелец может проверять вложения.',403)
    campaign_id=identifier(campaign_id);case_id=identifier(case_id);message_id=identifier(message_id)
    if type(campaign_version) is not int or type(case_version) is not int or min(campaign_version,case_version)<1:
        raise TenantLinearError('Некорректная версия. Обновите состояние.',400)
    cur.execute('''SELECT c.version,c.approved_by,c.approved_at,c.progress,c.max_cost_microusd,
      g.status AS grant_status,g.cap_microusd,g.approved_at AS grant_approved_at,g.approved_by AS grant_approved_by
      FROM saas_knowledge_campaigns c JOIN saas_ai_media_grants g
      ON g.tenant_id=c.tenant_id AND g.id=c.id AND g.campaign_id=c.id
      WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid FOR SHARE OF c,g''',(tenant,campaign_id))
    row=cur.fetchone()
    if not row or str(row.get('approved_by'))!=str(actor) or str(row.get('grant_approved_by'))!=str(actor):
        raise TenantLinearError('Нет доступа к исходной обработке.',403)
    if (not row.get('approved_at') or not row.get('grant_approved_at') or row['grant_status'] not in {'active','completed'}
        or row.get('cap_microusd')!=10000000 or row.get('max_cost_microusd')!=10000000):
        raise TenantLinearError('Исходное разрешение недоступно.',409)
    if row['version']!=campaign_version:raise TenantLinearError('Обработка изменилась. Обновите состояние.',409)
    progress=row.get('progress') or {}
    if progress.get('mode')!='case_media_batch' or not any(str(p.get('case_id'))==case_id for p in progress.get('case_plans',[]) if isinstance(p,dict)):
        raise TenantLinearError('Кейс не входит в исходную обработку.',403)
    source=inventory.inspect(cur,profile,case_id,message_id,case_version,campaign_id,_internal=True)
    if not source['current_source_addressable']:
        raise TenantLinearError('Текущий источник нельзя адресовать через Connect.',409)
    return source


def _saved_byte_results(cur,tenant,campaign_id,source,digest):
    """Return only completed results attributed to these freshly fetched bytes."""
    from tenant_media_evidence import _safe_text
    cur.execute("""SELECT e.id::text AS extraction_id,e.media_results FROM saas_knowledge_campaign_fragments f
      JOIN saas_knowledge_extractions e ON e.tenant_id=f.tenant_id AND e.id=COALESCE(f.extraction_id,f.id)
      WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid
        AND f.route->>'case_id'=%s
        AND f.route->'message_ids' @> %s::jsonb AND f.route->>'case_source_sha256'=%s
        AND e.route->>'case_source_sha256'=%s AND e.status='completed' AND f.status IN ('completed','partial')
      ORDER BY f.updated_at DESC LIMIT 3""",
      (tenant,campaign_id,source['case_id'],__import__('json').dumps([source['message_id']]),digest,digest))
    rows=[(row,False) for row in cur.fetchall()]
    # Older extractions can also be reused when the result itself recorded
    # the exact bytes. Metadata-only source versions are never enough.
    import json
    cur.execute("""SELECT media_results FROM saas_knowledge_extractions
      WHERE tenant_id=%s::uuid AND status='completed' AND source_refs @> %s::jsonb
        AND media_results @> %s::jsonb ORDER BY completed_at DESC LIMIT 3""",
      (tenant,json.dumps([{'id':source['message_id'],'channel_id':source['channel_id']}]),
       json.dumps([{'id':source['message_id'],'sha256':digest}])))
    rows.extend((row,True) for row in cur.fetchall())
    saved=[];seen=set()
    for row,legacy in rows:
        for result in row.get('media_results') or []:
            if (not isinstance(result,dict) or str(result.get('id'))!=source['message_id']
                or result.get('status') not in {'processed','partial'} or (legacy and result.get('sha256')!=digest)):continue
            parts=[]
            for key in ('observation','transcript'):
                if isinstance(result.get(key),str) and result[key].strip():
                    value=_safe_text(result[key])
                    if key=='transcript' and source.get('kind')=='video' and result.get('audio_status')!='processed':
                        value='Непроверенная расшифровка (не подтверждённый факт): '+value
                    parts.append(value)
            if result.get('transcript_candidate_status')=='uncertain_unverified' and isinstance(result.get('transcript_candidate'),str) and result['transcript_candidate'].strip():
                parts.append('Непроверенная расшифровка (не подтверждённый факт): '+_safe_text(result['transcript_candidate']))
            for value in result.get('observations') or []:
                if isinstance(value,dict) and isinstance(value.get('text'),str) and value['text'].strip():parts.append(_safe_text(value['text']))
            if parts:
                entry={'status':result['status'],'text':'\n'.join(parts)[:4000]}
                entry['_extraction_id']=str(row.get('extraction_id') or '')
                if source.get('kind')=='video':
                    entry['visual_status']='partial' if (result.get('observation') or any(isinstance(x,dict) and x.get('text') for x in result.get('observations') or [])) else 'unknown'
                    audio=result.get('audio_status')
                    entry['audio_status']=audio if audio in {'processed','not_requested','unavailable_or_error','uncertain','no_speech_detected'} else 'unknown'
                    reason=result.get('audio_reason')
                    if reason in {'speech_confidence_unavailable','unsupported_video_asr_model','language_mismatch'}:entry['audio_reason']=reason
                    from tenant_chat_ai_provider import is_safe_failure_code
                    if is_safe_failure_code(result.get('audio_failure_code')):entry['audio_failure_code']=result['audio_failure_code']
                key=(entry['status'],entry['text'])
                if key not in seen:saved.append(entry);seen.add(key)
    cur.execute("SELECT to_regclass('public.saas_knowledge_media_cache') AS relation")
    if cur.fetchone().get('relation'):
        cur.execute("""SELECT transcript,metadata FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid
          AND state='completed' AND metadata->>'kind' IN ('vision','asr') AND metadata->>'sha256'=%s
          AND metadata->'owner_retry_source'->>'message_id'=%s
          AND metadata->'owner_retry_source'->>'channel_id'=%s ORDER BY created_at DESC LIMIT 3""",
          (tenant,digest,source['message_id'],source['channel_id']))
        for row in cur.fetchall():
            if isinstance(row.get('transcript'),str) and row['transcript'].strip():
                meta=row.get('metadata') if isinstance(row.get('metadata'),dict) else {}
                value=_safe_text(row['transcript'])[:4000]
                if source.get('kind')=='video' and meta.get('kind')=='asr' and meta.get('speech_state')!='confident':
                    value='Непроверенная расшифровка (не подтверждённый факт): '+value
                entry={'status':'partial' if source.get('kind')=='video' else 'processed','text':value}
                if source.get('kind')=='video':entry['visual_status']='unknown'
                if source.get('kind')=='video' and meta.get('kind')=='asr':
                    state=meta.get('speech_state');entry['audio_status']='processed' if state=='confident' else state if state in {'uncertain','no_speech_detected'} else 'unknown'
                key=(entry['status'],entry['text'])
                if key not in seen:saved.append(entry);seen.add(key)
        if source.get('kind')=='video' and any(item.get('audio_status')=='unknown' for item in saved):
            # An old partial receipt may omit the audio failure category. Read
            # only the unknown ASR claim for this exact extension and bytes;
            # an unrelated old attempt must never be attributed to this video.
            cur.execute("""SELECT s.metadata,COALESCE(f.extraction_id,f.id)::text AS extraction_id FROM saas_knowledge_media_cache s
              JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=s.tenant_id
                AND COALESCE(f.extraction_id,f.id)::text=s.metadata->>'owner_retry_extraction_id'
              WHERE s.tenant_id=%s::uuid AND f.campaign_id=%s::uuid AND f.route->>'case_id'=%s
                AND f.route->'message_ids' @> %s::jsonb AND f.route->>'case_source_sha256'=%s
                AND s.state='unknown' AND s.metadata->>'kind'='asr' AND s.metadata->>'sha256'=%s
                AND s.metadata->'owner_retry_source'->>'message_id'=%s
                AND s.metadata->'owner_retry_source'->>'channel_id'=%s
              ORDER BY s.created_at DESC LIMIT 1""",
              (tenant,campaign_id,source['case_id'],json.dumps([source['message_id']]),digest,digest,source['message_id'],source['channel_id']))
            from tenant_chat_ai_provider import is_safe_failure_code
            for row in cur.fetchall():
                metadata=row.get('metadata') if isinstance(row.get('metadata'),dict) else {}
                code=metadata.get('failure_code')
                if is_safe_failure_code(code):
                    for entry in saved:
                        if entry.get('audio_status')=='unknown' and entry.get('_extraction_id')==str(row.get('extraction_id')) and not entry.get('audio_failure_code'):entry['audio_failure_code']=code
    for entry in saved:entry.pop('_extraction_id',None)
    return saved[:3]


def verify_owner_source(profile,campaign_id,data):
    required={'expected_version','case_id','message_id','case_version'}
    if not isinstance(data,dict) or set(data)!=required:raise TenantLinearError('Некорректный источник.',400)
    args=(profile,campaign_id,data['expected_version'],data['case_id'],data['message_id'],data['case_version'])
    with _connect() as conn:
        with conn.cursor() as cur:before=_authorized_snapshot(cur,*args)
    if not _READ_SLOTS.acquire(blocking=False):
        error=TenantLinearError('Проверка файлов занята. Повторите позже.',429)
        error.code='owner_read_busy';error.retry_after=5;raise error
    try:
        payload,mime=inventory._fetch_current(profile,before)
    except TenantLinearError as error:
        stage=getattr(error,'source_stage','source_recheck')
        category={('rate_limit',429):'owner_read_rate_limit',('source_access',403):'source_access_denied',
          ('source_access',404):'source_route_unavailable',('connect_download',404):'connect_source_unavailable',
          ('connect_download',429):'connect_rate_limit',('connect_download',413):'connect_size_limit',
          ('connect_download',415):'connect_format_unsupported',('connect_download',502):'connect_fetch_failed'}.get((stage,error.status),'source_check_failed')
        if stage=='connect_download' and getattr(error,'code',None)=='connect_access_denied':category='connect_access_denied'
        safe=TenantLinearError('Не удалось проверить файл. Причина: '+category+'.',403 if category=='connect_access_denied' else error.status)
        safe.code=category;safe.retry_after=getattr(error,'retry_after',None);raise safe from None
    finally:
        _READ_SLOTS.release()
    if before['kind']=='image':
        from tenant_connect_images import signature
        if not signature(payload,mime):
            error=TenantLinearError('Не удалось проверить файл. Причина: connect_format_unsupported.',415)
            error.code='connect_format_unsupported';raise error
    digest=hashlib.sha256(payload).hexdigest()
    with _connect() as conn:
        with conn.cursor() as cur:
            after=_authorized_snapshot(cur,*args)
            from tenant_case_media_extension import _verified_byte_history
            history=_verified_byte_history(cur,str(profile['tenant_id']),after['message_id'],after['_source']['source_hash'],allow_incomplete=True)
            saved=_saved_byte_results(cur,str(profile['tenant_id']),str(campaign_id),after,digest)
            from tenant_case_uncertain_retry import eligible
            uncertain_retry=((history.get('reason') in {'prior_media_result_unknown','prior_attempt_or_unresolved_result','attempt_history_not_recorded'}
                or (after.get('interval_only') and history.get('state') in {'known_not_attempted','incomplete_history_eligible'}))
                and eligible(cur,str(profile['tenant_id']),after,digest))
    if before!=after:raise TenantLinearError('Кейс или источник изменился. Обновите состояние.',409)
    return {'case_id':before['case_id'],'message_id':before['message_id'],'case_version':before['case_version'],
      'provider':'connect','current_bytes_checked':True,'current_sha256':digest,
      'mime_type':mime,'content_signature_checked':before['kind']=='image','size_bytes':len(payload),
      'previous_bytes_verified':False,'continuation_supported':before['kind'] not in {'audio','voice'} or uncertain_retry,
      'attempt_reconciliation':history,'saved_byte_results':saved,'uncertain_retry_available':uncertain_retry and not saved,'provider_ai_calls':0}
