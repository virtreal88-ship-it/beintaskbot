"""Read-only coverage over the existing extractor inventory and receipts.

No fetching, scheduling, provider calls, consent inference, or persistent state.
Execution completion and media completeness are different facts.
"""
import re
from tenant_knowledge_candidates_policy import redact
from tenant_media_evidence import has_visual_result

SUPPORTED={'image','audio','voice','video','document'}
CONSENT={'external_media_permission_required','asr_authorization_required','vision_not_approved'}
STATES={'processed','partial','unavailable','unsupported','error','unknown','pending_processing','pending_consent'}


def project(refs,receipts,execution_status,*,include_content=False):
    execution_status=execution_status or 'unknown'
    known=isinstance(refs,list)
    refs=refs if known else []
    saved={str(r.get('id')):r for r in receipts or [] if isinstance(r,dict)}
    items=[];seen=set()
    for ref in refs:
        if not isinstance(ref,dict) or not ref.get('media_kind'):continue
        key=str(ref.get('id') or '')
        if not key or key in seen:continue
        seen.add(key);kind=str(ref['media_kind']);row=saved.get(key)
        # Never attach an outcome from another channel to this inventory item.
        if row and row.get('channel_id') and str(row['channel_id'])!=str(ref.get('channel_id')):row=None
        code=str((row or {}).get('code') or '')
        if not re.fullmatch('[a-z_]{1,80}',code):code='status_not_recorded'
        state=str((row or {}).get('status') or '')
        if row is None:
            if kind not in SUPPORTED:state,code='unsupported','unsupported_format'
            elif execution_status=='running':state,code='pending_processing','not_started'
            else:state,code='unknown','status_not_recorded'
        elif code in CONSENT:state='pending_consent'
        elif code in {'provider_result_unknown','media_result_unknown','provider_timeout'}:state='unknown'
        elif state not in STATES:state='unknown'
        if (execution_status in {'unknown','failed'} or execution_status=='completed' and code=='awaiting_model') and state=='pending_processing':state='unknown'
        coverage=(row or {}).get('coverage') or {}
        complete=state=='processed' and kind!='video' and coverage.get('complete') is not False
        # Inline input acceptance is not a reusable visual observation. The
        # metadata flag is derived only while reading an exact saved result.
        visual_available=has_visual_result(row) or (row or {}).get('has_visual_result') is True
        if kind=='image':complete=complete and visual_available
        if state=='processed' and not complete:state='partial'
        item={'id':key,'kind':kind,'status':state,'code':code,'complete':complete,
              'reused':(row or {}).get('reused') is True,'extraction_status':execution_status}
        if kind=='video':
            audio=(row or {}).get('audio_status') or coverage.get('audio') or 'unknown'
            item['audio_status']=audio if audio in {'processed','not_requested','unavailable_or_error'} else 'unknown'
            item['visual_status']='failed' if state=='error' else 'partial' if (row or {}).get('visuals') or state=='partial' else 'unknown'
        stage=(row or {}).get('failure_stage')
        if stage in {'source_validation','media_download','media_validation','video_decode','audio_transcription','visual_analysis','visual_input_prepare'}:item['failure_stage']=stage
        from tenant_chat_ai_provider import is_safe_failure_code
        failure=(row or {}).get('failure_code')
        if is_safe_failure_code(failure):item['failure_code']=failure
        if include_content and row:
            # Redaction/content OAuth gates belong to the existing caller. Never
            # project URLs, provider inputs, raw payloads, keys or binary bytes.
            result={k:redact(row[k][:4000]) for k in ('transcript','transcript_candidate','observation') if isinstance(row.get(k),str)}
            candidate_status=row.get('transcript_candidate_status')
            if candidate_status=='uncertain_unverified':result['transcript_candidate_status']=candidate_status
            observations=[{'text':redact(x['text'][:2400])} for x in row.get('observations',[]) if isinstance(x,dict) and isinstance(x.get('text'),str)]
            if observations:result['observations']=observations[:2]
            if result:item['result']=result
        items.append(item)
    counts={state:sum(x['status']==state for x in items) for state in sorted(STATES)}
    complete=known and all(x['complete'] for x in items)
    media_state='complete' if complete else 'partial' if any(x['complete'] or x['status']=='partial' for x in items) else 'unknown' if not known or counts['unknown'] else 'incomplete'
    status='partial' if execution_status=='completed' and not complete else execution_status
    return {'status':status,'execution_status':execution_status,'media_results':items,
            'media_coverage':{'inventory_known':known,'total':len(items),'complete':complete,
              'status':media_state,'counts':counts}}
